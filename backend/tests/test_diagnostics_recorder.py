import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest

from app.services.diagnostics.recorder import DiagnosticRecorder
from app.services.diagnostics.schema import DiagnosticLimits
from app.services.diagnostics.store import DiagnosticStore


@pytest.mark.parametrize("dependency", ["llm", "embeddings", "qdrant"])
def test_failed_dependency_is_retained_without_capture(tmp_path, dependency):
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    recorder = DiagnosticRecorder(store)
    recorder.start()
    try:
        accepted = recorder.emit("dependency_call_finished", exception=TimeoutError("CANARY_RESPONSE"),
                                 fields={"dependency": dependency, "duration_ms": 20,
                                         "error_code": "internal_error"})
    finally:
        recorder.stop()
    assert accepted
    events = [json.loads(line) for path in (store.root / "events/baseline").glob("*.jsonl")
              for line in path.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["dependency"] == dependency
    assert events[0]["level"] == "ERROR"
    assert "CANARY" not in repr(events)


def test_database_is_not_required_and_message_never_formatted(tmp_path, monkeypatch):
    from app.db import session
    monkeypatch.setattr(session, "get_engine", lambda: (_ for _ in ()).throw(AssertionError("DB used")))
    class Private:
        def __str__(self):
            raise AssertionError("Secret formatted")
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    recorder = DiagnosticRecorder(store)
    recorder.start()
    try:
        record = logging.LogRecord("app.services.pipeline", logging.ERROR, __file__, 1,
                                   "CANARY_PASSWORD %s", (Private(),), None)
        recorder.handler.emit(record)
    finally:
        recorder.stop()
    content = b"".join(path.read_bytes() for path in (tmp_path / "spool/events").rglob("*.jsonl"))
    assert b"CANARY_PASSWORD" not in content
    assert json.loads(content)["event_code"] == "log_error"


def test_queue_overflow_never_blocks(tmp_path):
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0, queue_size=1))
    entered, release = threading.Event(), threading.Event()
    original = store.append_batch
    def slow_append(*args, **kwargs):
        entered.set()
        release.wait(3)
        return original(*args, **kwargs)
    store.append_batch = slow_append
    recorder = DiagnosticRecorder(store)
    recorder.start()
    try:
        assert recorder.emit("log_error")
        assert entered.wait(2)
        start = time.monotonic()
        for _ in range(100):
            recorder.emit("operation_failed", fields={"error_code": "internal_error"})
        assert time.monotonic() - start < 0.5
        assert recorder.status()["dropped"] > 0
    finally:
        release.set()
        recorder.stop()


def test_success_requests_are_bounded_without_sampling_errors(tmp_path):
    session_id = str(uuid4())
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    recorder = DiagnosticRecorder(store, capture_selector=lambda *_: session_id)
    recorder.monotonic = lambda: 100.0
    success = {"route_template": "/api/settings", "http_method": "GET",
               "http_status": 200, "duration_ms": 2}
    for _ in range(50):
        recorder.emit("request_finished", fields=success)
    assert recorder.status()["sampled_success"] == 30
    assert recorder.emit("request_finished", fields={**success, "http_status": 500})
    recorder.monotonic = lambda: 101.0
    assert recorder.emit("request_finished", fields=success)
    recorder.start()
    recorder.stop()
    captured = [json.loads(line) for path in (store.root / "events" / session_id).glob("*.jsonl")
                for line in path.read_text().splitlines()]
    assert sum(row["http_status"] == 200 for row in captured) == 21
    assert any(row["http_status"] == 500 for row in captured)


def test_revoked_capture_does_not_write_queued_detail(tmp_path):
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    active = [str(uuid4())]
    recorder = DiagnosticRecorder(store, capture_selector=lambda *_: active[0])
    # Queue while no worker is running, then revoke before starting worker.
    assert recorder.emit("stage_started", fields={"stage": "parse"})
    active[0] = None
    recorder.start()
    recorder.stop()
    assert not list((tmp_path / "spool/events").rglob("*.jsonl"))
    assert recorder.status()["expired_queue"] == 1


def test_manual_capture_drain_keeps_accepted_event_and_closes_admission(tmp_path):
    session_id = str(uuid4())
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    active = [session_id]
    entered, release = threading.Event(), threading.Event()
    original = store.append_batch

    def blocked_append(*args, **kwargs):
        entered.set()
        release.wait(3)
        return original(*args, **kwargs)

    store.append_batch = blocked_append
    recorder = DiagnosticRecorder(store, capture_selector=lambda *_: active[0])
    recorder.start()
    try:
        assert recorder.emit("stage_started", fields={"stage": "parse"})
        assert entered.wait(2)
        result = []
        draining = threading.Thread(target=lambda: result.append(recorder.drain_capture(session_id, 2)))
        draining.start()
        time.sleep(0.05)
        assert not recorder.emit("stage_started", fields={"stage": "parse"})
        release.set()
        draining.join(2)
        assert result == [True]
        active[0] = None
    finally:
        release.set()
        recorder.stop()
    events = [json.loads(line) for path in (store.root / "events" / session_id).glob("*.jsonl")
              for line in path.read_text().splitlines()]
    assert [event["event_code"] for event in events] == ["stage_started"]
    assert recorder.status()["expired_queue"] == 0


def test_finished_aggregate_releases_session_view(tmp_path):
    from types import SimpleNamespace
    from app.services.diagnostics.schema import DiagnosticContext

    session_id = str(uuid4())
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    recorder = DiagnosticRecorder(store, capture_selector=lambda *_: session_id)
    view = SimpleNamespace(session_id=session_id, doc_id=None, scope="interface",
                           policy=SimpleNamespace(aggregate_interval_ms=5000))
    recorder._observe_success(view, "request_finished", DiagnosticContext(),
                              {"route_template": "/api/search", "duration_ms": 1})
    recorder._flush_aggregates()
    recorder._flush_batches()
    assert recorder._aggregate_views == {}
    events = [json.loads(line) for path in (store.root / "events" / session_id).glob("*.jsonl")
              for line in path.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["event_code"] == "success_aggregate"
    store.close()


def test_stopped_capture_history_is_bounded(tmp_path):
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0, queue_size=2048))
    recorder = DiagnosticRecorder(store)
    ids = [str(uuid4()) for _ in range(1025)]
    for session_id in ids:
        recorder.drain_capture(session_id, timeout_seconds=0)
    assert len(recorder._closed_captures) == 1024
    assert ids[-1] in recorder._closed_captures
    store.close()


def test_snapshot_barrier_flushes_accepted_events_without_closing_capture(tmp_path):
    session_id = str(uuid4())
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    recorder = DiagnosticRecorder(store, capture_selector=lambda *_: session_id)
    recorder.start()
    try:
        assert recorder.emit("stage_started", fields={"stage": "parse"})
        assert recorder.flush_pending() is True
        assert recorder.status()["queued"] == 0
        assert recorder.emit("stage_started", fields={"stage": "index"})
    finally:
        recorder.stop()
    captured = [json.loads(line) for path in (store.root / "events" / session_id).glob("*.jsonl")
                for line in path.read_text().splitlines()]
    assert [row["stage"] for row in captured] == ["parse", "index"]


def test_permission_error_in_recorder_does_not_escape(tmp_path):
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    def fail(*args, **kwargs):
        raise PermissionError("CANARY")
    store.append_batch = fail
    recorder = DiagnosticRecorder(store)
    recorder.start()
    assert recorder.emit("log_error")
    recorder.stop()
    assert recorder.status()["dropped"] == 1


def test_baseline_error_survives_capture_expiry(tmp_path):
    session_id = str(uuid4())
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    recorder = DiagnosticRecorder(store, capture_selector=lambda *_: session_id)
    recorder.start()
    recorder.emit("log_error", exception=ValueError("CANARY_PRIVATE_ERROR"))
    recorder.stop()
    with store:
        store.retire_capture(session_id, datetime.now(timezone.utc))
        store.sweep(datetime.now(timezone.utc) + timedelta(hours=25))
    baseline = list((store.root / "events/baseline").glob("*.jsonl"))
    assert baseline
    events = [json.loads(line) for path in baseline for line in path.read_text().splitlines()]
    assert events[0]["event_code"] == "log_error"
    assert "CANARY" not in repr(events)
    assert not list((store.root / "events" / session_id).glob("*.jsonl"))


def test_duplicate_summary_preserves_first_last_and_count(tmp_path):
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    recorder = DiagnosticRecorder(store)
    for _ in range(8):
        assert recorder.emit("log_error", exception=ValueError("CANARY_CONTENT"))
    recorder.start()
    recorder.stop()
    events = [json.loads(line) for path in (store.root / "events").rglob("*.jsonl")
              for line in path.read_text().splitlines()]
    assert len(events) == 2
    first, summary = events
    assert summary["counts"]["repeats"] == 7
    assert summary["first_timestamp_utc"] == first["timestamp_utc"]
    assert summary["last_timestamp_utc"] >= summary["first_timestamp_utc"]
    assert "CANARY" not in repr(events)


def test_identical_errors_with_distinct_request_ids_remain_separate(tmp_path):
    from app.services.diagnostics.schema import DiagnosticContext

    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    recorder = DiagnosticRecorder(store)
    request_ids = [str(uuid4()), str(uuid4())]
    recorder.start()
    try:
        for request_id in request_ids:
            assert recorder.emit("log_error", context=DiagnosticContext(request_id=request_id),
                                 exception=ValueError("CANARY_PRIVATE"))
    finally:
        recorder.stop()
    events = [json.loads(line) for path in (store.root / "events" / "baseline").glob("*.jsonl")
              for line in path.read_text().splitlines()]
    assert {event["request_id"] for event in events} == set(request_ids)
    assert [event["event_code"] for event in events] == ["log_error", "log_error"]
    assert "CANARY_PRIVATE" not in repr(events)


def test_capture_byte_accounting_notifies_session(tmp_path):
    from app.services.diagnostics.sessions import DiagnosticSessionService
    from app.config import Settings
    from tests.test_diagnostics_sessions import actor
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", diagnostics_dir=store.root,
                        diagnostics_capture_enabled=True)
    with store:
        service = DiagnosticSessionService(store, settings)
        session = service.start("system", 5, actor())
        recorder = DiagnosticRecorder(store, capture_selector=service.active_for,
                                      on_capture_written=service.record_written,
                                      on_capture_failed=service.recording_failed)
        recorder.start()
        assert recorder.emit("stage_started", fields={"stage": "parse"})
        recorder.stop()
        capture_bytes = sum(path.stat().st_size for path in (store.root / "events" / session.id).glob("*.jsonl"))
        assert service.status()["session"]["bytes_written"] == capture_bytes > 0


def test_structured_failure_not_recorded_twice_by_log_handler(tmp_path):
    from app.services.diagnostics.context import bind_context, new_operation
    from app.services.diagnostics.recorder import emit_event, set_recorder
    from app.services.diagnostics.schema import DiagnosticContext
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    recorder = DiagnosticRecorder(store)
    recorder.start()
    set_recorder(recorder)
    exception = ValueError("CANARY_SECRET")
    try:
        with bind_context(new_operation(DiagnosticContext(request_id=str(uuid4())))):
            emit_event("operation_failed", exception=exception, fields={"stage": "parse"})
            recorder.handler.emit(logging.LogRecord("app.test", logging.ERROR, __file__, 1,
                                                   "CANARY", (), (type(exception), exception, None)))
    finally:
        set_recorder(None)
        recorder.stop()
    events = [json.loads(line) for path in (store.root / "events").rglob("*.jsonl")
              for line in path.read_text().splitlines()]
    assert [event["event_code"] for event in events] == ["operation_failed"]
