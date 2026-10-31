"""Document publication and stale handler writes share durable work fencing."""
import pytest

from app.db.models import DocumentGeneration, DocumentGenerationState, WorkItem
from app.db.session import session_scope
from app.services.generation_publication import publish_prepared_document
from app.services.work.models import LostOwnership, WorkSpec
from app.services.work.store import enqueue, claim, cancel
from app.services.work.document_handler import document_execution, prepare_document_attempt
from tests.test_generation_cleanup import _prepare_second
from tests.test_generation_pipeline import DOC_ID, _published_snapshot
from tests.test_generation_pipeline import pipeline_env as pipeline_env
from tests.test_work_store import expire


def document_claim():
    with session_scope() as session:
        enqueue(session, WorkSpec("document", 1, {"action": "regenerate", "doc_id": DOC_ID}, "document-request", DOC_ID))
        return claim(session, owner_id="worker-a", supported={"document": {1}}, lease_seconds=120)


def bind_candidate(candidate, work):
    with session_scope() as session:
        row = session.get(DocumentGeneration, candidate)
        row.work_id, row.work_fence = work.work_id, work.fence


def test_old_worker_returns_after_reclaim(pipeline_env, monkeypatch):
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    before = _published_snapshot(pipeline)
    old = document_claim()
    bind_candidate(candidate, old)
    expire(old.work_id)
    with session_scope() as session:
        current = claim(session, owner_id="worker-b", supported={"document": {1}}, lease_seconds=120)
    assert current.fence > old.fence
    with pytest.raises(LostOwnership):
        publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate, claim=old)
    assert _published_snapshot(pipeline) == before


def test_publication_and_work_success_commit_together(pipeline_env, monkeypatch):
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    work = document_claim()
    bind_candidate(candidate, work)
    assert publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate, claim=work)
    with session_scope() as session:
        assert session.get(WorkItem, work.work_id).status == "succeeded"
        assert session.get(DocumentGenerationState, DOC_ID).active_generation_id == candidate


def test_cancel_prevents_document_publication(pipeline_env, monkeypatch):
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    before = _published_snapshot(pipeline)
    work = document_claim()
    bind_candidate(candidate, work)
    with session_scope() as session:
        cancel(session, work.work_id)
    with pytest.raises(LostOwnership):
        publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate, claim=work)
    assert _published_snapshot(pipeline) == before


def test_old_handler_cannot_update_registry_after_reclaim(pipeline_env):
    pipeline, _, _ = pipeline_env
    work = document_claim()
    expire(work.work_id)
    with pytest.raises(LostOwnership), document_execution(work):
        pipeline.registry.update(DOC_ID, status="paused", error="stale result")
    assert pipeline.registry.get(DOC_ID)["status"] == "done"


def test_reclaim_always_uses_new_generation_namespace(pipeline_env):
    pipeline, _, _ = pipeline_env
    work = document_claim()
    with session_scope() as session:
        first = prepare_document_attempt(session, work, pipeline.settings).id
    expire(work.work_id)
    with session_scope() as session:
        new = claim(session, owner_id="replacement", supported={"document": {1}}, lease_seconds=120)
        second = prepare_document_attempt(session, new, pipeline.settings).id
    assert first != second
    with session_scope() as session:
        assert session.get(DocumentGeneration, first).phase == "abandoned"
        assert session.get(DocumentGenerationState, DOC_ID).candidate_generation_id == second


def test_work_candidate_cannot_use_legacy_publication_without_claim(pipeline_env, monkeypatch):
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    before = _published_snapshot(pipeline)
    work = document_claim()
    bind_candidate(candidate, work)
    with pytest.raises(LostOwnership):
        publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)
    assert _published_snapshot(pipeline) == before


def test_work_status_rolls_back_with_failed_canonical_publication(pipeline_env, monkeypatch):
    from app.services import generation_publication
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    before = _published_snapshot(pipeline)
    work = document_claim()
    bind_candidate(candidate, work)
    def fail(*_args, **_kwargs):
        raise RuntimeError("canonical write interrupted")
    monkeypatch.setattr(generation_publication, "replace_chunks", fail)
    with pytest.raises(RuntimeError, match="canonical write interrupted"):
        publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate, claim=work)
    assert _published_snapshot(pipeline) == before
    with session_scope() as session:
        assert session.get(WorkItem, work.work_id).status == "running"


def test_late_qdrant_write_never_visible_after_work_generation_cleanup(pipeline_env):
    from uuid import uuid4
    from qdrant_client.http import models as qm
    pipeline, _, _ = pipeline_env
    old = document_claim()
    with session_scope() as session:
        first = prepare_document_attempt(session, old, pipeline.settings).id
    expire(old.work_id)
    with session_scope() as session:
        current = claim(session, owner_id="replacement", supported={"document": {1}}, lease_seconds=120)
        prepare_document_attempt(session, current, pipeline.settings)
    pipeline.cleanup_inactive_generations()
    with session_scope() as session:
        assert session.get(DocumentGeneration, first) is not None
    point_id = str(uuid4())
    vector = [1.0] + [0.0] * 7
    pipeline.vector_store.client.upsert(pipeline.vector_store.collection, [qm.PointStruct(id=point_id, vector=vector,
        payload={"doc_id": DOC_ID, "generation_id": first, "point_type": "concept", "slug": "stale", "deleted": False})])
    assert pipeline.vector_store.client.retrieve(pipeline.vector_store.collection, [point_id])
    hits = pipeline.vector_store.search_composite(dense_vec=vector, sparse_vec=None, tags=None, branches={"dense"}, top_k=20)
    assert all(hit.point_id != point_id for hit in hits)


def test_attempt_preparation_remains_fenced_until_caller_commit(pipeline_env):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update
    pipeline, _, _ = pipeline_env
    work = document_claim()
    with pytest.raises(LostOwnership), session_scope() as session:
        prepare_document_attempt(session, work, pipeline.settings)
        session.execute(update(WorkItem).where(WorkItem.id == work.work_id)
            .values(lease_until=datetime.now(timezone.utc) - timedelta(seconds=10))
            .execution_options(synchronize_session=False))


def test_nested_scopes_share_work_transaction_and_rollback_inner_changes(pipeline_env):
    from app.db.models import Document
    work = document_claim()
    with document_execution(work), session_scope() as outer:
        with pytest.raises(ValueError, match="inner rollback"):
            with session_scope() as inner:
                assert inner is outer
                inner.get(Document, DOC_ID).status = "paused"
                inner.flush()
                raise ValueError("inner rollback")
        assert outer.get(Document, DOC_ID).status == "done"


def test_work_publication_records_duplicate_refresh_as_durable_intent(pipeline_env, monkeypatch):
    from tests.test_generation_publication_service import _rewrite_manifest
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    _rewrite_manifest(pipeline, candidate, lambda body: body.setdefault("parse_effects", {}).update(signature={"test": True}))
    monkeypatch.setattr("app.services.deduplication.find_duplicates_for_document", lambda *_a: {"level2": [{"doc": {"id": "b" * 16}}], "level3": []})
    monkeypatch.setattr("app.services.deduplication.apply_document_signature", lambda *_a: None)
    monkeypatch.setattr("app.services.deduplication.refresh_duplicate_flags", lambda *_a: pytest.fail("Completed document work must enqueue maintenance"))
    work = document_claim()
    bind_candidate(candidate, work)
    publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate, claim=work)
    with session_scope() as session:
        intent = session.query(WorkItem).filter_by(kind="duplicate_refresh").one()
        assert intent.payload_json == {"doc_id": DOC_ID, "previous_ids": ["b" * 16]}
        assert session.get(WorkItem, work.work_id).status == "succeeded"


def test_different_live_work_cannot_abandon_current_candidate(pipeline_env):
    pipeline, _, _ = pipeline_env
    first = document_claim()
    with session_scope() as session:
        first_generation = prepare_document_attempt(session, first, pipeline.settings).id
        enqueue(session, WorkSpec("document", 1, {"action": "regenerate", "doc_id": DOC_ID}, "other-request", DOC_ID))
        other = claim(session, owner_id="other-worker", supported={"document": {1}}, lease_seconds=120)
    with pytest.raises(LostOwnership), session_scope() as session:
        prepare_document_attempt(session, other, pipeline.settings)
    with session_scope() as session:
        assert session.get(DocumentGenerationState, DOC_ID).candidate_generation_id == first_generation


def test_hard_delete_retains_minimal_late_write_exclusion(pipeline_env):
    from app.services.generation_search import generation_exclusions
    pipeline, _, _ = pipeline_env
    work = document_claim()
    with session_scope() as session:
        generation_id = prepare_document_attempt(session, work, pipeline.settings).id
    assert pipeline.registry.delete(DOC_ID)
    conditions = generation_exclusions()
    assert any(generation_id in condition.match.any for condition in conditions if hasattr(condition, "match"))
    with session_scope() as session:
        assert session.get(WorkItem, work.work_id).cancel_requested


def test_soft_delete_invalidates_running_work_before_restore(pipeline_env):
    pipeline, _, _ = pipeline_env
    work = document_claim()
    assert pipeline.registry.soft_delete(DOC_ID)
    assert pipeline.registry.restore(DOC_ID)
    with pytest.raises(LostOwnership), document_execution(work):
        pipeline.registry.update(DOC_ID, status="done")


@pytest.mark.parametrize("operation", ["prepare", "ready", "write"])
def test_work_owned_generation_rejects_unguarded_mutators(pipeline_env, operation):
    from app.services.generation_store import prepare_generation_attempt, mark_generation_ready, lock_writable_generation
    pipeline, _, _ = pipeline_env
    work = document_claim()
    with session_scope() as session:
        generation_id = prepare_document_attempt(session, work, pipeline.settings).id
    with pytest.raises(LostOwnership), session_scope() as session:
        if operation == "prepare":
            prepare_generation_attempt(session, DOC_ID, resume=True)
        elif operation == "ready":
            mark_generation_ready(session, DOC_ID, generation_id)
        else:
            lock_writable_generation(session, DOC_ID, generation_id)


def test_distributed_direct_writer_cannot_allocate_unowned_generation(pipeline_env):
    from app.services.generation_store import begin_generation
    pipeline, _, _ = pipeline_env
    pipeline.settings.okf_deployment_schema, pipeline.settings.app_role = "replicas-v1", "api"
    with pytest.raises(LostOwnership), session_scope() as session:
        begin_generation(session, DOC_ID)


def test_work_candidate_cannot_be_abandoned_by_unguarded_writer(pipeline_env):
    from app.services.generation_store import abandon_generation
    pipeline, _, _ = pipeline_env
    work = document_claim()
    with session_scope() as session:
        generation_id = prepare_document_attempt(session, work, pipeline.settings).id
    with pytest.raises(LostOwnership), session_scope() as session:
        abandon_generation(session, DOC_ID, generation_id)


def test_completion_marker_cannot_authorize_a_later_transaction(pipeline_env):
    from app.db.models import Document
    from app.db.session import get_session_factory
    from app.services.work.context import bind_session
    from app.services.work.store import finish
    work = document_claim()
    session = get_session_factory()()
    try:
        bind_session(session, work)
        finish(session, work, outcome="succeeded")
        session.commit()
        session.get(Document, DOC_ID).status = "paused"
        with pytest.raises(LostOwnership):
            session.commit()
    finally:
        session.rollback()
        session.close()
