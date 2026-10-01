"""Admit success work before constructing a diagnostic event."""
from dataclasses import replace
from uuid import uuid4

import pytest

from app.config import Settings
from app.services.diagnostics.schema import DiagnosticContext


def view(tmp_path, level="standard"):
    from app.services.diagnostics.policy import CaptureRuntimeView, build_policy
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", diagnostics_dir=tmp_path / "logs")
    return CaptureRuntimeView(session_id=str(uuid4()), revision=1, scope="system", doc_id=None,
                              deadline_mono=1000.0, policy=build_policy(level, settings))


def test_standard_aggregates_success_but_records_errors(tmp_path):
    from app.services.diagnostics.policy import PolicyEngine
    policy = PolicyEngine(monotonic=lambda: 100.0)
    session = view(tmp_path)
    context = DiagnosticContext(operation_id=str(uuid4()), operation_kind="search_chat")
    success = policy.decide(session, "dependency_call_finished", context,
                            {"dependency": "qdrant", "duration_ms": 8.0, "success": True})
    assert success.action == "aggregate"
    assert policy.decide(session, "operation_failed", context,
                         {"stage": "search", "success": False}).action == "record"
    assert policy.decide(session, "stage_started", context, {"stage": "search"}).action == "omit"


def test_detailed_trace_admission_keeps_existing_chain_when_rate_exhausted(tmp_path):
    from app.services.diagnostics.policy import PolicyEngine
    session = view(tmp_path, "detailed")
    session = replace(session, policy=replace(session.policy, trace_limit_per_second=1))
    engine = PolicyEngine(monotonic=lambda: 100.0)
    first = DiagnosticContext(operation_id=str(uuid4()), operation_kind="search_chat")
    second = DiagnosticContext(operation_id=str(uuid4()), operation_kind="search_chat")
    assert engine.decide(session, "operation_started", first, {"stage": "search"}).action == "record"
    assert engine.decide(session, "dependency_call_finished", first,
                         {"dependency": "qdrant", "duration_ms": 10, "success": True}).action == "record"
    assert engine.decide(session, "operation_started", second, {"stage": "search"}).action == "omit"
    assert engine.decide(session, "operation_failed", second,
                         {"stage": "search", "success": False}).action == "record"


def test_detailed_inflight_limit_never_evicts_selected_chain(tmp_path):
    from app.services.diagnostics.policy import PolicyEngine
    session = view(tmp_path, "detailed")
    session = replace(session, policy=replace(session.policy, max_inflight_traces=1,
                                              trace_limit_per_second=2))
    engine = PolicyEngine(monotonic=lambda: 100.0)
    first = DiagnosticContext(operation_id=str(uuid4()), operation_kind="search_chat")
    second = DiagnosticContext(operation_id=str(uuid4()), operation_kind="search_chat")
    assert engine.decide(session, "operation_started", first, {"stage": "search"}).action == "record"
    assert engine.decide(session, "operation_started", second, {"stage": "search"}).action == "omit"
    assert engine.decide(session, "stage_finished", first, {"stage": "search"}).action == "record"
    engine.finish_trace(session, first)
    assert engine.decide(session, "operation_started", second, {"stage": "search"}).action == "record"


def test_default_detailed_success_budget_keeps_errors_outside_sampling(tmp_path):
    from app.services.diagnostics.policy import PolicyEngine
    session = view(tmp_path, "detailed")
    engine = PolicyEngine(monotonic=lambda: 100.0)
    contexts = [DiagnosticContext(operation_id=str(uuid4()), operation_kind="search_chat")
                for _ in range(6)]
    decisions = [engine.decide(session, "operation_started", context,
                               {"stage": "search", "success": True})
                 for context in contexts]
    assert decisions[0].action == "record"
    assert [decision.action for decision in decisions[1:]] == ["omit"] * 5
    assert engine.decide(session, "operation_failed", contexts[5],
                         {"stage": "search", "success": False}).action == "record"


def test_recorder_aggregates_success_before_event_encoding(tmp_path, monkeypatch):
    import json
    from app.services.diagnostics import recorder as recorder_module
    from app.services.diagnostics.recorder import DiagnosticRecorder
    from app.services.diagnostics.schema import DiagnosticLimits
    from app.services.diagnostics.store import DiagnosticStore

    session = view(tmp_path)
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    recorder = DiagnosticRecorder(store, capture_selector=lambda *_: session.session_id,
                                  capture_view_selector=lambda *_: session)
    real_encode = recorder_module.encode_event
    encoded = []
    def encode(event):
        encoded.append(event["event_code"])
        return real_encode(event)
    monkeypatch.setattr(recorder_module, "encode_event", encode)
    context = DiagnosticContext(operation_id=str(uuid4()), operation_kind="search_chat")
    for _ in range(1000):
        assert recorder.emit("dependency_call_finished", context=context,
                             fields={"dependency": "qdrant", "duration_ms": 10})
    assert encoded == []
    recorder.start()
    assert recorder.flush_pending(timeout_seconds=10)
    recorder.stop()
    events = [json.loads(line) for path in (store.root / "events" / session.session_id).glob("*.jsonl")
              for line in path.read_text(encoding="utf-8").splitlines()]
    aggregates = [event for event in events if event["event_code"] == "success_aggregate"]
    assert sum(event["counts"]["count"] for event in aggregates) == 1000
    assert "dependency_call_finished" not in [event["event_code"] for event in events]


def test_detailed_request_decision_follows_child_operation_id(tmp_path):
    from app.services.diagnostics.policy import PolicyEngine
    session = view(tmp_path, "detailed")
    engine = PolicyEngine(monotonic=lambda: 100.0)
    request_id = str(uuid4())
    request = DiagnosticContext(request_id=request_id, operation_kind="search_chat")
    child = DiagnosticContext(request_id=request_id, operation_id=str(uuid4()),
                              operation_kind="search_chat")
    assert engine.begin_trace(session, request)
    assert engine.decide(session, "operation_started", child, {"stage": "search"}).action == "record"
    assert engine.decide(session, "dependency_call_finished", child,
                         {"dependency": "qdrant", "duration_ms": 10, "success": True}).action == "record"
    engine.finish_trace(session, request)
    assert engine.decide(session, "request_finished", request,
                         {"duration_ms": 12, "http_status": 200, "success": True}).action == "aggregate"


def test_late_old_session_cannot_reset_new_trace_admission(tmp_path):
    from app.services.diagnostics.policy import PolicyEngine
    old = view(tmp_path, "detailed")
    new = replace(view(tmp_path, "detailed"), revision=2)
    engine = PolicyEngine(monotonic=lambda: 100.0)
    context = DiagnosticContext(operation_id=str(uuid4()), operation_kind="document")
    assert engine.begin_trace(new, context)
    assert engine.decide(old, "stage_started", context, {"stage": "parse"}).action == "omit"
    assert engine.decide(new, "stage_started", context, {"stage": "parse"}).action == "record"


def test_slow_success_limit_is_visible_and_error_is_never_sampled(tmp_path):
    from app.services.diagnostics.policy import PolicyEngine
    session = view(tmp_path)
    session = replace(session, policy=replace(session.policy, slow_limit_per_second=1))
    engine = PolicyEngine(monotonic=lambda: 100.0)
    context = DiagnosticContext(operation_id=str(uuid4()), operation_kind="search_chat")
    facts = {"dependency": "qdrant", "duration_ms": 300, "success": True}
    assert engine.decide(session, "dependency_call_finished", context, facts).reason == "slow"
    second = engine.decide(session, "dependency_call_finished", context, facts)
    assert (second.action, second.reason) == ("aggregate", "slow_limit")
    assert engine.decide(session, "dependency_call_finished", context,
                         {**facts, "success": False}).action == "record"


@pytest.mark.parametrize("terminal", ["operation_finished", "operation_failed"])
def test_document_terminal_releases_trace_with_parent_request_id(tmp_path, terminal):
    from app.services.diagnostics.recorder import DiagnosticRecorder
    from app.services.diagnostics.schema import DiagnosticLimits
    from app.services.diagnostics.store import DiagnosticStore

    session = view(tmp_path, "detailed")
    session = replace(session, policy=replace(session.policy, max_inflight_traces=1,
                                              trace_limit_per_second=10))
    recorder = DiagnosticRecorder(DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)),
                                  capture_selector=lambda *_: session.session_id,
                                  capture_view_selector=lambda *_: session)
    first = DiagnosticContext(request_id=str(uuid4()), operation_id=str(uuid4()), operation_kind="document")
    second = DiagnosticContext(request_id=str(uuid4()), operation_id=str(uuid4()), operation_kind="document")
    assert recorder.begin_trace(first)
    assert not recorder.begin_trace(second)
    assert recorder.emit(terminal, context=first, fields={"stage": "parse", "duration_ms": 10})
    assert recorder.begin_trace(second)


def test_search_operation_terminal_keeps_request_trace_until_http_finish(tmp_path):
    from app.services.diagnostics.recorder import DiagnosticRecorder
    from app.services.diagnostics.schema import DiagnosticLimits
    from app.services.diagnostics.store import DiagnosticStore

    session = view(tmp_path, "detailed")
    session = replace(session, policy=replace(session.policy, max_inflight_traces=1,
                                              trace_limit_per_second=10))
    recorder = DiagnosticRecorder(DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)),
                                  capture_selector=lambda *_: session.session_id,
                                  capture_view_selector=lambda *_: session)
    first = DiagnosticContext(request_id=str(uuid4()), operation_id=str(uuid4()), operation_kind="search_chat")
    second = DiagnosticContext(request_id=str(uuid4()), operation_kind="search_chat")
    assert recorder.begin_trace(first)
    assert recorder.emit("operation_finished", context=first, fields={"stage": "search", "duration_ms": 10})
    assert not recorder.begin_trace(second)
    assert recorder.emit("request_finished", context=first,
                         fields={"route_template": "/api/search", "http_status": 200, "duration_ms": 10})
    assert recorder.begin_trace(second)


def test_configured_aggregate_interval_flushes_while_record_queue_stays_busy(tmp_path, monkeypatch):
    from app.services.diagnostics.recorder import DiagnosticRecorder
    from app.services.diagnostics.schema import DiagnosticLimits
    from app.services.diagnostics.store import DiagnosticStore

    session = view(tmp_path)
    session = replace(session, policy=replace(session.policy, aggregate_interval_ms=1000))
    recorder = DiagnosticRecorder(DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)),
                                  capture_selector=lambda *_: session.session_id,
                                  capture_view_selector=lambda *_: session)
    clock = [0.0]
    recorder.monotonic = lambda: clock[0]
    assert recorder.emit("request_finished", fields={"route_template": "/api/search",
                         "http_status": 200, "duration_ms": 10})
    context = DiagnosticContext(operation_id=str(uuid4()), operation_kind="document")
    for _ in range(5):
        assert recorder.emit("operation_finished", context=context, fields={"stage": "parse", "duration_ms": 10})
    def write(*_):
        clock[0] += 0.4
        return False
    monkeypatch.setattr(recorder, "_record_to_stream", write)
    flushed = []
    flush = recorder._flush_aggregates
    def capture_flush(*args, **kwargs):
        flushed.append(clock[0])
        return flush(*args, **kwargs)
    monkeypatch.setattr(recorder, "_flush_aggregates", capture_flush)
    monkeypatch.setattr(recorder, "_flush_batches", lambda **_: None)
    monkeypatch.setattr(recorder, "_checkpoint_counts", lambda: None)
    recorder._stop.set()
    recorder._run()
    assert 1.0 <= flushed[0] < 2.0
