"""Тесты centralized exception handler и /health endpoint.

Проверяют:
  - DependencyUnavailableError -> 503 JSON с code/service
  - /health возвращает статус зависимостей
"""

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

    chat_module._get_embedder().embed = raise_qdrant_error
    try:
        resp = client.post("/api/chat", json={"query": "test", "mode": "dense"})
        assert resp.status_code == 503
        data = resp.json()
        assert data["code"] == "dependency_unavailable"
        assert data["service"] == "qdrant"
        assert "Повторите попытку" in data["detail"]
    finally:
        chat_module._get_embedder().embed = lambda text: [0.0] * 8


def test_llm_error_returns_503_with_llm_service(client, monkeypatch):
    """LLM connection failure is wrapped and remains actionable without raw details."""
    from app.api import chat as chat_module
    from app.services.fusion import Hit
    from app.services.registry import DocumentRegistry

    # Хит ссылается на документ — DB-side фильтр видимости отсекает orphan-хиты.
    DocumentRegistry().create("0123456789abcdef", "a.pdf", "application/pdf", 1)

    def raise_runtime(system, user, **kw):
        raise ConnectionError("OpenRouter connection lost")

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

    monkeypatch.setattr(chat_module._get_llm(), "chat", raise_runtime)
    monkeypatch.setattr(chat_module._get_embedder(), "embed", lambda text: [0.0] * 8)
    monkeypatch.setattr(chat_module._get_vector_store(), "search_composite", lambda **k: [chunk_hit])

    resp = client.post("/api/chat", json={"query": "test", "mode": "dense"})
    assert resp.status_code == 503
    data = resp.json()
    assert data["code"] == "dependency_unavailable"
    assert data["service"] == "llm"


def test_generic_exception_returns_500_with_russian_message(client, monkeypatch):
    """Необработанная ошибка (не DependencyUnavailableError) → 500 с code=internal_error."""
    from app.api import chat as chat_module
    from app.services.fusion import Hit
    from app.services.registry import DocumentRegistry

    # Хит ссылается на документ — DB-side фильтр видимости отсекает orphan-хиты.
    DocumentRegistry().create("0123456789abcdef", "a.pdf", "application/pdf", 1)

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

    monkeypatch.setattr(chat_module._get_embedder(), "embed", lambda text: [0.0] * 8)
    monkeypatch.setattr(chat_module._get_vector_store(), "search_composite", lambda **k: [chunk_hit])
    monkeypatch.setattr(chat_module.get_store(), "format", lambda *a, **k: (_ for _ in ()).throw(ValueError("bug in prompts")))

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
    assert isinstance(data["knowledge_profile"], str)
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


def test_health_rate_limited_llm_is_not_degraded(client, monkeypatch):
    """429 LLM (rate_limited) не должен переводить общий статус в degraded."""
    from app.services import health as health_module

    health_module._cache = {}
    health_module._cache_ts = 0.0

    monkeypatch.setattr(health_module, "_check_llm", lambda: {"status": "rate_limited", "error": "HTTP 429"})
    monkeypatch.setattr(health_module, "_check_embeddings", lambda: {"status": "ok"})
    monkeypatch.setattr(health_module, "_check_qdrant", lambda: {"status": "ok"})

    resp = client.get("/health")
    data = resp.json()
    assert data["status"] == "ok"
    assert data["dependencies"]["llm"]["status"] == "rate_limited"


@pytest.mark.parametrize("kind", ["dependency", "api", "http", "unexpected"])
def test_internal_details_only_in_server_logs(client, caplog, kind):
    from fastapi import HTTPException
    from app.api.errors import ApiError

    raw = 'litellm.BadRequestError: OpenrouterException context_length_exceeded private-user'

    @client.app.get("/test-provider-error")
    def fail():
        try:
            raise RuntimeError(raw)
        except RuntimeError as exc:
            if kind == "dependency":
                raise LLMError(raw, cause=exc) from exc
            if kind == "api":
                raise ApiError(500, "internal_error", raw) from exc
            if kind == "http":
                raise HTTPException(502, raw) from exc
            raise

    response = client.get("/test-provider-error")
    assert response.status_code >= 500
    assert "litellm" not in response.text
    assert "private-user" not in response.text
    assert "техническую поддержку" in response.json()["detail"]
    assert raw in caplog.text
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize("failure, expected", [
    (TimeoutError("private timeout diagnostic"), "timeout"),
    (ConnectionError("private network diagnostic"), "dependency_unavailable"),
])
def test_recoverable_dependency_error_has_actionable_code(client, failure, expected):
    @client.app.get("/test-recoverable-error")
    def fail():
        raise LLMError(cause=failure)

    response = client.get("/test-recoverable-error")
    assert response.json()["code"] == expected
    assert "private" not in response.text


@pytest.mark.parametrize("status, expected", [(429, "rate_limited"), (503, "dependency_unavailable"), (408, "timeout"), (400, "internal_error"), (401, "internal_error")])
def test_provider_status_is_classified_without_matching_its_message(status, expected):
    from app.services.errors import public_error_code

    failure = RuntimeError("untrusted provider JSON: please retry or restart")
    failure.status_code = status
    assert public_error_code(LLMError(cause=failure)) == expected


def test_qdrant_rest_wrapper_preserves_recoverable_network_cause():
    import httpx
    from qdrant_client.http.exceptions import ResponseHandlingException
    from app.services.errors import processing_error_code

    try:
        try:
            raise httpx.ConnectError("private network diagnostic")
        except httpx.ConnectError as exc:
            raise ResponseHandlingException(exc)
    except ResponseHandlingException as exc:
        assert processing_error_code(VectorStoreError(cause=exc)) == "processing_unavailable"


@pytest.mark.parametrize("status, expected", [
    ("UNAVAILABLE", "processing_unavailable"),
    ("DEADLINE_EXCEEDED", "generation_timeout"),
    ("INVALID_ARGUMENT", "internal_error"),
])
def test_qdrant_grpc_failures_have_actionable_codes(status, expected):
    import grpc
    from app.services.errors import processing_error_code

    class Failure(grpc.RpcError):
        def code(self):
            return getattr(grpc.StatusCode, status)

    assert processing_error_code(VectorStoreError(cause=Failure())) == expected
