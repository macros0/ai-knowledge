import asyncio
import json
from uuid import uuid4

from app.services.diagnostics.schema import DiagnosticContext


def test_multipass_sources_and_progress_keep_error_correlation(monkeypatch, tmp_path):
    from app.config import Settings
    from app.services import chat_stream
    from app.services.diagnostics.context import current_context
    from app.services.llm_profiles import request_state

    monkeypatch.setattr(chat_stream, "get_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    context = DiagnosticContext(request_id=str(uuid4()), operation_id=str(uuid4()), operation_kind="search_chat")
    observed = []

    def work():
        state = request_state()
        observed.append(current_context())
        assert state["deadline"] is None
        state["on_sources"]([{"source_index": 1}])
        state["on_progress"]({"phase": "generation", "batches_done": 1})
        raise ValueError("CANARY_PRIVATE_PROVIDER_RESPONSE")

    async def collect():
        return [json.loads(line) async for line in chat_stream.stream_chat(
            work, context=context, independent_calls=True)]

    events = asyncio.run(collect())
    assert observed == [context]
    assert [event["type"] for event in events] == ["sources", "progress", "error"]
    assert events[-1]["request_id"] == context.request_id
    assert events[-1]["code"] == "internal_error"
    assert "CANARY" not in json.dumps(events)
