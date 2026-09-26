"""Deletion must wait for worker ownership and close new admission first."""
from concurrent.futures import Future
from threading import Event

import pytest

from app.services.errors import ConflictError, NotFoundError
from tests.test_generation_pipeline import DOC_ID, _published_snapshot
from tests.test_generation_pipeline import pipeline_env as pipeline_env


@pytest.mark.parametrize("operation", ["remove", "remove_if_deleted", "soft_delete"])
def test_live_worker_timeout_preserves_document_and_files(pipeline_env, operation):
    pipeline, source, _write = pipeline_env
    if operation == "remove_if_deleted":
        pipeline.registry.soft_delete(DOC_ID)
    before = _published_snapshot(pipeline)
    task = Future()
    task.set_running_or_notify_cancel()
    abort = Event()
    pipeline._threads[DOC_ID] = task
    pipeline._abort_events[DOC_ID] = abort
    try:
        with pytest.raises(ConflictError) as caught:
            getattr(pipeline, operation)(DOC_ID)
        assert caught.value.code == "processing_stopping"
        assert abort.is_set()
        assert pipeline._threads[DOC_ID] is task
        assert pipeline.registry.get(DOC_ID) is not None
        assert source.is_file()
        assert _published_snapshot(pipeline) == before
        # Once the worker really finishes, retry can perform the operation.
        task.set_result(None)
        getattr(pipeline, operation)(DOC_ID)
        if operation != "soft_delete":
            assert pipeline.registry.get(DOC_ID) is None
            assert not source.exists()
    finally:
        if not task.done():
            task.set_result(None)
        pipeline._threads.pop(DOC_ID, None)
        pipeline._abort_events.pop(DOC_ID, None)


def test_remove_retires_canonical_document_before_physical_cleanup(pipeline_env, monkeypatch):
    pipeline, _source, _write = pipeline_env
    observed = []
    original = pipeline._physical_cleanup

    def cleanup(doc_id):
        observed.append(pipeline.registry.get(doc_id))
        original(doc_id)

    monkeypatch.setattr(pipeline, "_physical_cleanup", cleanup)
    pipeline.remove(DOC_ID)
    assert observed == [None]


@pytest.mark.parametrize("state", ["purged", "trashed"])
def test_admission_rechecks_document_after_a_stale_regenerate_read(pipeline_env, monkeypatch, state):
    pipeline, _source, _write = pipeline_env
    original = pipeline._ensure_not_running
    calls = []

    def check(doc_id):
        original(doc_id)
        if not calls:
            calls.append(doc_id)
            if state == "purged":
                pipeline.registry.delete(doc_id)
            else:
                pipeline.registry.soft_delete(doc_id)

    submitted = []
    monkeypatch.setattr(pipeline, "_ensure_not_running", check)
    monkeypatch.setattr(pipeline._executor, "submit", lambda *args: submitted.append(args) or Future())
    with pytest.raises((NotFoundError, ConflictError)):
        pipeline.regenerate(DOC_ID)
    assert submitted == []


def test_worker_cancelled_while_starting_leaves_resumable_status(pipeline_env, monkeypatch):
    pipeline, source, _write = pipeline_env
    entered, release = Event(), Event()

    def blocked_process(*_args, **_kwargs):
        entered.set()
        assert release.wait(8)

    monkeypatch.setattr(pipeline, "_process", blocked_process)
    pipeline._start(DOC_ID, str(source), "decision.eml", [], resume=False)
    task = pipeline._threads[DOC_ID]
    try:
        assert entered.wait(5)
        with pytest.raises(ConflictError):
            pipeline.soft_delete(DOC_ID)
    finally:
        release.set()
        task.result(timeout=8)
    assert pipeline.registry.get(DOC_ID)["status"] == "paused"
