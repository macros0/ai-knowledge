"""Historical bundle migration must not overwrite generation-owned canonical data."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db.models import Document, DocumentChunk, DocumentSource, OkfAttachment, OkfConcept
from app.db.session import session_scope
from app.services.generation_store import begin_generation, mark_generation_ready, publish_generation
from scripts import backfill_db_store as migration


@pytest.mark.parametrize("state", ["active", "candidate", "sources", "trash"])
def test_legacy_force_cannot_replace_canonical_document(tmp_path, state):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    doc_id = "legacy-guard"
    with session_scope() as session:
        session.add(Document(id=doc_id, filename="doc.docx", status="done"))
        session.flush()
        if state in {"active", "candidate"}:
            generation = begin_generation(session, doc_id)
            if state == "active":
                mark_generation_ready(session, doc_id, generation.id)
                publish_generation(session, doc_id, generation.id)
        if state == "sources":
            session.add(DocumentSource(doc_id=doc_id, source_id="root", kind="document",
                                       ordinal=0, display_name="doc.docx"))
        if state == "trash":
            session.get(Document, doc_id).deleted_at = datetime.now(timezone.utc)
        session.add(DocumentChunk(doc_id=doc_id, chunk_index=4, content="Canonical"))
        session.add(OkfAttachment(doc_id=doc_id, name="current.bin", kind="other", saved_path="attachments/current.bin"))
        session.add(OkfConcept(doc_id=doc_id, slug="concept", title="Canonical", type="concept", content="Canonical"))
    bundle = settings.okf_dir / doc_id
    (bundle / "chunks").mkdir(parents=True)
    (bundle / "chunks" / "chunk_00.md").write_text("Obsolete", encoding="utf-8")
    (bundle / "attachments").mkdir()
    (bundle / "attachments" / "obsolete.bin").write_bytes(b"obsolete")
    (bundle / "concept.md").write_text("---\ncreated_at: 2001-01-01\n---\n\nObsolete", encoding="utf-8")

    assert migration.backfill_chunks(doc_id, "doc.docx", settings, force=True) == 0
    assert migration.backfill_attachments(doc_id, settings, force=True) == 0
    assert migration.backfill_generated_at(doc_id, settings, force=True) == 0
    with session_scope() as session:
        chunk = session.query(DocumentChunk).filter_by(doc_id=doc_id).one()
        assert (chunk.chunk_index, chunk.content) == (4, "Canonical")
        assert session.query(OkfAttachment).filter_by(doc_id=doc_id).one().name == "current.bin"
        assert session.query(OkfConcept).filter_by(doc_id=doc_id).one().generated_at is None
    assert not (settings.uploads_dir / doc_id / "attachments" / "obsolete.bin").exists()


def test_legacy_attachment_migration_rejects_conflicting_live_bytes(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with session_scope() as session:
        session.add(Document(id="legacy-files", filename="doc.docx", status="done"))
    old = settings.okf_dir / "legacy-files" / "attachments" / "file.bin"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"archive")
    current = settings.uploads_dir / "legacy-files" / "attachments" / "file.bin"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"current")
    with pytest.raises(ValueError, match="conflict|differs|different"):
        migration.backfill_attachments("legacy-files", settings)
    assert current.read_bytes() == b"current"
    with session_scope() as session:
        assert session.query(OkfAttachment).filter_by(doc_id="legacy-files").count() == 0


@pytest.mark.parametrize("mode", ["success", "dry_run", "failure"])
def test_legacy_reparse_is_private_and_preserves_source_tree(tmp_path, monkeypatch, mode):
    from docparser.blocks import Block

    settings = Settings(_env_file=None, data_dir=tmp_path)
    doc_id = "legacy-reparse"
    with session_scope() as session:
        session.add(Document(id=doc_id, filename="doc.docx", status="done"))
    settings.uploads_dir.mkdir(parents=True)
    (settings.uploads_dir / f"{doc_id}.docx").write_bytes(b"original")
    monkeypatch.setattr(migration, "_read_qdrant_chunks", lambda *_args: [])
    monkeypatch.setattr("app.services.vector_store.VectorStore", lambda: None)
    attempts = []

    def parse(_src, _filename, *, attachments_dir, context):
        attempt = Path(attachments_dir)
        attempts.append(attempt)
        assert attempt.is_relative_to(settings.staging_dir)
        assert not (settings.uploads_dir / doc_id).exists()
        child = context.add_child("root", "child.eml", "mail")
        saved = attempt / "child.eml"
        saved.write_bytes(b"child mail")
        if mode == "failure":
            raise RuntimeError("parser failed")
        return [
            Block("paragraph", "Parent text", meta={"source_id": "root"}),
            Block("attachment", "child.eml", meta={"source_id": child, "saved_path": str(saved),
                                                     "name": "child.eml", "kind": "mail", "parsed": True}),
            Block("paragraph", "Child text", meta={"source_id": child, "from_attachment": True}),
        ]

    monkeypatch.setattr("docparser.parse_document", parse)
    if mode == "failure":
        with pytest.raises(RuntimeError, match="parser failed"):
            migration.backfill_chunks(doc_id, "doc.docx", settings)
    else:
        assert migration.backfill_chunks(doc_id, "doc.docx", settings, dry_run=mode == "dry_run") == 2
    assert all(not attempt.exists() for attempt in attempts)
    with session_scope() as session:
        if mode == "success":
            assert {row.source_id for row in session.query(DocumentChunk).filter_by(doc_id=doc_id)} == {"root", "root/0"}
            source = session.query(DocumentSource).filter_by(doc_id=doc_id, source_id="root/0").one()
            attachment = session.query(OkfAttachment).filter_by(doc_id=doc_id).one()
            assert source.saved_path == attachment.saved_path == "attachments/child.eml"
            assert attachment.source_id == "root/0"
            assert (settings.uploads_dir / doc_id / source.saved_path).read_bytes() == b"child mail"
        else:
            assert session.query(DocumentChunk).filter_by(doc_id=doc_id).count() == 0
            assert session.query(DocumentSource).filter_by(doc_id=doc_id).count() == 0
            assert session.query(OkfAttachment).filter_by(doc_id=doc_id).count() == 0
            assert not (settings.uploads_dir / doc_id).exists()


def test_legacy_qdrant_fallback_excludes_generation_points():
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qm

    client = QdrantClient(":memory:")
    try:
        client.create_collection("legacy", vectors_config=qm.VectorParams(size=1, distance=qm.Distance.COSINE))
        client.upsert("legacy", [
            qm.PointStruct(id=1, vector=[1.0], payload={"doc_id": "doc", "point_type": "chunk", "chunk_index": 0,
                                                       "content": "Legacy"}),
            qm.PointStruct(id=2, vector=[1.0], payload={"doc_id": "doc", "point_type": "chunk", "chunk_index": 0,
                                                       "content": "Candidate", "generation_id": "candidate"}),
        ])
        assert migration._read_qdrant_chunks("doc", SimpleNamespace(client=client, collection="legacy")) == [(0, "Legacy")]
    finally:
        client.close()
