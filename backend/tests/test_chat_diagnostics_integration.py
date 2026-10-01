import asyncio
import pytest
from app.api.errors import ApiError
from app.services.diagnostics import events, middleware
from app.services.diagnostics.context import current_context
from app.services.diagnostics.sanitize import sanitize_event
from tests.test_diagnostics_schema import event

CHAT_CODES = ["chat_budget_unavailable", "chat_answer_truncated", "chat_evidence_invalid",
              "chat_sources_changed", "chat_summary_too_large", "chat_evidence_too_large",
              "chat_search_scope_empty", "chat_search_scope_unavailable"]

@pytest.mark.parametrize("code", CHAT_CODES)
def test_decorated_chat_failure_keeps_allowlisted_code(monkeypatch, code):
    recorded = []
    monkeypatch.setattr(events, "emit_event", lambda name, **kw: recorded.append((name, kw)))
    @events.observed_operation("chat")
    def work():
        raise ApiError(status_code=503, code=code, detail="CANARY_PRIVATE")
    with pytest.raises(ApiError):
        work()
    fields = [kw["fields"] for name, kw in recorded if name == "operation_failed"][0]
    assert fields["error_code"] == code
    assert sanitize_event(event(**fields)) is not None


def test_arbitrary_api_code_is_not_preserved(monkeypatch):
    recorded = []
    monkeypatch.setattr(events, "emit_event", lambda name, **kw: recorded.append(kw))
    events.record_failure(ApiError(status_code=503, code="CANARY_PRIVATE", detail="CANARY"))
    assert recorded[0]["fields"]["error_code"] == "dependency_unavailable"

@pytest.mark.parametrize("path, template", [
    ("/api/chat/search-scope", "/api/chat/search-scope"),
    ("/api/chat/attempts/CANARY_PRIVATE/cancel", "/api/chat/attempts/{attempt_id}/cancel"),
])
def test_new_chat_routes_are_captured_without_raw_parameters(monkeypatch, path, template):
    recorded, kinds = [], []
    monkeypatch.setattr(middleware, "emit_event", lambda name, **kw: recorded.append(kw))
    monkeypatch.setattr(middleware, "begin_trace", lambda context: None)
    async def app(scope, receive, send):
        kinds.append(current_context().operation_kind)
        await send({"type": "http.response.start", "status": 200, "headers": []})
    async def receive():
        raise AssertionError("body read")
    async def send(message):
        pass
    asyncio.run(middleware.DiagnosticContextMiddleware(app)(
        {"type": "http", "path": path, "method": "POST", "headers": []}, receive, send))
    assert kinds == ["search_chat"]
    assert recorded[0]["fields"]["route_template"] == template
