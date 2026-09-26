"""L10: real ENOSPC on a dedicated small filesystem, never on the host data disk.

Opt in with FULL_STORAGE_TEST_ROOT pointing to an isolated tmpfs <=16 MiB.
Ordinary runs skip these OS drills; injected Windows/Linux errors have separate tests.
"""
import errno
from email.message import EmailMessage
from importlib import import_module
import importlib.util
import multiprocessing
import os
from pathlib import Path
import shutil
import uuid

import pytest
from qdrant_client import QdrantClient

from app.config import Settings
from app.db.models import Document
from app.db.session import session_scope
from app.models.schemas import Concept
from app.services.pipeline import Pipeline
from app.services.registry import DocumentRegistry
from app.services.storage import StorageFullError
from tests.test_authz import login, make_client
from tests.test_generation_pipeline import DOC_ID, _published_snapshot


@pytest.fixture
def small_storage(tmp_path):
    configured = os.environ.get("FULL_STORAGE_TEST_ROOT")
    if os.name != "posix" or not configured:
        pytest.skip("isolated FULL_STORAGE_TEST_ROOT is required for real ENOSPC")
    root = Path(configured).resolve(strict=True)
    info = os.statvfs(root)
    assert root.stat().st_dev != tmp_path.stat().st_dev, "must use a separate filesystem"
    assert 0 < info.f_blocks * info.f_frsize <= 16 * 1024 * 1024, "refuse to fill a large filesystem"
    case = root / f"mail-disk-{uuid.uuid4().hex}"
    case.mkdir()
    try:
        yield case
    finally:
        assert case.resolve().parent == root
        shutil.rmtree(case)


def _fill_storage(case):
    filler = case / "exclusive-test-filler"
    with filler.open("xb", buffering=0) as target:
        while True:
            try:
                target.write(bytes(65536))
            except OSError as exc:
                assert exc.errno == errno.ENOSPC
                break
    assert os.statvfs(case).f_bavail == 0
    return filler


def _message(body, attachment=True, nested=False):
    message = EmailMessage()
    message["Subject"] = "Synthetic disk drill"
    message.set_content(body)
    if attachment:
        if nested:
            child = EmailMessage()
            child["Subject"] = "Nested disk drill"
            child.set_content(body)
            child.add_attachment(bytes(65536), maintype="application", subtype="octet-stream", filename="proof.bin")
            message.add_attachment(child, filename="child.eml")
        else:
            message.add_attachment(bytes(65536), maintype="application", subtype="octet-stream", filename="proof.bin")
    return message.as_bytes()


def test_actual_full_disk_upload_returns_507_without_ghost_document(small_storage, monkeypatch):
    from app.api import documents
    from app.services import pipeline as pipeline_module

    client = make_client(small_storage, monkeypatch, embedding_provider="fake", dedup_enabled=False,
                         mail_import_enabled=True)
    settings = documents.get_settings()
    monkeypatch.setattr(pipeline_module, "get_settings", lambda: settings)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    login(client, "demo.admin")
    filler = _fill_storage(small_storage)
    response = client.post("/api/documents", files={"file": ("disk.eml", _message("Fact: 23 days.", False), "message/rfc822")})
    assert response.status_code == 507, response.text
    assert response.json()["code"] == "storage_full"
    assert list(settings.uploads_dir.iterdir()) == []
    with session_scope() as session:
        assert session.query(Document).count() == 0
    assert client.get("/health").status_code == 200
    filler.unlink()


@pytest.mark.parametrize("shape", ["eml", "nested-eml", "msg"])
def test_actual_full_disk_spawn_error_preserves_storage_classification(small_storage, tmp_path, shape):
    from app.services.parser_supervisor import parse_document_supervised

    body = "The verified inspection period is 23 days."
    source = tmp_path / ("disk.msg" if shape == "msg" else "disk.eml")
    if shape == "msg":
        spec = importlib.util.spec_from_file_location("disk_msg_fixture", Path(__file__).parents[2] / "doc-parser/tests/mail_fixtures.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        source.write_bytes(fixture.unicode_msg(subject="Disk drill", body=body, attachments={"proof.bin": bytes(65536)}))
    else:
        source.write_bytes(_message(body, nested=shape == "nested-eml"))
    destination = small_storage / "attempt"
    filler = _fill_storage(small_storage)
    before = {child.pid for child in multiprocessing.active_children()}
    with pytest.raises(StorageFullError):
        parse_document_supervised(source, source.name, attachments_dir=destination,
                                  timeout_seconds=20, max_memory_mb=1024)
    assert not destination.exists()
    assert {child.pid for child in multiprocessing.active_children()} == before
    filler.unlink()
    # The failed child must also release its admission slot.
    result = parse_document_supervised(source, source.name, attachments_dir=destination,
                                      timeout_seconds=20, max_memory_mb=1024)
    assert any("23 days" in (block.text or "") for block in result.blocks)


def test_actual_full_disk_regeneration_keeps_published_generation_and_resumes(small_storage, monkeypatch):
    settings = Settings(_env_file=None, data_dir=small_storage, embedding_provider="fake", embedding_dimensions=8,
                        llm_model="test/mail", dedup_enabled=False, dev_detection_enabled=False,
                        okf_write_bundles=True, mail_import_enabled=True, parser_supervisor_enabled=True)
    names = ("app.config", "app.services.pipeline", "app.services.staging", "app.services.embedder",
             "app.services.llm_client", "app.services.okf_generator", "app.services.vector_store",
             "app.api.documents", "app.services.export_okf", "app.services.deduplication", "app.services.dev_detector")
    modules = [import_module(name) for name in names]
    for module in modules:
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    registry = DocumentRegistry()
    monkeypatch.setattr("app.services.pipeline.get_registry", lambda: registry)
    pipeline = Pipeline()
    pipeline.vector_store.client.close()
    pipeline.vector_store.client = QdrantClient(location=":memory:")
    pipeline.okf_generator.generate_chunk = lambda text, *_a, **_k: [Concept(id="decision", title="Decision", content=text)]
    source = settings.uploads_dir / f"{DOC_ID}.eml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(_message("Original approved inspection period: 23 days. " * 8))
    registry.create(DOC_ID, "disk.eml", "message/rfc822", source.stat().st_size)
    try:
        pipeline._process(DOC_ID, source, "disk.eml", [], resume=False)
        assert registry.get(DOC_ID)["status"] == "done"
        before = _published_snapshot(pipeline)
        before_points, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
        before_ids = {str(point.id) for point in before_points}
        source.write_bytes(_message("New approved inspection period: 24 days. " * 8))
        filler = _fill_storage(small_storage)
        pipeline.regenerate(DOC_ID)
        result = pipeline.wait_for(DOC_ID, timeout=30)
        assert result["status"] == "paused", result
        assert result["error_code"] == "storage_full", result
        assert _published_snapshot(pipeline) == before
        points, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
        assert {str(point.id) for point in points} == before_ids
        filler.unlink()
        pipeline.resume(DOC_ID)
        result = pipeline.wait_for(DOC_ID, timeout=30)
        assert result["status"] == "done", result
        after = _published_snapshot(pipeline)
        assert after[0] != before[0]
        assert any("24 days" in content for _slug, content in after[1])
    finally:
        pipeline._executor.shutdown(wait=True, cancel_futures=True)
        pipeline.vector_store.client.close()
