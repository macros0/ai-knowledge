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
    monkeypatch.setattr(chat_module, "_validate_final_source_state", lambda blocks: None)
    monkeypatch.setattr(chat_module._get_embedder(), "embed", lambda *a, **k: [0.0] * 10)
    monkeypatch.setattr(chat_module._get_vector_store(), "search_composite", lambda **kw: hits)
    if hits:
        monkeypatch.setattr(chat_module, "load_visible_retrieval_hits", lambda h, **kw: (h, {}))
        monkeypatch.setattr(chat_module, "merge_and_format", lambda *a, **kw: [_block()])
        for name in ("drop_unmatched_blocks", "drop_partial_title_matches", "limit_context"):
            monkeypatch.setattr(chat_module, name, lambda merged, *a, **kw: merged)
        monkeypatch.setattr(chat_module, "format_context", lambda *a, **kw: "ctx")


def test_documents_mode_lists_all_hits_without_generation(monkeypatch):
    from app.api import chat as chat_module

    _patch_retrieval(monkeypatch, chat_module, hits=[{"id": "p1"}])
    first = {**_block(), "title": "Раздел A", "doc_id": "doc-a", "source_slug": "section-a"}
    second = {**_block(), "title": "Раздел B", "doc_id": "doc-b", "source_slug": "section-b"}
    monkeypatch.setattr(chat_module, "merge_and_format", lambda *a, **k: [first, second])
    monkeypatch.setattr(chat_module, "_get_llm", lambda: (_ for _ in ()).throw(AssertionError("LLM used")))
    response = make_client(monkeypatch).post(
        "/api/chat", json={"query": "справочник", "response_mode": "documents", "top_k": 1}
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["sources"]) == 2
    assert [src["source_slug"] for src in response.json()["sources"]] == ["section-a", "section-b"]
    assert response.json()["response_mode"] == "documents"
    assert all(not src["in_model_context"] for src in response.json()["sources"])


def test_fast_mode_calls_llm_once_and_shows_unchosen_source(monkeypatch):
    from app.api import chat as chat_module

    _patch_retrieval(monkeypatch, chat_module, hits=[{"id": "p1"}])
    first = {**_block(), "doc_id": "doc-a", "content": "x" * 200, "source_slug": "section-a"}
    second = {**_block(), "doc_id": "doc-b", "content": "y" * 200, "source_slug": "section-b"}
    monkeypatch.setattr(chat_module, "merge_and_format", lambda *a, **k: [first, second])
    monkeypatch.setattr(chat_module, "format_context", lambda blocks, **kw: "\n".join(b["content"] for b in blocks))
    monkeypatch.setattr("app.api.chat.ChatTokenBudget.fits", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(chat_module._get_llm(), "chat", lambda *a, **k: calls.append(a) or "Ответ [1]")
    client = make_client(monkeypatch)
    monkeypatch.setattr(chat_module, "get_settings", lambda: Settings(
        _env_file=None, auth_provider="disabled", chat_max_context_chars=300,
    ))
    response = client.post("/api/chat", json={"query": "справочник", "response_mode": "fast", "top_k": 1})
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert len(response.json()["sources"]) == 2
    assert [src["source_slug"] for src in response.json()["sources"]] == ["section-a", "section-b"]
    assert [source["in_model_context"] for source in response.json()["sources"]] == [True, False]


def test_fast_authorship_uses_only_selected_canonical_excerpt(monkeypatch):
    from app.api import chat as chat_module

    _patch_retrieval(monkeypatch, chat_module, hits=[{"id": "p1"}])
    first = {**_block(), "doc_id": "doc-a", "content": "summary one"}
    second = {**_block(), "doc_id": "doc-b", "content": "summary two"}
    monkeypatch.setattr(chat_module, "merge_and_format", lambda *a, **k: [first, second])
    monkeypatch.setattr(chat_module, "load_authorship_evidence", lambda *a, **k: [
        {"index": 1, "text": "Автор: Иванов\n" + "A" * 80},
        {"index": 2, "text": "Автор: Петров\n" + "B" * 80},
    ])
    monkeypatch.setattr("app.api.chat.ChatTokenBudget.fits", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(chat_module._get_llm(), "chat", lambda *a: calls.append(a) or
                        '{"quotes":[{"source":1,"quote":"Автор: Иванов"}]}')
    client = make_client(monkeypatch)
    monkeypatch.setattr(chat_module, "get_settings", lambda: Settings(
        _env_file=None, auth_provider="disabled", chat_max_context_chars=250,
    ))
    response = client.post("/api/chat", json={"query": "кто автор", "response_mode": "fast"})
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert "Автор: Иванов" in calls[0][1]
    assert "Автор: Петров" not in calls[0][1]
    assert response.json()["sources"][0]["cited"] is True
    assert response.json()["sources"][1]["in_model_context"] is False


def test_fast_rejects_citation_to_search_only_source(monkeypatch):
    from app.api import chat as chat_module

    _patch_retrieval(monkeypatch, chat_module, hits=[{"id": "p1"}])
    first = {**_block(), "doc_id": "doc-a", "content": "x" * 200}
    second = {**_block(), "doc_id": "doc-b", "content": "y" * 200}
    monkeypatch.setattr(chat_module, "merge_and_format", lambda *a, **k: [first, second])
    monkeypatch.setattr(chat_module, "format_context", lambda blocks, **kw: "\n".join(b["content"] for b in blocks))
    monkeypatch.setattr("app.api.chat.ChatTokenBudget.fits", lambda *a, **k: True)
    monkeypatch.setattr(chat_module._get_llm(), "chat", lambda *a: "Ответ [2]")
    client = make_client(monkeypatch)
    monkeypatch.setattr(chat_module, "get_settings", lambda: Settings(
        _env_file=None, auth_provider="disabled", chat_max_context_chars=300,
    ))
    response = client.post("/api/chat", json={"query": "справочник", "response_mode": "fast"})
    assert response.status_code == 422
    assert response.json()["code"] == "chat_evidence_invalid"


def test_full_mode_processes_two_batches_and_synthesizes(monkeypatch):
    from app.api import chat as chat_module

    _patch_retrieval(monkeypatch, chat_module, hits=[{"id": "p1"}])
    first = {**_block(), "doc_id": "doc-a", "content": "AAA " * 90}
    second = {**_block(), "doc_id": "doc-b", "content": "BBB " * 90}
    monkeypatch.setattr(chat_module, "merge_and_format", lambda *a, **k: [first, second])
    monkeypatch.setattr(chat_module, "format_context", lambda blocks, **kw: "\n".join(
        f'<context_block id="{b["_source_index"]}">{b["content"]}</context_block>' for b in blocks
    ))
    monkeypatch.setattr("app.api.chat.ChatTokenBudget.fits", lambda *a, **k: True)
    calls = []

    def complete(system, user):
        calls.append((system, user))
        if len(calls) == 1:
            return '{"facts":[{"text":"A","source":1,"quote":"AAA"}]}'
        if len(calls) == 2:
            return '{"facts":[{"text":"B","source":2,"quote":"BBB"}]}'
        return "A [1], B [2]"

    monkeypatch.setattr(chat_module._get_llm(), "chat", complete)
    client = make_client(monkeypatch)
    monkeypatch.setattr(chat_module, "get_settings", lambda: Settings(
        _env_file=None, auth_provider="disabled", chat_max_context_chars=550,
    ))
    response = client.post("/api/chat", json={"query": "сравни", "response_mode": "full", "top_k": 1})
    assert response.status_code == 200, response.text
    assert len(calls) == 3
    assert len(response.json()["sources"]) == 2
    assert [s["completed_parts"] for s in response.json()["sources"]] == [1, 1]


def test_full_mode_repairs_invalid_fact_once(monkeypatch):
    from app.api import chat as chat_module

    _patch_retrieval(monkeypatch, chat_module, hits=[{"id": "p1"}])
    first = {**_block(), "doc_id": "doc-a", "content": "AAA " * 90}
    second = {**_block(), "doc_id": "doc-b", "content": "BBB " * 90}
    monkeypatch.setattr(chat_module, "merge_and_format", lambda *a, **k: [first, second])
    monkeypatch.setattr(chat_module, "format_context", lambda blocks, **kw: "\n".join(
        f'<context_block id="{b["_source_index"]}">{b["content"]}</context_block>' for b in blocks
    ))
    monkeypatch.setattr("app.api.chat.ChatTokenBudget.fits", lambda *a, **k: True)
    replies = iter([
        '{"facts":[{"text":"wrong","source":1,"quote":"invented"}]}',
        '{"facts":[{"text":"A","source":1,"quote":"AAA"}]}',
        '{"facts":[{"text":"B","source":2,"quote":"BBB"}]}',
        "A [1], B [2]",
    ])
    calls = []
    monkeypatch.setattr(chat_module._get_llm(), "chat", lambda *a: calls.append(a) or next(replies))
    client = make_client(monkeypatch)
    monkeypatch.setattr(chat_module, "get_settings", lambda: Settings(
        _env_file=None, auth_provider="disabled", chat_max_context_chars=550,
    ))
    response = client.post("/api/chat", json={"query": "сравни", "response_mode": "full"})
    assert response.status_code == 200, response.text
    assert len(calls) == 4
    assert response.json()["answer"] == "A [1], B [2]"


def test_full_mode_does_not_complete_on_twice_invalid_fact(monkeypatch):
    from app.api import chat as chat_module

    _patch_retrieval(monkeypatch, chat_module, hits=[{"id": "p1"}])
    first = {**_block(), "doc_id": "doc-a", "content": "AAA " * 90}
    second = {**_block(), "doc_id": "doc-b", "content": "BBB " * 90}
    monkeypatch.setattr(chat_module, "merge_and_format", lambda *a, **k: [first, second])
    monkeypatch.setattr(chat_module, "format_context", lambda blocks, **kw: "\n".join(
        f'<context_block id="{b["_source_index"]}">{b["content"]}</context_block>' for b in blocks
    ))
    monkeypatch.setattr("app.api.chat.ChatTokenBudget.fits", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(chat_module._get_llm(), "chat", lambda *a: calls.append(a) or "broken")
    client = make_client(monkeypatch)
    monkeypatch.setattr(chat_module, "get_settings", lambda: Settings(
        _env_file=None, auth_provider="disabled", chat_max_context_chars=550,
    ))
    response = client.post("/api/chat", json={"query": "сравни", "response_mode": "full"})
    assert response.status_code >= 400
    assert len(calls) == 2


def test_full_mode_checks_stop_before_next_llm_call(monkeypatch):
    from app.api import chat as chat_module

    _patch_retrieval(monkeypatch, chat_module, hits=[{"id": "p1"}])
    first = {**_block(), "doc_id": "doc-a", "content": "AAA " * 90}
    second = {**_block(), "doc_id": "doc-b", "content": "BBB " * 90}
    monkeypatch.setattr(chat_module, "merge_and_format", lambda *a, **k: [first, second])
    monkeypatch.setattr(chat_module, "format_context", lambda blocks, **kw: "\n".join(
        f'<context_block id="{b["_source_index"]}">{b["content"]}</context_block>' for b in blocks
    ))
    monkeypatch.setattr("app.api.chat.ChatTokenBudget.fits", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(chat_module._get_llm(), "chat", lambda *a: calls.append(a) or
                        '{"facts":[{"text":"A","source":1,"quote":"AAA"}]}')
    monkeypatch.setattr(chat_module.chat_history, "attempt_status",
                        lambda *a: "stopped" if calls else "incomplete")
    client = make_client(monkeypatch)
    monkeypatch.setattr(chat_module, "get_settings", lambda: Settings(
        _env_file=None, auth_provider="disabled", chat_max_context_chars=550,
    ))
    response = client.post("/api/chat/stream", json={"query": "сравни", "response_mode": "full"})
    assert response.status_code == 200
    assert len(calls) == 1
    assert '"type": "result"' not in response.text


def test_full_mode_splits_truncated_table_batch(monkeypatch):
    from app.api import chat as chat_module
    from app.services.llm_client import LLMTruncationError

    _patch_retrieval(monkeypatch, chat_module, hits=[{"id": "p1"}])
    table = {**_block(), "doc_id": "doc-a", "content": "A row\nB row\nC row\nD row\n"}
    other = {**_block(), "doc_id": "doc-b", "content": "Other source\n"}
    monkeypatch.setattr(chat_module, "merge_and_format", lambda *a, **k: [table, other])
    monkeypatch.setattr(chat_module, "format_context", lambda blocks, **kw: "\n".join(b["content"] for b in blocks))
    monkeypatch.setattr("app.api.chat.ChatTokenBudget.fits", lambda *a, **k: True)
    calls = []

    def complete(system, user):
        calls.append((system, user))
        if "Extract only facts" in system and "A row" in user and "B row" in user:
            raise LLMTruncationError("cut")
        if "Extract only facts" in system:
            quote = "A row" if "A row" in user else "B row" if "B row" in user else "C row" if "C row" in user else "D row" if "D row" in user else "Other source"
            index = 2 if quote == "Other source" else 1
            return f'{{"facts":[{{"text":"{quote}","source":{index},"quote":"{quote}"}}]}}'
        return "Ответ [1] [2]"

    monkeypatch.setattr(chat_module._get_llm(), "chat", complete)
    client = make_client(monkeypatch)
    monkeypatch.setattr(chat_module, "get_settings", lambda: Settings(
        _env_file=None, auth_provider="disabled", chat_max_context_chars=30,
    ))
    response = client.post("/api/chat", json={"query": "что в таблице", "response_mode": "full"})
    assert response.status_code == 200, response.text
    assert any("A row" in user and "B row" not in user for system, user in calls if "Extract only facts" in system)
    assert response.json()["sources"][0]["completed_parts"] > 1


def test_full_authorship_splits_truncated_canonical_batch(monkeypatch):
    from app.api import chat as chat_module
    from app.services.llm_client import LLMTruncationError

    _patch_retrieval(monkeypatch, chat_module, hits=[{"id": "p1"}])
    first = {**_block(), "doc_id": "doc-a", "content": "summary A"}
    second = {**_block(), "doc_id": "doc-b", "content": "summary B"}
    monkeypatch.setattr(chat_module, "merge_and_format", lambda *a, **k: [first, second])
    monkeypatch.setattr(chat_module, "load_authorship_evidence", lambda *a, **k: [
        {"index": 1, "text": "A row\nB row\nC row\nD row\n"},
        {"index": 2, "text": "Other source\n"},
    ])
    monkeypatch.setattr("app.api.chat.ChatTokenBudget.fits", lambda *a, **k: True)
    calls = []

    def complete(system, user):
        calls.append(user)
        if "A row" in user and "B row" in user:
            raise LLMTruncationError("cut")
        quote = "A row" if "A row" in user else "B row" if "B row" in user else "C row" if "C row" in user else "D row" if "D row" in user else "Other source"
        source = 2 if quote == "Other source" else 1
        return f'{{"quotes":[{{"source":{source},"quote":"{quote}"}}]}}'

    monkeypatch.setattr(chat_module._get_llm(), "chat", complete)
    client = make_client(monkeypatch)
    monkeypatch.setattr(chat_module, "get_settings", lambda: Settings(
        _env_file=None, auth_provider="disabled", chat_max_context_chars=110,
    ))
    response = client.post("/api/chat", json={"query": "кто автор", "response_mode": "full"})
    assert response.status_code == 200, response.text
    assert response.json()["sources"][0]["completed_parts"] > 1
    assert any("A row" in call and "B row" not in call for call in calls)


def test_documents_stream_persists_sources_and_attempt_identity(monkeypatch):
    import json
    from uuid import uuid4
    from app.api import chat as chat_module
    from app.services import chat_history

    _patch_retrieval(monkeypatch, chat_module, hits=[{"id": "p1"}])
    attempt_id = str(uuid4())
    session_id = str(uuid4())
    response = make_client(monkeypatch).post(
        "/api/chat/stream", json={
            "query": "справочник", "response_mode": "documents",
            "attempt_id": attempt_id, "session_id": session_id,
        },
    )
    assert response.status_code == 200, response.text
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events if event["type"] != "ping"] == ["start", "sources", "result"]
    assert all(event["attempt_id"] == attempt_id for event in events)
    thread = chat_history.get_thread(session_id, "anonymous")
    assert len(thread["messages"][-1]["sources"]) == 1
    assert thread["messages"][-1]["retrieval_metadata"]["answer_attempt"]["status"] == "completed"


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
