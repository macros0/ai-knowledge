"""Comment maintenance uses canonical provenance and unique source-owned anchors."""
from datetime import datetime, timezone

import pytest

from app.db.models import Document, DocumentChunk, DocumentGeneration, DocumentGenerationState, DocumentSource, OkfConcept
from app.db.session import session_scope
from app.services.bundle import load_bundle
from app.services.chunk_store import replace_chunks
from app.services.concept_store import replace_concepts
from app.services.okf_generator import OKFGenerator
from app.services.registry import get_registry
from app.services.source_evidence import span_from_offsets
from app.services.canonical_repair import canonical_rows_digest
from app.services.generation_cleanup import cleanup_document_generations
from app.services.generation_files import active_bundle_path
from tests.test_backfill_comment_concepts import DOC_ID, QUESTION, _fake_services, _setup_doc, backfill


def _canonical(tmp_path):
    settings, bundle = _setup_doc(tmp_path)
    text = (bundle / "chunks" / "chunk_00.md").read_text(encoding="utf-8")
    with session_scope() as session:
        replace_concepts(session, DOC_ID, load_bundle(bundle))
        session.add(DocumentSource(doc_id=DOC_ID, source_id="root", ordinal=0, kind="document", display_name="doc.docx"))
        session.flush()
        replace_chunks(session, DOC_ID, [{"chunk_index": 0, "source_id": "root", "content": text}])
        concept = session.query(OkfConcept).filter_by(doc_id=DOC_ID, slug="obychnoy-koncept").one()
        concept.source_id = "root"
        concept.source_spans = [span_from_offsets(text, 0, len("Текст чанка.")).model_dump()]
        concept.model_id = "original-model"
        concept.prompt_version = "original-prompt"
        concept.generated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    return settings, bundle


def test_comment_repair_preserves_unrelated_sql_provenance(tmp_path, monkeypatch):
    settings, _bundle = _canonical(tmp_path)
    embedder, store, _qdrant = _fake_services(settings, monkeypatch)
    result = backfill.process_doc(DOC_ID, "doc.docx", settings, OKFGenerator(), embedder, store)
    assert result["error"] is None and result["skipped"] == []
    with session_scope() as session:
        concept = session.query(OkfConcept).filter_by(doc_id=DOC_ID, slug="obychnoy-koncept").one()
        assert concept.source_id == "root" and concept.source_spans
        assert concept.model_id == "original-model" and concept.prompt_version == "original-prompt"
        assert concept.generated_at.date().isoformat() == "2026-01-02"
        assert session.get(DocumentGenerationState, DOC_ID).active_generation_id


def test_global_review_tag_does_not_delete_unrelated_concept(tmp_path, monkeypatch):
    settings, bundle = _canonical(tmp_path)
    get_registry().update(DOC_ID, tags=["review"])
    path = bundle / "obychnoy-koncept.md"
    text = path.read_text(encoding="utf-8").replace("- business", "- business\n- review")
    path.write_text(text, encoding="utf-8")
    embedder, store, _qdrant = _fake_services(settings, monkeypatch)
    result = backfill.process_doc(DOC_ID, "doc.docx", settings, OKFGenerator(), embedder, store)
    assert result["error"] is None
    with session_scope() as session:
        assert session.query(OkfConcept).filter_by(doc_id=DOC_ID, slug="obychnoy-koncept").count() == 1


def test_ambiguous_question_anchor_does_not_publish_any_changes(tmp_path, monkeypatch):
    settings, _bundle = _canonical(tmp_path)
    with session_scope() as session:
        chunk = session.query(DocumentChunk).filter_by(doc_id=DOC_ID).one()
        chunk.content += "\n\n" + QUESTION
    embedder, store, qdrant = _fake_services(settings, monkeypatch)
    result = backfill.process_doc(DOC_ID, "doc.docx", settings, OKFGenerator(), embedder, store)
    assert result["error"] is not None
    assert qdrant.upserted == []
    with session_scope() as session:
        assert session.get(DocumentGenerationState, DOC_ID) is None
        assert session.query(OkfConcept).filter_by(doc_id=DOC_ID, slug="kommentariy-retsenzenta-llm").count() == 1


@pytest.mark.parametrize("root_has_anchor", [True, False])
def test_comment_anchor_cannot_cross_source_boundary(tmp_path, monkeypatch, root_has_anchor):
    settings, _bundle = _canonical(tmp_path)
    with session_scope() as session:
        root = session.query(DocumentChunk).filter_by(doc_id=DOC_ID, source_id="root").one()
        session.add(DocumentSource(doc_id=DOC_ID, source_id="root/0", parent_source_id="root",
                                   ordinal=1, kind="document", display_name="nested.docx"))
        session.flush()
        session.add(DocumentChunk(doc_id=DOC_ID, chunk_index=7, source_id="root/0", content=root.content))
        if not root_has_anchor:
            root.content = "Корневой документ без комментария."
    embedder, store, qdrant = _fake_services(settings, monkeypatch)
    result = backfill.process_doc(DOC_ID, "doc.docx", settings, OKFGenerator(), embedder, store)
    if not root_has_anchor:
        assert result["error"] and "canonical source" in result["error"]
        assert qdrant.upserted == []
        return
    assert result["error"] is None
    with session_scope() as session:
        comment = session.query(OkfConcept).filter_by(doc_id=DOC_ID).filter(
            OkfConcept.slug.like("zamechanie-retsenzenta%")
        ).one()
        assert comment.source_id == "root" and comment.chunk_index == 0
        chunk = session.query(DocumentChunk).filter_by(doc_id=DOC_ID, chunk_index=0).one()
        assert len(comment.source_spans) == 1
        span = comment.source_spans[0]
        assert chunk.content[span["start"]:span["end"]] == QUESTION


def test_comment_repair_index_failure_preserves_canonical_rows_and_bundle(tmp_path, monkeypatch):
    settings, bundle = _canonical(tmp_path)
    before_files = {path.relative_to(bundle): path.read_bytes() for path in bundle.rglob("*") if path.is_file()}
    with session_scope() as session:
        before_rows = canonical_rows_digest(session, DOC_ID)
    embedder, store, _qdrant = _fake_services(settings, monkeypatch)

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected chunk index failure")

    monkeypatch.setattr(store, "index_chunks", fail)
    result = backfill.process_doc(DOC_ID, "doc.docx", settings, OKFGenerator(), embedder, store)
    assert result["error"] == "injected chunk index failure"
    assert active_bundle_path(settings, DOC_ID) == bundle
    assert {path.relative_to(bundle): path.read_bytes() for path in bundle.rglob("*")
            if path.is_file() and "generations" not in path.relative_to(bundle).parts} == before_files
    with session_scope() as session:
        assert canonical_rows_digest(session, DOC_ID) == before_rows
        state = session.get(DocumentGenerationState, DOC_ID)
        assert state.active_generation_id is None and state.candidate_generation_id is None
        assert session.query(DocumentGeneration).filter_by(doc_id=DOC_ID).one().phase == "abandoned"
    cleanup_document_generations(settings, store, DOC_ID)
    assert {path.relative_to(bundle): path.read_bytes() for path in bundle.rglob("*") if path.is_file()} == before_files


def test_comment_repair_rejects_changed_original_before_indexing(tmp_path, monkeypatch):
    settings, _bundle = _canonical(tmp_path)
    with session_scope() as session:
        session.get(Document, DOC_ID).file_hash = "0" * 64
    embedder, store, qdrant = _fake_services(settings, monkeypatch)
    result = backfill.process_doc(DOC_ID, "doc.docx", settings, OKFGenerator(), embedder, store)
    assert "Source file changed" in result["error"]
    assert qdrant.upserted == [] and embedder.calls == []
    with session_scope() as session:
        assert session.get(DocumentGenerationState, DOC_ID) is None


def test_comment_cli_reports_failure_via_exit_code(monkeypatch):
    monkeypatch.setattr(backfill.sys, "argv", ["backfill_comment_concepts.py"])
    monkeypatch.setattr(backfill, "get_settings", lambda: None)
    monkeypatch.setattr(backfill, "OKFGenerator", lambda: None)
    monkeypatch.setattr("app.services.embedder.Embedder", lambda: None)
    monkeypatch.setattr("app.services.vector_store.VectorStore", lambda: None)
    monkeypatch.setattr(backfill, "_iter_docs", lambda _doc_id: [(DOC_ID, "doc.docx", None)])
    monkeypatch.setattr(backfill, "process_doc", lambda *_args, **_kwargs: {"error": "index failed"})
    with pytest.raises(SystemExit) as caught:
        backfill.main()
    assert caught.value.code == 1


def test_comment_cli_dry_run_does_not_initialize_external_clients(tmp_path, monkeypatch):
    settings, _bundle = _canonical(tmp_path)
    monkeypatch.setattr(backfill.sys, "argv", ["backfill_comment_concepts.py", "--dry-run", "--doc-id", DOC_ID])
    monkeypatch.setattr(backfill, "get_settings", lambda: settings)

    def unexpected():
        pytest.fail("dry-run must not initialize embedding or Qdrant clients")

    monkeypatch.setattr("app.services.embedder.Embedder", unexpected)
    monkeypatch.setattr("app.services.vector_store.VectorStore", unexpected)
    backfill.main()
    with session_scope() as session:
        assert session.get(DocumentGenerationState, DOC_ID) is None
