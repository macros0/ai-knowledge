"""Dedup maintenance reads published SQL text and preserves mail identity."""
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.db.models import Document, DocumentChunk
from app.db.session import session_scope
from app.services.deduplication import content_hash
from app.services.generation_store import begin_generation, mark_generation_ready, publish_generation
from scripts.backfill_dedup import process_doc


def test_dedup_backfill_uses_canonical_text_without_flat_bundle_and_keeps_mail_fingerprint(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    doc_id = "dedup-published"
    with session_scope() as session:
        session.add(Document(id=doc_id, filename="mail.eml", status="paused", mail_fingerprint="mail-identity"))
        session.flush()
        generation = begin_generation(session, doc_id)
        mark_generation_ready(session, doc_id, generation.id)
        publish_generation(session, doc_id, generation.id)
        session.add(DocumentChunk(doc_id=doc_id, chunk_index=4, content="Published canonical text"))
    source = settings.uploads_dir / f"{doc_id}.eml"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original")
    pipeline = SimpleNamespace(ensure_chunks=lambda _doc_id: [{"index": 4}])
    result = process_doc(doc_id, "mail.eml", pipeline, settings)
    assert result["content"] is True, result
    with session_scope() as session:
        document = session.get(Document, doc_id)
        assert document.content_hash == content_hash("Published canonical text")
        assert document.mail_fingerprint == "mail-identity"


def test_dedup_backfill_does_not_use_stale_flat_bundle(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    doc_id = "dedup-legacy"
    with session_scope() as session:
        session.add(Document(id=doc_id, filename="doc.docx", status="done", mail_fingerprint="preserved"))
        session.add(DocumentChunk(doc_id=doc_id, chunk_index=0, content="Current SQL text"))
    chunks_dir = settings.okf_dir / doc_id / "chunks"
    chunks_dir.mkdir(parents=True)
    (chunks_dir / "chunk_00.md").write_text("Obsolete bundle text", encoding="utf-8")
    pipeline = SimpleNamespace(ensure_chunks=lambda _doc_id: [{"index": 0}])
    result = process_doc(doc_id, "doc.docx", pipeline, settings)
    assert result["error"] is None
    with session_scope() as session:
        document = session.get(Document, doc_id)
        assert document.content_hash == content_hash("Current SQL text")
        assert document.mail_fingerprint == "preserved"


@pytest.mark.parametrize("remove", [False, True])
def test_dedup_backfill_rechecks_deleted_or_missing_after_ensure_chunks(tmp_path, remove):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    doc_id = "dedup-deleted"
    with session_scope() as session:
        session.add(Document(id=doc_id, filename="doc.docx", status="done", content_hash="old"))
        session.add(DocumentChunk(doc_id=doc_id, chunk_index=0, content="Should not be hashed"))

    def ensure_chunks(_doc_id):
        with session_scope() as session:
            document = session.get(Document, doc_id)
            if remove:
                session.delete(document)
            else:
                document.deleted_at = datetime.now(timezone.utc)
        return []

    result = process_doc(doc_id, "doc.docx", SimpleNamespace(ensure_chunks=ensure_chunks), settings)
    assert result["skipped"] == ["missing_or_deleted"]
    assert not result["content"] and not result["file_hash"]
    with session_scope() as session:
        document = session.get(Document, doc_id)
        assert document is None if remove else document.content_hash == "old"


def test_dedup_backfill_rolls_back_file_hash_when_signature_write_fails(tmp_path, monkeypatch):
    from scripts import backfill_dedup

    settings = Settings(_env_file=None, data_dir=tmp_path)
    doc_id = "dedup-rollback"
    with session_scope() as session:
        session.add(Document(id=doc_id, filename="mail.eml", status="done", file_hash="old-file",
                             content_hash="old-content", mail_fingerprint="preserved"))
        session.add(DocumentChunk(doc_id=doc_id, chunk_index=0, content="Current text"))
    source = settings.uploads_dir / f"{doc_id}.eml"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original")

    def fail(*_args):
        raise RuntimeError("signature write failed")

    monkeypatch.setattr(backfill_dedup, "apply_document_signature", fail)
    result = process_doc(doc_id, "mail.eml", SimpleNamespace(ensure_chunks=lambda _doc_id: []), settings)
    assert result["error"] == "signature write failed"
    assert not result["file_hash"] and not result["content"]
    with session_scope() as session:
        document = session.get(Document, doc_id)
        assert (document.file_hash, document.content_hash, document.mail_fingerprint) == (
            "old-file", "old-content", "preserved",
        )
