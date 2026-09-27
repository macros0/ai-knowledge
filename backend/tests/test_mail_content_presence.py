"""S01/S02: mail metadata cannot turn an empty body or scan into LLM input."""
import importlib.util
import io
from email.message import EmailMessage
from pathlib import Path

import pytest
from openpyxl import Workbook
from PIL import Image

from app.config import Settings
from app.db.models import DocumentChunk, DocumentSource, OkfConcept
from app.db.session import session_scope
from app.models.schemas import Concept
from app.services.pipeline import Pipeline
from app.services.registry import DocumentRegistry


@pytest.fixture
def content_pipeline(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None, data_dir=tmp_path, embedding_provider="fake", embedding_dimensions=8,
        llm_model="test/mail", dedup_enabled=False, dev_detection_enabled=False,
        okf_write_bundles=True, mail_import_enabled=True,
    )
    for module in ("app.config", "app.services.pipeline", "app.services.staging", "app.services.embedder",
                   "app.services.llm_client", "app.services.okf_generator", "app.services.vector_store"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    registry = DocumentRegistry()
    monkeypatch.setattr("app.services.pipeline.get_registry", lambda: registry)
    pipeline = Pipeline()
    calls = []

    def generate(text, *_args, **_kwargs):
        calls.append(text)
        return [Concept(id=f"content-{len(calls)}", title="Content", content=text)]

    pipeline.okf_generator.generate_chunk = generate
    for method in ("ensure_collection", "delete_document", "delete_orphaned_points"):
        monkeypatch.setattr(pipeline.vector_store, method, lambda *_args, **_kwargs: None)
    from tests.mail_pipeline_boundary import use_memory_qdrant
    use_memory_qdrant(pipeline, monkeypatch)
    return pipeline, registry, calls


@pytest.mark.parametrize("format_name", ["eml", "msg"])
@pytest.mark.parametrize("attachment_kind", ["none", "scan", "xlsx"])
@pytest.mark.parametrize("resume", [False, True])
def test_subject_only_mail_does_not_make_empty_or_scanned_body_textual(content_pipeline, monkeypatch, format_name, attachment_kind, resume):
    pipeline, registry, calls = content_pipeline
    subject = "Subject metadata only " * 20  # Exceeds the legacy no_text_layer threshold.
    payload = None
    if attachment_kind != "none":
        output = io.BytesIO()
        if attachment_kind == "scan":
            Image.new("RGB", (160, 100), "white").save(output, format="PDF")
        else:
            workbook = Workbook()
            workbook.active.append(["Approval days", 18])
            workbook.save(output)
        payload = output.getvalue()
    name = "scan.pdf" if attachment_kind == "scan" else "data.xlsx"
    if format_name == "eml":
        message = EmailMessage()
        message["From"] = "author@example.test"
        message["Subject"] = subject
        message.set_content("")
        if payload:
            message.add_attachment(payload, maintype="application", subtype="octet-stream", filename=name)
        raw = message.as_bytes()
    else:
        spec = importlib.util.spec_from_file_location("presence_fixture", Path(__file__).parents[2] / "doc-parser/tests/mail_fixtures.py")
        fixtures = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixtures)
        raw = fixtures.unicode_msg(subject=subject, body="", attachments={name: payload} if payload else None)
    doc_id = "presence00000001"
    source = pipeline.settings.uploads_dir / f"{doc_id}.{format_name}"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(raw)
    registry.create(doc_id, source.name, "application/octet-stream", len(raw))
    if resume:
        from app.services.errors import VectorStoreError

        finalize = pipeline._finalize

        def unavailable(*_args, **_kwargs):
            raise VectorStoreError("Synthetic publication failure")

        monkeypatch.setattr(pipeline, "_finalize", unavailable)
        pipeline._process(doc_id, source, source.name, [], resume=False)
        assert registry.get(doc_id)["status"] == "paused"
        monkeypatch.setattr(pipeline, "_finalize", finalize)
    pipeline._process(doc_id, source, source.name, [], resume=resume)

    assert len(calls) == (1 if attachment_kind == "xlsx" else 0)
    document = registry.get(doc_id)
    assert document["status"] == "done"
    assert document["problem"] == (None if attachment_kind == "xlsx" else "no_text_layer")
    with session_scope() as session:
        chunks = session.query(DocumentChunk).filter_by(doc_id=doc_id).all()
        sources = session.query(DocumentSource).filter_by(doc_id=doc_id).all()
        concepts = session.query(OkfConcept).filter_by(doc_id=doc_id).all()
    assert sources[0].metadata_json["subject"] == subject.strip()
    assert any(subject.strip() in chunk.content for chunk in chunks)
    if payload:
        attachment = next(row for row in sources if row.source_id == "root/0")
        assert (pipeline.settings.uploads_dir / doc_id / attachment.saved_path).read_bytes() == payload
    if attachment_kind == "xlsx":
        assert "Approval days" in calls[0] and "18" in calls[0]
        assert {row.source_id for row in concepts} == {"root/0"}
    else:
        assert concepts == []


@pytest.mark.parametrize("embedded", [False, True])
def test_native_named_protected_msg_never_reaches_llm_or_canonical_body(content_pipeline, embedded):
    import struct
    from uuid import UUID

    import olefile

    pipeline, registry, calls = content_pipeline
    spec = importlib.util.spec_from_file_location("named_fixture", Path(__file__).parents[2] / "doc-parser/tests/mail_fixtures.py")
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    parent = fixtures.unicode_msg(body="VISIBLE PARENT" if embedded else "PRIVATE FALLBACK")
    raw = fixtures.nested_msg(parent, fixtures.unicode_msg(body="PRIVATE FALLBACK")) if embedded else parent
    with olefile.OleFileIO(io.BytesIO(raw)) as ole:
        streams = {"/".join(path): ole.openstream(path).read() for path in ole.listdir()}
    mapping = "__nameid_version1.0/__substg1.0_"
    name = "Content-Class".encode("utf-16-le")
    streams[mapping + "00020102"] = UUID("00020386-0000-0000-c000-000000000046").bytes_le
    streams[mapping + "00030102"] = struct.pack("<IHH", 0, 7, 0)
    streams[mapping + "00040102"] = struct.pack("<I", len(name)) + name + bytes((-len(name)) % 4)
    prefix = "__attach_version1.0_#00000000/__substg1.0_3701000D/" if embedded else ""
    streams[prefix + "__substg1.0_8000001F"] = "rpmsg.Message\0".encode("utf-16-le")
    raw = fixtures.compound_bytes(streams)
    doc_id = "namedprotected01"
    source = pipeline.settings.uploads_dir / f"{doc_id}.msg"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(raw)
    registry.create(doc_id, source.name, "application/octet-stream", len(raw))
    pipeline._process(doc_id, source, source.name, [], resume=False)
    assert len(calls) == (1 if embedded else 0)
    assert all("PRIVATE FALLBACK" not in text for text in calls)
    with session_scope() as session:
        chunks = session.query(DocumentChunk).filter_by(doc_id=doc_id).all()
        sources = session.query(DocumentSource).filter_by(doc_id=doc_id).all()
        concepts = session.query(OkfConcept).filter_by(doc_id=doc_id).all()
    assert all("PRIVATE FALLBACK" not in row.content for row in [*chunks, *concepts])
    protected_id = "root/0" if embedded else "root"
    protected = next(row for row in sources if row.source_id == protected_id)
    assert protected.metadata_json["content_class"] == "rpmsg.Message"
    assert protected.warnings == [{"code": "protected_mail", "source_id": protected_id}]
    assert source.read_bytes() == raw
