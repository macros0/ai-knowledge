from copy import deepcopy
from threading import BoundedSemaphore
import pytest

from tests.test_processing_scope_change import example
from app.db.models import Document, DocumentGeneration, DocumentGenerationState, DocumentProcessingReview, DocumentScopeChange
from app.db.session import session_scope
from app.services.processing_scope import ProcessingScopeConflict
import app.services.processing_scope_jobs as jobs


@pytest.fixture
def setup(monkeypatch):
    base, selection = example()
    selection["decisions"][0]["effective_included"] = False
    with session_scope() as s:
        s.add(Document(id=base.doc_id, filename="source.docx", status="done", file_hash=base.file_hash))
        s.flush()
        s.add(DocumentGeneration(id=base.generation_id, doc_id=base.doc_id, phase="active", processing_scope_json=base.scope))
        s.add(DocumentGenerationState(doc_id=base.doc_id, active_generation_id=base.generation_id))
        s.add(DocumentProcessingReview(doc_id=base.doc_id, id="e" * 32, revision=1, state="draft",
            source_file_hash=base.file_hash, parser_version="test", assessor_version="test", policy_json={},
            inventory_json=selection["inventory"], selection_json=selection["decisions"], selection_hash="d" * 64,
            base_generation_id=base.generation_id))
    monkeypatch.setattr(jobs, "load_scope_base", lambda doc_id,**kwargs: deepcopy(base))
    monkeypatch.setattr(jobs, "_fast_slots", BoundedSemaphore(1))
    dispatched = []
    monkeypatch.setattr(jobs, "_dispatch", lambda *args: dispatched.append(args))
    return base, dispatched


def preview(base):
    return jobs.create_change_preview(base.doc_id, "e" * 32, 1, base.generation_id, "editor")


def test_preview_has_no_candidate(setup):
    base, dispatched = setup
    result = preview(base)
    assert result["mode"] == "fast_exclude" and result["state"] == "preview"
    assert not dispatched
    with session_scope() as s:
        assert s.get(DocumentGenerationState, base.doc_id).candidate_generation_id is None
        assert s.get(DocumentProcessingReview, base.doc_id).state == "draft"


def test_double_apply_has_one_candidate_and_receipt(setup):
    base, dispatched = setup
    p = preview(base)
    first = jobs.apply_scope_change(base.doc_id, p["plan_id"], "1" * 32, base.generation_id, "editor", {"fast_exclude"})
    second = jobs.apply_scope_change(base.doc_id, p["plan_id"], "1" * 32, base.generation_id, "editor", {"fast_exclude"})
    assert first["candidate_generation_id"] == second["candidate_generation_id"]
    assert len(dispatched) == 1
    with session_scope() as s:
        assert s.query(DocumentScopeChange).filter(DocumentScopeChange.request_id.isnot(None)).count() == 1
        review = s.get(DocumentProcessingReview, base.doc_id)
        assert review.confirmed_by == "editor"
        snapshot = s.get(DocumentScopeChange, p["plan_id"]).plan_json["snapshot"]
        assert snapshot["confirmed_by"] == "editor" and snapshot["confirmed_at"]


def test_busy_worker_rejects_before_reservation(setup):
    base, _ = setup
    p = preview(base)
    jobs._fast_slots.acquire()
    with pytest.raises(jobs.ScopeWorkerBusy):
        jobs.apply_scope_change(base.doc_id, p["plan_id"], "1" * 32, base.generation_id, "editor", {"fast_exclude"})
    with session_scope() as s:
        assert s.get(DocumentGenerationState, base.doc_id).candidate_generation_id is None


def test_stale_review_base_and_digest(setup):
    base, _ = setup
    p = preview(base)
    with session_scope() as s:
        s.get(DocumentProcessingReview, base.doc_id).revision = 2
    with pytest.raises(ProcessingScopeConflict):
        jobs.apply_scope_change(base.doc_id, p["plan_id"], "1" * 32, base.generation_id, "editor", {"fast_exclude"})


def test_request_replay_after_cleanup(setup):
    base, dispatched = setup
    p = preview(base)
    first = jobs.apply_scope_change(base.doc_id, p["plan_id"], "1" * 32, base.generation_id, "editor", {"fast_exclude"})
    with session_scope() as s:
        row = s.get(DocumentScopeChange, p["plan_id"])
        row.state = "published"
        row.result_json = {"mode": "fast_exclude", "generation_id": first["candidate_generation_id"]}
        # A later publication and cleanup must not invalidate an accepted request.
        s.get(DocumentGenerationState, base.doc_id).active_generation_id = "f" * 32
        s.get(DocumentGenerationState, base.doc_id).candidate_generation_id = None
        s.query(DocumentGeneration).filter_by(id=first["candidate_generation_id"]).delete()
    replay = jobs.apply_scope_change(base.doc_id, p["plan_id"], "1" * 32, base.generation_id, "editor", {"fast_exclude"})
    assert replay["state"] == "published" and len(dispatched) == 1


def test_dispatch_failure_restores_old_publication(setup, monkeypatch):
    base, _ = setup
    p = preview(base)
    def failed(*args):
        raise RuntimeError("executor stopped")
    monkeypatch.setattr(jobs, "_dispatch", failed)
    with pytest.raises(RuntimeError):
        jobs.apply_scope_change(base.doc_id, p["plan_id"], "1" * 32, base.generation_id, "editor", {"fast_exclude"})
    with session_scope() as s:
        assert s.get(DocumentScopeChange, p["plan_id"]).state == "failed"
        assert s.get(DocumentGenerationState, base.doc_id).candidate_generation_id is None
        assert s.get(Document, base.doc_id).status == "done"


def test_changed_original_at_apply_is_rejected(setup, monkeypatch):
    base, _ = setup
    p = preview(base)
    changed = deepcopy(base)
    changed.current_file_hash = "f" * 64
    monkeypatch.setattr(jobs, "load_scope_base", lambda doc_id,**kwargs: changed)
    with pytest.raises(ProcessingScopeConflict):
        jobs.apply_scope_change(base.doc_id, p["plan_id"], "1" * 32, base.generation_id, "editor", {"fast_exclude"})


def test_cancel_blocks_next_write_and_restores_draft(setup):
    from app.services.update_workers import scope_write_guard, request_update_cancel
    from app.services.document_update import DocumentUpdateCancelled
    base, _ = setup
    p = preview(base)
    jobs.apply_scope_change(base.doc_id, p["plan_id"], "1" * 32, base.generation_id, "editor", {"fast_exclude"})
    with session_scope() as s:
        owner = s.get(DocumentScopeChange, p["plan_id"]).owner_token
    assert request_update_cancel(base.doc_id)
    with pytest.raises(DocumentUpdateCancelled):
        with scope_write_guard(base.doc_id, p["plan_id"], owner):
            pytest.fail("Canceled worker entered a write section")
    jobs.fail_scope_change(base.doc_id, p["plan_id"], owner)
    with session_scope() as s:
        assert s.get(DocumentScopeChange, p["plan_id"]).state == "canceled"
        review = s.get(DocumentProcessingReview, base.doc_id)
        assert review.state == "draft" and review.revision == 2
        assert s.get(DocumentGenerationState, base.doc_id).active_generation_id == base.generation_id


def test_pipeline_cancel_does_not_cleanup_live_scope_writer(setup):
    from threading import RLock
    from unittest.mock import Mock
    from app.services.pipeline import Pipeline
    from app.db.models import DocumentUpdateAttempt
    base, _ = setup
    p = preview(base)
    jobs.apply_scope_change(base.doc_id, p["plan_id"], "1" * 32, base.generation_id, "editor", {"fast_exclude"})
    pipeline = Pipeline.__new__(Pipeline)
    pipeline._start_lock, pipeline._threads, pipeline._abort_events = RLock(), {}, {}
    pipeline._finish_canceled_update, pipeline._cleanup_document_generations = Mock(), Mock()
    with session_scope() as s:
        attempt_id = s.get(DocumentUpdateAttempt, base.doc_id).id
    pipeline.cancel_update(base.doc_id, expected_update_id=attempt_id)
    pipeline._finish_canceled_update.assert_not_called()
    pipeline._cleanup_document_generations.assert_not_called()


def test_restart_preserves_live_owner_and_reclaims_expired_lease(setup):
    from datetime import datetime, timedelta, timezone
    from app.services.update_workers import scope_write_guard
    from app.services.document_update import DocumentUpdateCancelled
    base, _ = setup
    p = preview(base)
    jobs.apply_scope_change(base.doc_id, p["plan_id"], "1" * 32, base.generation_id, "editor", {"fast_exclude"})
    with session_scope() as s:
        owner = s.get(DocumentScopeChange, p["plan_id"]).owner_token
    from app.services.registry import DocumentRegistry
    DocumentRegistry().reset_stale_statuses()
    with session_scope() as s:
        assert s.get(Document, base.doc_id).status == "indexing"
    assert jobs.recover_scope_changes() == 0
    with session_scope() as s:
        s.get(DocumentScopeChange, p["plan_id"]).lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert jobs.recover_scope_changes() == 1
    with pytest.raises(DocumentUpdateCancelled):
        with scope_write_guard(base.doc_id, p["plan_id"], owner):
            pytest.fail("Late writer reclaimed a terminal operation")


def test_delta_uses_existing_pipeline_capacity_and_leaves_fast_slot(setup, monkeypatch):
    from types import SimpleNamespace
    from threading import RLock, BoundedSemaphore
    base, _ = setup
    p = preview(base)
    with session_scope() as s:
        row = s.get(DocumentScopeChange, p["plan_id"])
        value = deepcopy(row.plan_json)
        value["preview"]["mode"] = "incremental_generate"
        row.plan_json = value
    pipeline = SimpleNamespace(_start_lock=RLock(), _threads={}, _pipeline_slots=BoundedSemaphore(1),
                               _queue_limit=lambda admin: 0, settings=SimpleNamespace(pipeline_max_workers=1))
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    jobs.apply_scope_change(base.doc_id, p["plan_id"], "1" * 32, base.generation_id, "editor", {"incremental_generate"})
    assert jobs._fast_slots.acquire(blocking=False)
    assert not pipeline._pipeline_slots.acquire(blocking=False)
    assert base.doc_id in pipeline._threads
    with session_scope() as s:
        row = s.get(DocumentScopeChange, p["plan_id"])
        assert row.state == "queued" and row.lease_expires_at is not None


def test_expired_queued_owner_is_recovered_without_new_generation(setup):
    from datetime import datetime, timedelta, timezone
    base, _ = setup
    p = preview(base)
    jobs.apply_scope_change(base.doc_id, p["plan_id"], "1" * 32, base.generation_id, "editor", {"fast_exclude"})
    with session_scope() as s:
        row = s.get(DocumentScopeChange, p["plan_id"])
        row.state = "queued"
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert jobs.recover_scope_changes() == 1
    with session_scope() as s:
        assert s.get(DocumentScopeChange, p["plan_id"]).state == "failed"
        assert s.get(DocumentGenerationState, base.doc_id).active_generation_id == base.generation_id
        assert s.get(DocumentProcessingReview, base.doc_id).state == "draft"


def test_receipt_admitted_between_initial_read_and_document_lock_is_replayed(setup, monkeypatch):
    base, dispatched = setup
    p = preview(base)
    original_lock=jobs.lock_document_write
    def concurrent_admission(session,doc_id,**kwargs):
        with session_scope() as s:
            row = s.get(DocumentScopeChange, p["plan_id"])
            row.request_id, row.state = "1" * 32, "published"
            row.result_json = {"generation_id":base.generation_id, "mode":"fast_exclude"}
        return original_lock(session,doc_id,**kwargs)
    monkeypatch.setattr(jobs, "lock_document_write", concurrent_admission)
    result = jobs.apply_scope_change(base.doc_id, p["plan_id"], "1" * 32, base.generation_id, "editor", {"fast_exclude"})
    assert result["state"] == "published" and not dispatched
