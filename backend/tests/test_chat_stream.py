import asyncio
import json
import threading

import pytest

from app.services.context_builder import format_context, limit_context


@pytest.fixture(autouse=True)
def isolated_stream_settings(monkeypatch, tmp_path):
    from app.config import Settings
    from app.services import chat_stream
    monkeypatch.setattr(chat_stream, "get_settings",
                        lambda: Settings(_env_file=None, data_dir=tmp_path))


def test_strict_context_budget_counts_legacy_metadata():
    blocks = [{"title": "Регламент", "content": "Факт " * 200,
               "point_type": "chunk", "tags": [], "source_filename": "x.md"} for _ in range(3)]
    result = limit_context(blocks, 1600, strict=True)
    assert result
    assert len(format_context(result)) <= 1600
    assert len(result) < len(blocks)


def test_natural_question_with_exact_code_does_not_inherit_adjacent_chunk_topic():
    from app.services.context_builder import drop_partial_title_matches
    about = {"title": "Доработка ZPRP_DISABILITY_CHLD",
             "content": "Журнал ЭЛН содержит СНИЛС и даты болезни.",
             "concept_content": "Запустить HRULAPL4 в режиме корректировки."}
    adjacent = {"title": "Журнал ЭЛН", "content": "Другая подсистема."}
    query = "Для чего используется ZPRP_DISABILITY_CHLD?"
    assert drop_partial_title_matches([about, adjacent], query) == [about, adjacent]
    result = drop_partial_title_matches([about, adjacent], query, focus_named_objects=True)
    assert [block["title"] for block in result] == [about["title"]]
    assert result[0]["content"] == about["concept_content"]
    assert about["content"].startswith("Журнал ЭЛН")


def test_named_object_focus_does_not_collapse_comparison():
    from app.services.context_builder import drop_partial_title_matches
    blocks = [{"title": "Z_OBJECT_A", "content": "one"},
              {"title": "Z_OBJECT_B", "content": "two"}]
    assert drop_partial_title_matches(
        blocks, "Сравни Z_OBJECT_A и Z_OBJECT_B", focus_named_objects=True) == blocks


@pytest.mark.parametrize("query", [
    "Опиши поля ZPRP_DISABILITY_CHLD и допустимые значения",
    "Для чего используется ZPRP_DISABILITY_CHLD по сравнению с HRULAPL4?",
])
def test_overview_focus_preserves_detailed_and_mixed_code_questions(query):
    from app.services.context_builder import drop_partial_title_matches
    blocks = [{"title": "ZPRP_DISABILITY_CHLD", "content": "full", "concept_content": "brief"},
              {"title": "Поля и HRULAPL4", "content": "details"}]
    assert drop_partial_title_matches(blocks, query, focus_named_objects=True) == blocks


def test_stream_delivers_deltas_then_authoritative_result():
    from app.services.chat_stream import stream_chat
    from app.services.llm_profiles import request_state
    from app.models.schemas import ChatResponse
    def work():
        request_state()["on_text"]("Ответ")
        return ChatResponse(query="q", answer="Ответ [1].", sources=[])
    async def collect():
        return [json.loads(line) async for line in stream_chat(work)]
    events = asyncio.run(collect())
    assert events[0] == {"type": "delta", "text": "Ответ"}
    assert events[-1]["type"] == "result"
    assert events[-1]["data"]["answer"] == "Ответ [1]."


def test_stream_failure_never_exposes_provider_text():
    from app.services.chat_stream import stream_chat
    def work():
        raise ValueError("secret provider token")
    async def collect():
        return [json.loads(line) async for line in stream_chat(work)]
    events = asyncio.run(collect())
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "internal_error"
    assert "secret" not in json.dumps(events)


def test_stream_route_retains_empty_source_short_circuit(monkeypatch):
    from app.api import chat as chat_module
    from tests.test_chat import make_client
    monkeypatch.setattr(chat_module._get_embedder(), "embed", lambda *a, **k: [0.0] * 10)
    monkeypatch.setattr(chat_module._get_vector_store(), "search_composite", lambda **kw: [])
    def forbidden(*args, **kwargs):
        pytest.fail("empty retrieval must never call the LLM")
    monkeypatch.setattr(chat_module._get_llm(), "chat", forbidden)
    response = make_client(monkeypatch).post("/api/chat/stream", json={"query": "q"})
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1]["type"] == "result"
    assert events[-1]["data"]["sources"] == []


def test_stream_close_signals_cancellation():
    from app.services.chat_stream import stream_chat
    from app.services.llm_profiles import request_state
    entered = threading.Event()
    cancelled = threading.Event()
    def work():
        state = request_state()
        state["on_text"]("start")
        entered.set()
        if state["cancel"].wait(2):
            cancelled.set()
        raise RuntimeError("cancelled")
    async def run():
        stream = stream_chat(work)
        await anext(stream)
        await stream.aclose()
        await asyncio.to_thread(cancelled.wait, 2)
    asyncio.run(run())
    assert entered.is_set() and cancelled.is_set()


def test_expired_generation_budget_prevents_another_llm_call(monkeypatch):
    import time
    from app.config import Settings
    from app.services import llm_client
    from app.services.llm_profiles import request_scope
    monkeypatch.setattr(llm_client, "get_settings", lambda: Settings(_env_file=None, llm_profile="local_qwen"))
    def forbidden(**kwargs):
        pytest.fail("a call escaped the chunk deadline")
    monkeypatch.setattr(llm_client.litellm, "completion", forbidden)
    with request_scope(deadline=time.monotonic() - 1):
        with pytest.raises(TimeoutError):
            llm_client.LLMClient().chat_json("s", "u")
