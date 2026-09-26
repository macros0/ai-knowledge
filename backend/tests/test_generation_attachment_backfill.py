"""Attachment tag backfill preserves source identity and retries projections."""
from types import SimpleNamespace

from docparser.blocks import Block

from app.config import Settings
from app.db.models import Document, DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.services.generation_store import begin_generation, mark_generation_ready, publish_generation
from app.services.okf_generator import OKFGenerator
from app.services.vector_store import concept_point_id
from scripts import backfill_attachment_tags as backfill


def _case(tmp_path, monkeypatch, *, source_id="root", chunk_index=0, tagged=False):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    generator = OKFGenerator(llm=SimpleNamespace())
    generator.settings = settings
    blocks = [Block("paragraph", "Text of attached document", meta={
        "source_id": source_id, "from_attachment": True,
    })]
    monkeypatch.setattr(backfill, "parse_document", lambda *_args, **_kwargs: blocks)
    with session_scope() as session:
        session.add(Document(id="attachment-tags", filename="doc.docx", status="done"))
        session.flush()
        generation = begin_generation(session, "attachment-tags")
        mark_generation_ready(session, "attachment-tags", generation.id)
        publish_generation(session, "attachment-tags", generation.id)
        generation_id = generation.id
        session.add(DocumentChunk(doc_id="attachment-tags", chunk_index=chunk_index,
                                  source_id=source_id, content="Text of attached document"))
        session.add(OkfConcept(doc_id="attachment-tags", slug="attached", title="Attached", type="concept",
                              content="Text of attached document", chunk_index=chunk_index, source_id=source_id,
                              tags=["attachment"] if tagged else []))
    settings.uploads_dir.mkdir(parents=True)
    (settings.uploads_dir / "attachment-tags.docx").write_bytes(b"source")
    calls = []
    vector = SimpleNamespace(ensure_collection=lambda: None,
                             set_document_tags_payload=lambda *args, **kwargs: calls.append((args, kwargs)))
    return settings, generator, vector, calls, generation_id


def test_attachment_backfill_addresses_active_generation_points(tmp_path, monkeypatch):
    settings, generator, vector, calls, generation_id = _case(tmp_path, monkeypatch)
    result = backfill.process_doc("attachment-tags", "doc.docx", settings, generator, vector)
    assert result["status"] == "tagged"
    assert calls[0][1]["concept_points"] == [
        (concept_point_id("attachment-tags", "attached", generation_id=generation_id), ["attachment"]),
    ]


def test_attachment_backfill_retries_projection_when_sql_tags_already_exist(tmp_path, monkeypatch):
    settings, generator, vector, calls, _generation_id = _case(tmp_path, monkeypatch, tagged=True)
    result = backfill.process_doc("attachment-tags", "doc.docx", settings, generator, vector)
    assert result["status"] == "matched_no_changes"
    assert len(calls) == 1


def test_attachment_backfill_preserves_noncontiguous_chunk_indices(tmp_path, monkeypatch):
    settings, generator, vector, calls, _generation_id = _case(
        tmp_path, monkeypatch, source_id="root/0", chunk_index=4,
    )
    result = backfill.process_doc("attachment-tags", "doc.docx", settings, generator, vector)
    assert result["status"] == "tagged"
    with session_scope() as session:
        concept = session.query(OkfConcept).filter_by(doc_id="attachment-tags").one()
        assert "attachment" in concept.tags


def test_attachment_backfill_rejects_equal_text_from_different_source(tmp_path, monkeypatch):
    settings, generator, vector, calls, _generation_id = _case(tmp_path, monkeypatch, source_id="root/0")
    monkeypatch.setattr(backfill, "parse_document", lambda *_args, **_kwargs: [
        Block("paragraph", "Text of attached document", meta={"source_id": "root/1", "from_attachment": True}),
    ])
    result = backfill.process_doc("attachment-tags", "doc.docx", settings, generator, vector)
    assert result["status"] == "skipped_parser_drift"
    assert not calls
    with session_scope() as session:
        assert session.query(OkfConcept).filter_by(doc_id="attachment-tags").one().tags == []


def test_attachment_backfill_qdrant_failure_can_be_retried(tmp_path, monkeypatch):
    settings, generator, vector, calls, _generation_id = _case(tmp_path, monkeypatch)
    original = vector.set_document_tags_payload

    def fail(*args, **kwargs):
        raise RuntimeError("unavailable")

    vector.set_document_tags_payload = fail
    failed = backfill.process_doc("attachment-tags", "doc.docx", settings, generator, vector)
    assert failed["status"] == "error"
    with session_scope() as session:
        assert session.query(OkfConcept).filter_by(doc_id="attachment-tags").one().tags == ["attachment"]
    vector.set_document_tags_payload = original
    retried = backfill.process_doc("attachment-tags", "doc.docx", settings, generator, vector)
    assert retried["status"] == "matched_no_changes"
    assert calls[0][1]["concept_points"][0][1] == ["attachment"]


def test_attachment_backfill_dry_run_does_not_write_tags_or_qdrant(tmp_path, monkeypatch):
    settings, generator, vector, calls, _generation_id = _case(tmp_path, monkeypatch)
    result = backfill.process_doc("attachment-tags", "doc.docx", settings, generator, vector, dry_run=True)
    assert result["status"] == "tagged" and result["dry_run"]
    assert not calls
    with session_scope() as session:
        assert session.query(OkfConcept).filter_by(doc_id="attachment-tags").one().tags == []


def test_attachment_backfill_rechecks_canonical_text_after_parse(tmp_path, monkeypatch):
    settings, generator, vector, calls, _generation_id = _case(tmp_path, monkeypatch)
    original = backfill.parse_document

    def parse_and_change(*args, **kwargs):
        blocks = original(*args, **kwargs)
        with session_scope() as session:
            session.query(DocumentChunk).filter_by(doc_id="attachment-tags").one().content = "New text"
        return blocks

    monkeypatch.setattr(backfill, "parse_document", parse_and_change)
    result = backfill.process_doc("attachment-tags", "doc.docx", settings, generator, vector)
    assert result["status"] == "skipped_parser_drift"
    assert not calls


def test_attachment_projection_uses_generation_published_after_tag_edit(tmp_path, monkeypatch):
    settings, generator, vector, calls, _generation_id = _case(tmp_path, monkeypatch)
    original = backfill._sync_concept_tags
    published = []

    def publish_and_sync(doc_id, store):
        with session_scope() as session:
            generation = begin_generation(session, doc_id)
            mark_generation_ready(session, doc_id, generation.id)
            publish_generation(session, doc_id, generation.id)
            published.append(generation.id)
            session.query(OkfConcept).filter_by(doc_id=doc_id).one().tags = ["current"]
        original(doc_id, store)

    monkeypatch.setattr(backfill, "_sync_concept_tags", publish_and_sync)
    result = backfill.process_doc("attachment-tags", "doc.docx", settings, generator, vector)
    assert result["status"] == "tagged"
    assert calls[0][1]["concept_points"] == [
        (concept_point_id("attachment-tags", "attached", generation_id=published[0]), ["current"]),
    ]


def test_attachment_chunks_keep_parent_child_parent_boundaries(tmp_path, monkeypatch):
    from app.services.source_chunking import chunk_blocks_by_source

    settings, generator, _vector, _calls, _generation_id = _case(tmp_path, monkeypatch)
    blocks = [
        Block("paragraph", "Parent before", meta={"source_id": "root"}),
        Block("paragraph", "Child text", meta={"source_id": "root/0", "from_attachment": True}),
        Block("paragraph", "Parent after", meta={"source_id": "root"}),
    ]
    actual = backfill._attachment_chunks(blocks, generator)
    assert [{"source_id": chunk["source_id"], "content": chunk["content"]} for chunk in actual] == (
        chunk_blocks_by_source(blocks, generator)
    )
    assert [chunk["share"] for chunk in actual] == [0.0, 1.0, 0.0]
