import hashlib
from email.message import EmailMessage
from email import policy

import pytest

from app.config import Settings
from app.db.models import DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.models.schemas import Concept
from app.services.export_okf import export_okf_bundle
from app.services.pipeline import Pipeline
from app.services.registry import DocumentRegistry
from app.services.source_store import replace_sources
from app.services.staging import StagingStore


def _pipeline(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        embedding_provider="fake",
        embedding_dimensions=8,
        llm_model="test/mail",
        dedup_enabled=False,
        dev_detection_enabled=False,
        okf_write_bundles=False,
        parser_supervisor_enabled=False,
    )
    for module in (
        "app.config", "app.services.pipeline", "app.services.staging",
        "app.services.embedder", "app.services.llm_client", "app.services.okf_generator",
        "app.services.vector_store",
    ):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    registry = DocumentRegistry()
    monkeypatch.setattr("app.services.pipeline.get_registry", lambda: registry)
    pipeline = Pipeline()
    for method in ("ensure_collection", "delete_document", "delete_orphaned_points"):
        monkeypatch.setattr(pipeline.vector_store, method, lambda *_args, **_kwargs: None)
    for method in ("index_concepts", "index_chunks"):
        monkeypatch.setattr(pipeline.vector_store, method, lambda *_args, **_kwargs: set())
    return pipeline, registry, settings


def test_resume_rejects_finished_checkpoint_from_different_source(tmp_path, monkeypatch):
    pipeline, registry, settings = _pipeline(tmp_path, monkeypatch)
    doc_id = "mailreview000001"
    message = EmailMessage()
    message["Subject"] = "New source"
    message.set_content("New text")
    path = settings.uploads_dir / f"{doc_id}.eml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(message))
    registry.create(doc_id, "review.eml", "message/rfc822", path.stat().st_size)
    staging = StagingStore(doc_id)
    staging.create(1, parser_version="old-parser", source_file_hash="0" * 64)
    staging.save_chunk_text(0, "OLD TEXT")
    staging.set_chunk_source(0, "root")
    staging.append_chunk(0, [Concept(id="old", title="Old", content="OLD CONTENT")])

    with pytest.raises(Exception, match="Версия извлечения изменилась"):
        pipeline._process(doc_id, path, "review.eml", [], resume=True)


def test_finalize_maps_concepts_to_numeric_chunk_indices_above_100(tmp_path, monkeypatch):
    pipeline, registry, _settings = _pipeline(tmp_path, monkeypatch)
    doc_id = "mailreview000002"
    registry.create(doc_id, "many.eml", "message/rfc822", 1)
    staging = StagingStore(doc_id)
    staging.create(102, parser_version="mail-sources-v1")
    for index in range(102):
        staging.save_chunk_text(index, f"Text {index}")
        staging.set_chunk_source(index, "root/0" if index % 2 else "root")
        staging.append_chunk(index, [Concept(id=f"c{index}", title=f"Concept {index}", content=f"Text {index}")])
    rows = [
        {"source_id": "root", "kind": "document"},
        {"source_id": "root/0", "parent_source_id": "root", "kind": "mail"},
    ]

    pipeline._finalize(doc_id, "many.eml", staging, [], [], source_rows=rows)

    with session_scope() as session:
        chunks = {row.chunk_index: row.source_id for row in session.query(DocumentChunk).filter_by(doc_id=doc_id)}
        concepts = session.query(OkfConcept).filter_by(doc_id=doc_id).all()
    assert all(concept.source_id == chunks[concept.chunk_index] for concept in concepts)


def test_supervisor_failure_keeps_previous_attachment_directory(tmp_path):
    from app.services.parser_supervisor import ParserWorkerError, parse_document_supervised

    source = tmp_path / "invalid.txt"
    source.write_text("invalid", encoding="utf-8")
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    previous = attachments / "previous-success.bin"
    previous.write_bytes(b"previous accepted artifact")

    with pytest.raises(ParserWorkerError):
        parse_document_supervised(
            source, source.name, attachments_dir=attachments, timeout_seconds=20, max_memory_mb=1024
        )

    assert previous.read_bytes() == b"previous accepted artifact"


def test_pipeline_keeps_published_attachments_when_supervised_retry_fails(tmp_path, monkeypatch):
    pipeline, registry, settings = _pipeline(tmp_path, monkeypatch)
    settings.parser_supervisor_enabled = True
    doc_id = "mailreview000004"
    source = settings.uploads_dir / f"{doc_id}.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("unsupported", encoding="utf-8")
    registry.create(doc_id, "retry.txt", "text/plain", source.stat().st_size)
    published = settings.uploads_dir / doc_id / "attachments" / "previous-success.bin"
    published.parent.mkdir(parents=True)
    published.write_bytes(b"published attachment")

    with pytest.raises(Exception):
        pipeline._process(doc_id, source, "retry.txt", [], resume=True)

    assert published.read_bytes() == b"published attachment"


def test_supervised_resume_rejection_keeps_published_attachments(tmp_path, monkeypatch):
    """A rejected checkpoint must not replace accepted attachment bytes."""
    pipeline, registry, settings = _pipeline(tmp_path, monkeypatch)
    settings.parser_supervisor_enabled = True
    doc_id = "mailreview000005"
    source = settings.uploads_dir / f"{doc_id}.eml"
    source.parent.mkdir(parents=True, exist_ok=True)
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = "sender@example.test"
    message.set_content("Current source")
    source.write_bytes(bytes(message))
    registry.create(doc_id, "resume.eml", "message/rfc822", source.stat().st_size)
    published = settings.uploads_dir / doc_id / "attachments" / "previous-success.bin"
    published.parent.mkdir(parents=True)
    published.write_bytes(b"previous accepted attachment")
    staging = StagingStore(doc_id)
    staging.create(1, parser_version="obsolete-parser")
    staging.save_chunk_text(0, "previous checkpoint")
    staging.set_chunk_source(0, "root")

    with pytest.raises(Exception, match="Версия извлечения изменилась"):
        pipeline._process(doc_id, source, "resume.eml", [], resume=True)

    assert published.read_bytes() == b"previous accepted attachment"


def test_resume_accepts_same_crlf_chunk_text(tmp_path, monkeypatch):
    """Checkpoint text written on Windows must match the same mail on resume."""
    pipeline, registry, settings = _pipeline(tmp_path, monkeypatch)
    pipeline.okf_generator.generate_chunk = lambda text, *_args, **_kwargs: [
        Concept(id="mail", title="Mail", content=text)
    ]
    doc_id = "mailreview000006"
    source = settings.uploads_dir / f"{doc_id}.eml"
    source.parent.mkdir(parents=True, exist_ok=True)
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = "sender@example.test"
    message.set_content("First line\r\nSecond line")
    source.write_bytes(bytes(message))
    registry.create(doc_id, "crlf.eml", "message/rfc822", source.stat().st_size)

    from docparser import ParseContext, parse_document
    from app.services.pipeline import _sha256_file
    from app.services.source_chunking import chunk_blocks_by_source

    context = ParseContext(source.name, mail_enabled=settings.mail_import_enabled)
    blocks = parse_document(source, source.name, attachments_dir=settings.uploads_dir / doc_id / "attachments", context=context)
    chunks = chunk_blocks_by_source(blocks, pipeline.okf_generator)
    assert chunks
    staging = StagingStore(doc_id)
    staging.create(
        len(chunks), parser_version=context.parser_version,
        source_file_hash=_sha256_file(source),
    )
    for index, item in enumerate(chunks):
        staging.save_chunk_text(index, item["content"])
        staging.set_chunk_source(index, item["source_id"])
    from app.services.pipeline import _same_checkpoint_chunk
    assert all(
        _same_checkpoint_chunk(staging.dir / f"chunk_{index:02d}.md", item["content"])
        for index, item in enumerate(chunks)
    )

    pipeline._process(doc_id, source, "crlf.eml", [], resume=True)
    assert registry.get(doc_id)["status"] == "done"


def test_pipeline_hashes_mail_evidence_against_canonical_crlf_chunk(tmp_path, monkeypatch):
    pipeline, registry, settings = _pipeline(tmp_path, monkeypatch)

    class QuoteLLM:
        def chat_json(self, *_args, **_kwargs):
            return [{
                "title": "Согласованный срок",
                "content": "Согласованный срок составляет 12 дней.",
                "source_quotes": ["Согласованный срок составляет 12 дней."],
            }]

    pipeline.okf_generator.llm = QuoteLLM()
    doc_id = "mailreview000007"
    source = settings.uploads_dir / f"{doc_id}.eml"
    source.parent.mkdir(parents=True, exist_ok=True)
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = "sender@example.test"
    message.set_content("Согласованный срок составляет 12 дней.")
    source.write_bytes(bytes(message))
    registry.create(doc_id, "evidence.eml", "message/rfc822", source.stat().st_size)

    pipeline._process(doc_id, source, "evidence.eml", [], resume=False)

    with session_scope() as session:
        chunk = session.query(DocumentChunk).filter_by(doc_id=doc_id, chunk_index=0).one()
        concept = session.query(OkfConcept).filter_by(doc_id=doc_id).one()
    assert concept.source_spans
    assert concept.source_spans[0]["chunk_hash"] == hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()


def test_database_export_preserves_source_tree_and_source_ids(tmp_path, monkeypatch):
    pipeline, registry, settings = _pipeline(tmp_path, monkeypatch)
    monkeypatch.setattr("app.services.export_okf.get_settings", lambda: settings)
    doc_id = "mailreview000003"
    registry.create(doc_id, "nested.eml", "message/rfc822", 1)
    with session_scope() as session:
        replace_sources(session, doc_id, [
            {"source_id": "root", "kind": "document"},
            {"source_id": "root/0", "parent_source_id": "root", "kind": "mail"},
        ])
        session.add(DocumentChunk(doc_id=doc_id, chunk_index=0, content="nested content", char_count=14, source_id="root/0"))
        session.add(OkfConcept(doc_id=doc_id, slug="nested", title="Nested", type="concept", content="nested content", chunk_index=0, source_id="root/0"))

    destination = tmp_path / "export"
    files = export_okf_bundle(doc_id, destination)

    assert "sources.json" in files
    assert '"source_id": "root/0"' in (destination / "sources.json").read_text(encoding="utf-8")
    assert '"source_id": "root/0"' in (destination / "chunks" / "manifest.json").read_text(encoding="utf-8")
