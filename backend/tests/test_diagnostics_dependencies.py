import json
from uuid import uuid4

import pytest

from app.services.diagnostics.context import bind_context, current_context, new_operation
from app.services.diagnostics.recorder import DiagnosticRecorder, set_recorder
from app.services.diagnostics.schema import DiagnosticContext, DiagnosticLimits
from app.services.diagnostics.store import DiagnosticStore


@pytest.fixture
def recording(tmp_path):
    session_id = str(uuid4())
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    recorder = DiagnosticRecorder(store, capture_selector=lambda *_: session_id)
    recorder.start()
    set_recorder(recorder)
    try:
        yield recorder, store, session_id
    finally:
        set_recorder(None)
        recorder.stop()


def _captured_events(recording):
    # Flush before assertions without depending on timing sleeps.
    recorder, store, session_id = recording
    recorder.stop()
    paths = (store.root / "events" / session_id).glob("*.jsonl")
    return [json.loads(line) for path in paths for line in path.read_text().splitlines()]


def test_llm_daemon_keeps_context_and_call_metadata(recording, monkeypatch, tmp_path):
    import litellm
    from app.services import llm_client
    from tests.test_llm_client import _settings, _stream_response_finished
    monkeypatch.setattr(llm_client, "get_settings", lambda: _settings(llm_max_concurrency=0))
    observed = []
    def complete(**kwargs):
        observed.append(current_context())
        return _stream_response_finished("CANARY_PRIVATE_RESPONSE")
    monkeypatch.setattr(litellm, "completion", complete)
    context = new_operation(DiagnosticContext(request_id=str(uuid4())), doc_id="0123456789abcdef")
    with bind_context(context):
        assert llm_client.LLMClient()._complete_once("CANARY_PROMPT", "CANARY_DOCUMENT")[0] == "CANARY_PRIVATE_RESPONSE"
    assert observed == [context]
    events = _captured_events(recording)
    calls = [event for event in events if event["event_code"] == "dependency_call_finished"]
    assert any(event["dependency"] == "llm" and event["operation_id"] == context.operation_id for event in calls)
    assert "CANARY" not in repr(events)


def test_qdrant_and_embedding_metadata_no_payload(recording, monkeypatch, tmp_path):
    from app.config import Settings
    from app.services import embedder, vector_store
    monkeypatch.setattr(embedder, "get_settings", lambda: Settings(_env_file=None, embedding_provider="fake"))
    context = new_operation(DiagnosticContext(request_id=str(uuid4())), operation_kind="search_chat")
    with bind_context(context):
        assert embedder.Embedder().embed("CANARY_PRIVATE_SEARCH")
        assert vector_store._qdrant_call(lambda: "CANARY_PRIVATE_SOURCE") == "CANARY_PRIVATE_SOURCE"
    events = _captured_events(recording)
    assert {"qdrant", "embeddings"} <= {event["dependency"] for event in events
                                         if event["event_code"] == "dependency_call_finished"}
    assert "CANARY" not in repr(events)


def test_llm_retry_and_parse_failure_are_safe(recording, monkeypatch, tmp_path):
    from app.services import llm_client
    from tests.test_llm_client import _settings
    monkeypatch.setattr(llm_client, "get_settings", lambda: _settings(llm_max_concurrency=0))
    client = llm_client.LLMClient()
    attempts = []
    def complete(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise llm_client.LLMTimeoutError("CANARY_PROVIDER_URL_PASSWORD")
        return "[]", "stop"
    monkeypatch.setattr(client, "_complete_once", complete)
    context = new_operation(DiagnosticContext(request_id=str(uuid4())), operation_kind="search_chat")
    with bind_context(context):
        assert client._complete_with_retries("CANARY_PROMPT", "CANARY_DOCUMENT") == ("[]", "stop")
        with pytest.raises(llm_client.LLMTruncationError):
            llm_client._parse_json("", finish_reason="length", chunk_idx=1)
    events = _captured_events(recording)
    assert any(event["event_code"] == "retry_scheduled" and event["dependency"] == "llm" for event in events)
    assert any(event["event_code"] == "llm_parse_failed" and event["chunk_index"] == 0 for event in events)
    assert "CANARY" not in repr(events)


def test_health_transitions_emit_once_without_error_text(recording, monkeypatch, tmp_path):
    from app.services import health
    monkeypatch.setattr(health, "_last_status", {})
    health._log_transitions({"qdrant": {"status": "down", "error": "CANARY_CREDENTIALS"}})
    health._log_transitions({"qdrant": {"status": "down", "error": "CANARY_CREDENTIALS"}})
    health._log_transitions({"qdrant": {"status": "ok"}})
    events = _captured_events(recording)
    transitions = [event for event in events if event["event_code"] == "dependency_status_changed"]
    assert [event["dependency_status"] for event in transitions] == ["down", "ok"]
    assert "CANARY" not in repr(events)
