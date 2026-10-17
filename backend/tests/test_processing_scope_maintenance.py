from types import SimpleNamespace
import pytest

from tests.test_generation_pipeline import pipeline_env as shared_pipeline_env, DOC_ID  # noqa:F401

pipeline_env = shared_pipeline_env
from tests.test_generation_processing_scope import (
    test_scoped_generation_excludes_raw_and_programmatic_content as publish_scoped,
)
from app.db.models import DocumentGenerationState, DocumentGeneration
from app.db.session import session_scope


def test_bulk_cannot_bypass_pending_scope_review():
    from app.services.bulk_generation import generation_skip_code

    doc = {"status": "done", "processing_review": {"state": "draft", "requires_action": True}}
    assert generation_skip_code(doc, resume=False) == "processing_scope_review_required"


def test_waiting_scope_is_terminal_for_worker_wait():
    from app.services.pipeline import Pipeline

    pipeline = Pipeline.__new__(Pipeline)
    pipeline.registry = SimpleNamespace(get=lambda _: {"status": "awaiting_attachments"})
    assert pipeline.wait_for("x", timeout=0)["status"] == "awaiting_attachments"


def test_canonical_reindex_preserves_scope_and_excluded_content(pipeline_env):
    from app.services.canonical_reindex import reindex_published_document

    pipeline, _, _ = pipeline_env
    publish_scoped(pipeline_env)
    with session_scope() as session:
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        before = session.get(DocumentGeneration, active).processing_scope_json
    result = reindex_published_document(
        DOC_ID, pipeline.settings, pipeline.vector_store, pipeline.embedder
    )
    assert result["docs"] == 1
    with session_scope() as session:
        assert session.get(DocumentGeneration, active).processing_scope_json == before


def test_regenerate_reuses_published_selection_even_when_feature_flag_off(
    pipeline_env, monkeypatch
):
    pipeline, _, _ = pipeline_env
    publish_scoped(pipeline_env)
    with session_scope() as session:
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        expected = session.get(DocumentGeneration, active).processing_scope_json
    pipeline.settings.attachment_review_enabled = False
    calls = []
    monkeypatch.setattr(pipeline, "_start", lambda *args, **kwargs: calls.append(kwargs))
    pipeline.regenerate(DOC_ID)
    assert calls[0]["processing_scope"] == expected


def test_unadapted_canonical_repair_fails_closed_for_scoped_generation(pipeline_env):
    from app.services.canonical_repair import repair_published_document

    pipeline, _, _ = pipeline_env
    publish_scoped(pipeline_env)
    result = repair_published_document(
        DOC_ID,
        pipeline.settings,
        pipeline.okf_generator,
        pipeline.embedder,
        pipeline.vector_store,
        lambda _: (_ for _ in ()).throw(AssertionError("must skip before transform")),
    )
    assert result["reason"] == "processing_scope_not_supported"


def test_new_resume_candidate_inherits_active_scope(pipeline_env):
    from app.services.generation_store import prepare_generation_attempt

    publish_scoped(pipeline_env)
    with session_scope() as session:
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        expected = session.get(DocumentGeneration, active).processing_scope_json
        candidate = prepare_generation_attempt(session, DOC_ID, resume=True)
        assert candidate.processing_scope_json == expected
        assert candidate.processing_scope_json is not expected


def test_scoped_admission_is_resumable_before_executor_submit(pipeline_env):
    import pytest

    pipeline, path, _ = pipeline_env
    publish_scoped(pipeline_env)
    with session_scope() as session:
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        scope = session.get(DocumentGeneration, active).processing_scope_json

    class FailedExecutor:
        def submit(self, *args, **kwargs):
            with session_scope() as session:
                from app.db.models import Document

                assert session.get(Document, DOC_ID).status == "queued"
                state = session.get(DocumentGenerationState, DOC_ID)
                assert (
                    session.get(
                        DocumentGeneration, state.candidate_generation_id
                    ).processing_scope_json
                    == scope
                )
            raise RuntimeError("submit failure")

    pipeline._executor = FailedExecutor()
    with pytest.raises(RuntimeError, match="submit failure"):
        pipeline._start(DOC_ID, str(path), "decision.eml", [], False, processing_scope=scope)
    assert pipeline.registry.get(DOC_ID)["status"] == "done"


def test_legacy_reparse_backfills_refuse_scoped_documents(pipeline_env, monkeypatch):
    from scripts import backfill_attachment_tags, backfill_comment_concepts

    pipeline, _, _ = pipeline_env
    publish_scoped(pipeline_env)

    def forbidden(*args, **kwargs):
        raise AssertionError("Scoped source must not be reparsed by a legacy backfill")

    monkeypatch.setattr(backfill_attachment_tags, "parse_document", forbidden)
    monkeypatch.setattr(backfill_comment_concepts, "_read_comment_concepts", forbidden)
    result = backfill_attachment_tags.process_doc(
        DOC_ID, "decision.eml", pipeline.settings, pipeline.okf_generator, pipeline.vector_store
    )
    assert result["status"] == "skipped_processing_scope_not_supported"
    result = backfill_comment_concepts.process_doc(
        DOC_ID,
        "decision.docx",
        pipeline.settings,
        pipeline.okf_generator,
        pipeline.embedder,
        pipeline.vector_store,
    )
    assert result["skipped"] == ["processing_scope_not_supported"]


@pytest.mark.parametrize("review_state", ["draft", "submitted"])
@pytest.mark.parametrize("stale_active_scope", [False, True])
def test_unscoped_start_cannot_bypass_pending_or_confirm_admission_window(
    pipeline_env, review_state, stale_active_scope
):
    from app.services.processing_scope import create_review
    from app.services.errors import DomainError
    from docparser.attachment_policy import AttachmentInventory
    from hashlib import sha256

    pipeline, path, _ = pipeline_env
    with session_scope() as session:
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        row = create_review(
            session,
            DOC_ID,
            AttachmentInventory(source_file_hash=sha256(path.read_bytes()).hexdigest()),
            base_generation_id=active,
            actor="editor",
        )
        row.state = review_state

    class ForbiddenExecutor:
        def submit(self, *args, **kwargs):
            raise AssertionError("Pending review bypassed")

    pipeline._executor = ForbiddenExecutor()
    with pytest.raises(DomainError) as failure:
        pipeline._start(
            DOC_ID,
            str(path),
            "decision.eml",
            [],
            False,
            processing_scope={"schema_version": 1, "selection_hash": "old"}
            if stale_active_scope
            else None,
        )
    assert failure.value.code == "processing_scope_review_required"
    assert pipeline.registry.get(DOC_ID)["status"] == "done"


@pytest.mark.parametrize("fail_index", [False, True])
def test_new_scope_switches_only_at_publication_and_cancel_preserves_old(
    pipeline_env, monkeypatch, fail_index
):
    from app.services.processing_scope import (
        create_review,
        save_review_selection,
        snapshot_from_review,
    )
    from app.services.errors import VectorStoreError
    from app.services.export_okf import export_okf_bundle
    from docparser.attachment_policy import AttachmentInventory
    import json

    pipeline, path, _ = pipeline_env
    publish_scoped(pipeline_env)
    previous = pipeline.registry.get(DOC_ID)["processing_scope"]
    assert previous["excluded"] == 1
    with session_scope() as session:
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        original = session.get(DocumentGeneration, active).processing_scope_json
        inventory = AttachmentInventory.from_dict(original["inventory"])
        review = create_review(
            session, DOC_ID, inventory, base_generation_id=active, actor="editor"
        )
        review = save_review_selection(
            session,
            DOC_ID,
            review.id,
            review.revision,
            frozenset(n.source_key for n in inventory.nodes),
            actor="editor",
        )
        selected = snapshot_from_review(review)
    real_index = pipeline.vector_store.index_chunks
    observations = []

    def observed_index(*args, **kwargs):
        assert pipeline.registry.get(DOC_ID)["processing_scope"] == previous
        observations.append("old-scope-during-index")
        if fail_index:
            raise VectorStoreError("Synthetic index failure")
        return real_index(*args, **kwargs)

    monkeypatch.setattr(pipeline.vector_store, "index_chunks", observed_index)
    pipeline._process(DOC_ID, path, "decision.eml", [], resume=False, processing_scope=selected)
    assert observations
    if fail_index:
        assert pipeline.registry.get(DOC_ID)["processing_scope"] == previous
        pipeline.cancel_update(DOC_ID)
        assert pipeline.registry.get(DOC_ID)["processing_scope"] == previous
    else:
        assert pipeline.registry.get(DOC_ID)["processing_scope"]["excluded"] == 0
        destination = pipeline.settings.data_dir / "scope-export"
        files = export_okf_bundle(DOC_ID, destination)
        assert "processing-scope.json" in files
        from app.services.processing_scope_cache import embedding_fingerprint
        assert (
            json.loads((destination / "processing-scope.json").read_text(encoding="utf-8"))
            == {**selected,"vector_space":{"kind":"proven","fingerprint":embedding_fingerprint(pipeline.settings)}}
        )
