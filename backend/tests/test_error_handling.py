"""Тесты centralized exception handler и /health endpoint.

Проверяют:
  - DependencyUnavailableError -> 503 JSON с code/service
  - /health возвращает статус зависимостей
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.errors import DependencyUnavailableError, EmbedderError, LLMError, VectorStoreError


def test_error_classes_inherit_base():
    assert issubclass(LLMError, DependencyUnavailableError)
    assert issubclass(EmbedderError, DependencyUnavailableError)
    assert issubclass(VectorStoreError, DependencyUnavailableError)


def test_error_service_identifiers():
    assert LLMError().service == "llm"
    assert EmbedderError().service == "ollama"
    assert VectorStoreError().service == "qdrant"


def test_error_user_messages_are_russian():
    assert "недоступ" in LLMError().user_message.lower()
    assert "эмбеддинг" in EmbedderError().user_message.lower()
    assert "qdrant" in VectorStoreError().user_message.lower() or "база" in VectorStoreError().user_message.lower()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient с изолированными settings (без Qdrant)."""
    from app.config import Settings

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        embedding_provider="fake",
        embedding_dimensions=8,
        llm_model="openai/test",
        llm_base_url="http://localhost",
        llm_api_key="key",
        auth_provider="disabled",
    )
    monkeypatch.setattr("app.config.get_settings", lambda: settings)

    from app.main import create_app

    app = create_app()

    return TestClient(app)


def test_dependency_error_returns_503_with_code_and_service(client, monkeypatch):
    """DependencyUnavailableError -> 503 JSON {detail, code, service}."""
    from app.api import chat as chat_module

    def raise_qdrant_error(text):
        raise VectorStoreError("Qdrant down", cause=ConnectionRefusedError("refused"))

    chat_module._embedder.embed = raise_qdrant_error
    try:
        resp = client.post("/api/chat", json={"query": "test", "mode": "dense"})
        assert resp.status_code == 503
        data = resp.json()
        assert data["code"] == "dependency_unavailable"
        assert data["service"] == "qdrant"
        assert "Qdrant" in data["detail"] or "недоступ" in data["detail"]
    finally:
        chat_module._embedder.embed = lambda text: [0.0] * 8


def test_llm_error_returns_503_with_llm_service(client, monkeypatch):
    """LLM RuntimeError оборачивается в LLMError в chat.py → 503 dependency_unavailable."""
    from app.api import chat as chat_module
    from app.services.fusion import Hit

    def raise_runtime(system, user, **kw):
        raise RuntimeError("OpenRouter 502")

    chunk_hit = Hit(
        point_id="chunk-1",
        score=0.9,
        payload={
            "point_type": "chunk",
            "doc_id": "0123456789abcdef",
            "chunk_index": 0,
            "section_title": "Раздел",
            "content": "текст чанка",
            "tags": [],
            "filepath": "",
        },
    )

    monkeypatch.setattr(chat_module._llm, "chat", raise_runtime)
    monkeypatch.setattr(chat_module._embedder, "embed", lambda text: [0.0] * 8)
    monkeypatch.setattr(chat_module._vector_store, "search_composite", lambda **k: [chunk_hit])

    resp = client.post("/api/chat", json={"query": "test", "mode": "dense"})
    assert resp.status_code == 503
    data = resp.json()
    assert data["code"] == "dependency_unavailable"
    assert data["service"] == "llm"


def test_generic_exception_returns_500_with_russian_message(client, monkeypatch):
    """Необработанная ошибка (не DependencyUnavailableError) → 500 с code=internal_error."""
    from app.api import chat as chat_module
    from app.services.fusion import Hit

    chunk_hit = Hit(
        point_id="chunk-1",
        score=0.9,
        payload={
            "point_type": "chunk",
            "doc_id": "0123456789abcdef",
            "chunk_index": 0,
            "section_title": "Раздел",
            "content": "текст чанка",
            "tags": [],
            "filepath": "",
        },
    )

    monkeypatch.setattr(chat_module._embedder, "embed", lambda text: [0.0] * 8)
    monkeypatch.setattr(chat_module._vector_store, "search_composite", lambda **k: [chunk_hit])
    monkeypatch.setattr(chat_module._prompts, "format", lambda *a, **k: (_ for _ in ()).throw(ValueError("bug in prompts")))

    resp = client.post("/api/chat", json={"query": "test", "mode": "dense"})
    assert resp.status_code == 500
    data = resp.json()
    assert data["code"] == "internal_error"
    assert "ошибка" in data["detail"].lower()


def test_health_returns_status_structure(client, monkeypatch):
    """/health возвращает {status, dependencies}."""
    from app.services import health as health_module

    health_module._cache = {}
    health_module._cache_ts = 0.0

    monkeypatch.setattr(health_module, "_check_llm", lambda: {"status": "ok"})
    monkeypatch.setattr(health_module, "_check_embeddings", lambda: {"status": "ok"})
    monkeypatch.setattr(health_module, "_check_qdrant", lambda: {"status": "ok"})

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "dependencies" in data
    assert data["dependencies"]["llm"]["status"] == "ok"
    assert data["dependencies"]["ollama"]["status"] == "ok"
    assert data["dependencies"]["qdrant"]["status"] == "ok"


def test_health_reports_down_when_qdrant_unavailable(client, monkeypatch):
    from app.services import health as health_module

    health_module._cache = {}
    health_module._cache_ts = 0.0

    monkeypatch.setattr(health_module, "_check_llm", lambda: {"status": "ok"})
    monkeypatch.setattr(health_module, "_check_embeddings", lambda: {"status": "ok"})
    monkeypatch.setattr(health_module, "_check_qdrant", lambda: {"status": "down", "error": "conn refused"})

    resp = client.get("/health")
    data = resp.json()
    assert data["status"] == "down"
    assert data["dependencies"]["qdrant"]["status"] == "down"


def test_health_reports_degraded_when_ollama_down(client, monkeypatch):
    from app.services import health as health_module

    health_module._cache = {}
    health_module._cache_ts = 0.0

    monkeypatch.setattr(health_module, "_check_llm", lambda: {"status": "ok"})
    monkeypatch.setattr(health_module, "_check_embeddings", lambda: {"status": "down", "error": "conn refused"})
    monkeypatch.setattr(health_module, "_check_qdrant", lambda: {"status": "ok"})

    resp = client.get("/health")
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["dependencies"]["ollama"]["status"] == "down"
