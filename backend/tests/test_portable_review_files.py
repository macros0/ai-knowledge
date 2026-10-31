"""Extracted originals in a pending attachment review are accepted durable bytes."""
import hashlib
import shutil

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.models import ArtifactBinding, DocumentProcessingReview
from app.db.session import session_scope
from app.services.registry import DocumentRegistry
from app.services.processing_scope import snapshot_from_review
from app.services.generation_store import begin_generation
from app.services.processing_scope_originals import retain_scope_originals
from docparser.attachment_policy import AttachmentInventory, AttachmentNode

DOC = "1234567890abcdef"
PAYLOAD = b"excluded original survives worker loss"


@pytest.fixture
def portable_review(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path / "worker-a", artifact_root=tmp_path / "durable",
                        artifact_uploads_enabled=True)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    files = tmp_path / "extracted"
    files.mkdir()
    (files / "one.xlsx").write_bytes(PAYLOAD)
    inventory = AttachmentInventory(source_file_hash="b" * 64, nodes=[
        AttachmentNode("a" * 64, "root", "one", "one.xlsx", "xlsx", assessment="heavy",
                       saved_path="one.xlsx", content_fingerprint=hashlib.sha256(PAYLOAD).hexdigest())])
    registry = DocumentRegistry()
    registry.create(DOC, "root.docx", "application/octet-stream", 4, file_hash="b" * 64,
                    processing_inventory=inventory, processing_artifacts_dir=files, processing_settings=settings)
    return settings, registry, inventory


def test_pending_review_original_is_retained_on_another_worker(portable_review, tmp_path):
    settings, _, _ = portable_review
    with session_scope() as session:
        review = session.get(DocumentProcessingReview, DOC)
        snapshot = snapshot_from_review(review)
        generation = begin_generation(session, DOC).id
    shutil.rmtree(settings.data_dir)
    settings.data_dir = tmp_path / "worker-b"
    sources, attachments = [], []
    retain_scope_originals(settings, DOC, generation, snapshot, sources, attachments)
    assert sources and attachments
    assert (settings.uploads_dir / DOC / sources[0]["saved_path"]).read_bytes() == PAYLOAD


def test_missing_review_blob_never_uses_an_old_local_extraction(portable_review):
    from app.db.models import ArtifactObject
    from app.services.generation_files import GenerationStorageError
    settings, _, _ = portable_review
    with session_scope() as session:
        review = session.get(DocumentProcessingReview, DOC)
        snapshot = snapshot_from_review(review)
        generation = begin_generation(session, DOC).id
        oid = session.scalar(select(ArtifactBinding.object_id).where(ArtifactBinding.logical_name.like("review/%")))
        assert oid is not None
        key = session.get(ArtifactObject, oid).key
    (settings.artifact_root / key).unlink()
    with pytest.raises(GenerationStorageError):
        retain_scope_originals(settings, DOC, generation, snapshot, [], [])
