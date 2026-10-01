import json
from uuid import uuid4

from app.services.diagnostics.context import bind_context, new_operation
from app.services.diagnostics.recorder import DiagnosticRecorder, set_recorder
from app.services.diagnostics.schema import DiagnosticContext, DiagnosticLimits
from app.services.diagnostics.store import DiagnosticStore
from app.services.registry import DocumentRegistry
from tests.test_generation_pipeline import pipeline_env as _pipeline_fixture, DOC_ID
from tests.test_diagnostics_sessions import service as _session_fixture, actor

pipeline_env = _pipeline_fixture
session_service = _session_fixture


def _events(store, session_id):
    return [json.loads(line) for path in (store.root / "events" / session_id).glob("*.jsonl")
            for line in path.read_text().splitlines()]


def test_pipeline_stages_have_ids_counts_and_no_source_content(pipeline_env, tmp_path):
    from app.db.models import DocumentGenerationState
    from app.db.session import session_scope
    pipeline, source, write_source = pipeline_env
    write_source("CANARY_DOCUMENT_PRIVATE_CONTENT " * 30)
    session_id = str(uuid4())
    root = tmp_path.parent / (tmp_path.name + "-diagnostics")
    store = DiagnosticStore(root, DiagnosticLimits(min_free_bytes=0))
    recorder = DiagnosticRecorder(store, capture_selector=lambda *_: session_id)
    context = new_operation(DiagnosticContext(request_id=str(uuid4())), doc_id=DOC_ID)
    recorder.start()
    set_recorder(recorder)
    try:
        with bind_context(context):
            pipeline._process(DOC_ID, source, "CANARY_PRIVATE_FILENAME.eml", [], resume=False)
    finally:
        set_recorder(None)
        recorder.stop()
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
    events = _events(store, session_id)
    assert {"operation_started", "operation_finished", "stage_started", "stage_finished"} <= {
        event["event_code"] for event in events
    }
    with session_scope() as db:
        generation_id = db.get(DocumentGenerationState, DOC_ID).active_generation_id
    stages = [event for event in events if event["event_code"] == "stage_finished"]
    assert {"parse", "generate", "index", "publish"} <= {event["stage"] for event in stages}
    assert all(event["operation_id"] == context.operation_id for event in events)
    assert all(event["request_id"] == context.request_id for event in events)
    assert all(event["doc_id"] == DOC_ID for event in events)
    assert all(event["generation_id"] == generation_id for event in stages)
    assert all(event["duration_ms"] >= 0 for event in stages)
    assert any(event.get("chunk_index") == 0 and event.get("counts", {}).get("concepts", 0) > 0
               for event in stages if event["stage"] == "generate")
    assert "CANARY" not in json.dumps(events)


def test_document_scope_excludes_other_document(session_service):
    target, other = "0123456789abcdef", "fedcba9876543210"
    registry = DocumentRegistry()
    for doc_id in (target, other):
        registry.create(doc_id, "private.docx", "docx", 1)
    captured = session_service.start("document", 5, actor(), doc_id=target)
    recorder = DiagnosticRecorder(session_service.store, capture_selector=session_service.active_for)
    recorder.start()
    assert recorder.emit("stage_started", context=new_operation(DiagnosticContext(), doc_id=target),
                         fields={"stage": "parse"})
    assert not recorder.emit("stage_started", context=new_operation(DiagnosticContext(), doc_id=other),
                             fields={"stage": "parse"})
    recorder.stop()
    events = _events(session_service.store, captured.id)
    assert len(events) == 1 and events[0]["doc_id"] == target


def test_capture_started_mid_operation_records_only_new_events(session_service):
    context = new_operation(DiagnosticContext(request_id=str(uuid4())), operation_kind="search_chat")
    recorder = DiagnosticRecorder(session_service.store, capture_selector=session_service.active_for)
    recorder.start()
    assert not recorder.emit("stage_started", context=context, fields={"stage": "search"})
    captured = session_service.start("search_chat", 5, actor())
    assert recorder.emit("stage_finished", context=context, fields={"stage": "search", "duration_ms": 100})
    recorder.stop()
    events = _events(session_service.store, captured.id)
    assert len(events) == 1
    assert events[0]["event_code"] == "stage_finished"
    assert events[0]["operation_id"] == context.operation_id


def test_failed_finalization_does_not_report_operation_finished(pipeline_env, tmp_path, monkeypatch):
    pipeline, source, write_source = pipeline_env
    write_source("Updated synthetic source " * 30)
    session_id = str(uuid4())
    store = DiagnosticStore(tmp_path.parent / (tmp_path.name + "-diagnostics"), DiagnosticLimits(min_free_bytes=0))
    recorder = DiagnosticRecorder(store, capture_selector=lambda *_: session_id)
    def fail(*args, **kwargs):
        raise ValueError("CANARY_STORAGE_PRIVATE_DATA")
    monkeypatch.setattr(pipeline.vector_store, "index_concepts", fail)
    recorder.start()
    set_recorder(recorder)
    try:
        pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    finally:
        set_recorder(None)
        recorder.stop()
    assert pipeline.registry.get(DOC_ID)["status"] == "failed"
    events = _events(store, session_id)
    assert sum(event["event_code"] == "operation_failed" for event in events) == 1
    assert not any(event["event_code"] == "operation_finished" for event in events)
    assert "CANARY" not in repr(events)
