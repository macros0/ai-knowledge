"""Тесты короткого замыкания /chat при пустом результате (Этап 4a.1)."""

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

        monkeypatch.setattr(chat_module._embedder, "embed", lambda *a, **k: [0.0] * 10)
        monkeypatch.setattr(
            chat_module._vector_store, "search_composite", lambda **kw: []
        )

        called = []

        def boom(*a, **k):
            called.append(True)
            raise RuntimeError("LLM must not be called on empty hits")

        monkeypatch.setattr(chat_module._llm, "chat", boom)

        resp = make_client(monkeypatch).post(
            "/api/chat", json={"query": "тест", "tags": ["12010"]}
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["sources"] == []
        assert "Источники не найдены" in data["answer"]
        assert called == []
