"""Тесты короткого замыкания /chat при пустом результате (Этап 4a.1)."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(monkeypatch) -> TestClient:
    settings = Settings(_env_file=None, auth_provider="disabled")
    for mod in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{mod}.get_settings", lambda: settings)
    return TestClient(create_app())


class TestChatEmptyShortCircuit:
    def test_empty_hits_returns_canned_answer_without_llm(self, monkeypatch):
        from app.api import chat as chat_module

        monkeypatch.setattr(chat_module._get_embedder(), "embed", lambda *a, **k: [0.0] * 10)
        monkeypatch.setattr(
            chat_module._get_vector_store(), "search_composite", lambda **kw: []
        )

        called = []

        def boom(*a, **k):
            called.append(True)
            raise RuntimeError("LLM must not be called on empty hits")

        monkeypatch.setattr(chat_module._get_llm(), "chat", boom)

        resp = make_client(monkeypatch).post(
            "/api/chat", json={"query": "тест", "tags": ["12010"]}
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["sources"] == []
        assert "Источники не найдены" in data["answer"]
        assert called == []


def _block():
    return {
        "title": "Концепт", "filepath": "doc/concept.md", "score": 1.0, "tags": [],
        "doc_id": "0123456789abcdef", "content": "тест", "point_type": "concept", "chunk_index": 0,
    }


def _patch_retrieval(monkeypatch, chat_module, *, hits):
    monkeypatch.setattr(chat_module._get_embedder(), "embed", lambda *a, **k: [0.0] * 10)
    monkeypatch.setattr(chat_module._get_vector_store(), "search_composite", lambda **kw: hits)
    if hits:
        monkeypatch.setattr(chat_module, "load_visible_retrieval_hits", lambda h, **kw: (h, {}))
        monkeypatch.setattr(chat_module, "merge_and_format", lambda *a, **kw: [_block()])
        for name in ("drop_unmatched_blocks", "drop_partial_title_matches", "limit_context"):
            monkeypatch.setattr(chat_module, name, lambda merged, *a, **kw: merged)
        monkeypatch.setattr(chat_module, "format_context", lambda *a, **kw: "ctx")


class TestChatAdmission:
    """Чат держит поток пула до ответа LLM: лишние запросы отклоняются сразу, а не копятся."""

    def _client(self, monkeypatch, **overrides):
        from app.api import chat as chat_module

        settings = Settings(_env_file=None, auth_provider="disabled", **overrides)
        monkeypatch.setattr("app.api.chat.get_settings", lambda: settings)
        monkeypatch.setattr(chat_module, "_inflight_by_limit", {})
        return make_client(monkeypatch)

    def test_request_over_inflight_limit_is_rejected_immediately(self, monkeypatch):
        from app.api import chat as chat_module

        client = self._client(monkeypatch, chat_max_inflight=1)
        _patch_retrieval(monkeypatch, chat_module, hits=[])
        held = chat_module._admit_chat(1)
        try:
            resp = client.post("/api/chat", json={"query": "тест"})
        finally:
            held.release()

        assert resp.status_code == 429, resp.text
        assert resp.json()["code"] == "rate_limited"
        assert int(resp.headers["retry-after"]) >= 1
        # Слот освободился — следующий запрос проходит.
        assert client.post("/api/chat", json={"query": "тест"}).status_code == 200

    def test_slot_is_released_when_request_fails(self, monkeypatch):
        from app.api import chat as chat_module

        client = self._client(monkeypatch, chat_max_inflight=1)
        _patch_retrieval(monkeypatch, chat_module, hits=[])

        def fail(*a, **k):
            raise RuntimeError("embed failed")

        monkeypatch.setattr(chat_module._get_embedder(), "embed", fail)
        assert client.post("/api/chat", json={"query": "тест"}).status_code == 500

        _patch_retrieval(monkeypatch, chat_module, hits=[])
        assert client.post("/api/chat", json={"query": "тест"}).status_code == 200

    def test_busy_llm_pool_returns_429_without_history(self, monkeypatch):
        from app.api import chat as chat_module
        from app.services import chat_history
        from app.services.llm_client import LLMBusyError

        client = self._client(monkeypatch)
        _patch_retrieval(monkeypatch, chat_module, hits=[{"id": "p1"}])

        def busy(*a, **k):
            raise LLMBusyError("все слоты заняты", retry_after=7)

        monkeypatch.setattr(chat_module._get_llm(), "chat", busy)
        stored = []
        monkeypatch.setattr(chat_history, "store_turn", lambda *a, **k: stored.append(a))

        resp = client.post("/api/chat", json={"query": "тест"})

        assert resp.status_code == 429, resp.text
        assert resp.json()["code"] == "rate_limited"
        assert resp.headers["retry-after"] == "7"
        assert stored == []


def test_chat_keeps_retrieved_sources_after_context_budget(monkeypatch):
    from app.api import chat as chat_module

    _patch_retrieval(monkeypatch, chat_module, hits=[{"id": "p1"}])
    first = {**_block(), "title": "Письмо", "filepath": "doc/mail.md"}
    second = {**_block(), "title": "Нужный справочник", "filepath": "doc/reference.md"}
    monkeypatch.setattr(chat_module, "merge_and_format", lambda *a, **k: [first, second])
    monkeypatch.setattr(chat_module, "limit_context", lambda blocks, *a, **k: blocks[:1])
    monkeypatch.setattr(chat_module._get_llm(), "chat", lambda *a: "Ответ [1]")

    response = make_client(monkeypatch).post(
        "/api/chat", json={"query": "справочник", "top_k": 10, "use_glossary": False}
    )
    assert response.status_code == 200, response.text
    assert [source["title"] for source in response.json()["sources"]] == [
        "Письмо", "Нужный справочник",
    ]
    assert [source["in_model_context"] for source in response.json()["sources"]] == [True, False]
    from app.db.models import ChatMessage
    from app.db.session import session_scope
    with session_scope() as session:
        saved = session.query(ChatMessage).filter_by(role="assistant").order_by(ChatMessage.id.desc()).first()
        assert [source["in_model_context"] for source in saved.sources] == [True, False]


@pytest.mark.parametrize("profile", ["standard", "local_qwen"])
def test_chat_source_list_is_not_cut_by_model_context_budget(monkeypatch, profile):
    from app.api import chat as chat_module
    from app.services.fusion import Hit

    settings = Settings(
        _env_file=None, auth_provider="disabled", llm_profile=profile,
        chat_max_context_chars=1000,
    )
    hits = [
        Hit(
            f"point-{i}", 0.9 - i * 0.1,
            {
                "point_type": "concept", "doc_id": "0123456789abcdef",
                "chunk_index": i, "slug": f"source-{i}", "title": f"ЭЛН блок {i}",
                "content": "ЭЛН " + "текст " * 100, "tags": [],
                "filepath": f"0123456789abcdef/source-{i}.md",
            },
        )
        for i in range(3)
    ]
    prompts = []
    client = make_client(monkeypatch)
    monkeypatch.setattr(chat_module, "get_settings", lambda: settings)
    monkeypatch.setattr(chat_module, "_get_embedder", lambda: SimpleNamespace(embed=lambda _: [0.0]))
    monkeypatch.setattr(
        chat_module, "_get_vector_store",
        lambda: SimpleNamespace(search_composite=lambda **_: hits),
    )
    monkeypatch.setattr(chat_module, "load_visible_retrieval_hits", lambda found, **_: (found, {}))
    monkeypatch.setattr(
        chat_module, "_get_llm",
        lambda: SimpleNamespace(chat=lambda system, user: prompts.append(user) or "Ответ [1]"),
    )
    monkeypatch.setattr(chat_module.chat_history, "store_turn", lambda *a, **k: "session-1")
    monkeypatch.setattr(chat_module.get_rate_limiter(), "check_action", lambda *a, **k: None)

    response = client.post(
        "/api/chat", json={"query": "ЭЛН", "top_k": 21, "use_glossary": False}
    )
    assert response.status_code == 200, response.text
    sources = response.json()["sources"]
    assert [source["title"] for source in sources] == [
        "ЭЛН блок 0", "ЭЛН блок 1", "ЭЛН блок 2",
    ]
    assert [source["in_model_context"] for source in sources] == [True, False, False]
    assert len(prompts) == 1
    assert "ЭЛН блок 0" in prompts[0]
    assert "ЭЛН блок 1" not in prompts[0]
