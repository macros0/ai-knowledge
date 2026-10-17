"""Тесты embedder: пустой вход и батчинг против лимитов провайдера."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.embedder import Embedder


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        data_dir=Path("data/unit-embedder"),
        diagnostics_root=Path("data/unit-embedder-diagnostics"),
        embedding_provider="http",
        embedding_model="openai/fixture",
        embedding_api_base="http://localhost:11434",
        embedding_batch_size=64,
        embedding_dimensions=8,
        embedding_timeout_seconds=30,
        embedding_retry_attempts=1,
        embedding_retry_backoff_seconds=0,
        llm_timeout_seconds=120,
    )


def _fake_response(texts, dim, start=0):
    return SimpleNamespace(
        data=[
            {"index": start + i, "object": "embedding", "embedding": [float(start + i + j) for j in range(dim)]}
            for i in range(len(texts))
        ]
    )


def test_empty_input_returns_without_network(monkeypatch):
    called = {"n": 0}

    def boom(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("сеть не должна вызываться на пустом входе")

    monkeypatch.setattr("app.services.embedder.litellm.embedding", boom)
    monkeypatch.setattr("app.services.embedder.get_settings", _settings)

    assert Embedder().embed_texts([]) == []
    assert called["n"] == 0


def test_batching_splits_into_safe_chunks(monkeypatch):
    calls = []
    counter = {"n": 0}

    def fake_embedding(model: str | None = None, input: list[str] | None = None, **kwargs):
        batch = input or []
        calls.append(list(batch))
        start = counter["n"]
        counter["n"] += len(batch)
        return _fake_response(batch, dim=8, start=start)

    monkeypatch.setattr("app.services.embedder.litellm.embedding", fake_embedding)
    monkeypatch.setattr("app.services.embedder.get_settings", _settings)

    texts = [f"text-{i}" for i in range(250)]
    vectors = Embedder().embed_texts(texts)

    assert len(calls) == 4
    assert [len(c) for c in calls] == [64, 64, 64, 58]
    assert calls[0][0] == "text-0"
    assert calls[3][-1] == "text-249"

    assert len(vectors) == 250
    flattened = [v[0] for v in vectors]
    assert flattened == list(range(250)), "векторы должны склеиваться без потерь и перестановок"


def test_single_batch_when_small(monkeypatch):
    calls = []

    def fake_embedding(model: str | None = None, input: list[str] | None = None, **kwargs):
        calls.append(list(input) if input is not None else [])
        return _fake_response(input or [], dim=8)

    monkeypatch.setattr("app.services.embedder.litellm.embedding", fake_embedding)
    monkeypatch.setattr("app.services.embedder.get_settings", _settings)

    vectors = Embedder().embed_texts(["один", "два"])
    assert len(calls) == 1
    assert len(vectors) == 2


def test_batch_error_raises_embedder_error(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("embedding server down")

    monkeypatch.setattr("app.services.embedder.litellm.embedding", boom)
    monkeypatch.setattr("app.services.embedder.get_settings", _settings)

    from app.services.errors import EmbedderError

    with pytest.raises(EmbedderError, match="эмбеддинг"):
        Embedder().embed_texts(["x"] * 10)

def test_ollama_native_wire_refuses_oversize_instead_of_returning_truncated_vector(monkeypatch):
    import httpx
    from app.services.errors import EmbedderError
    from app.services import embedder
    settings = _settings().model_copy(update={'embedding_model': 'ollama/bge-m3'})
    monkeypatch.setattr(embedder, 'get_settings', lambda: settings)
    async def native_post(self, url, *, json, **kwargs):
        assert url.endswith('/api/embed')
        if json.get('truncate') is False:
            request = httpx.Request('POST', url)
            raise httpx.HTTPStatusError('input length exceeds model context', request=request,
                response=httpx.Response(400, request=request))
        return httpx.Response(200, json={'embeddings': [[0.0] * 8], 'prompt_eval_count': 4096}, request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.AsyncClient, 'post', native_post)
    with pytest.raises(EmbedderError):
        Embedder().embed('x ' * 4128)


def test_ollama_native_wire_keeps_entire_regular_input_and_disables_truncation(monkeypatch):
    import httpx
    from app.services import embedder
    monkeypatch.setattr(embedder, 'get_settings', lambda: _settings().model_copy(update={'embedding_model': 'ollama/bge-m3'}))
    bodies = []
    async def native_post(self, url, *, json, **kwargs):
        bodies.append(json)
        return httpx.Response(200, json={'embeddings': [[1.0] * 8], 'prompt_eval_count': 3}, request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.AsyncClient, 'post', native_post)
    assert Embedder().embed('0 / False') == [1.0] * 8
    assert bodies[0]['input'] == ['0 / False'] and bodies[0].get('truncate') is False


def test_non_ollama_embedding_preserves_provider_parameters(monkeypatch):
    from app.services import embedder
    settings = _settings().model_copy(update={'embedding_model': 'openai/fixture'})
    monkeypatch.setattr(embedder, 'get_settings', lambda: settings)
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        return _fake_response(kwargs['input'], 8)
    monkeypatch.setattr(embedder.litellm, 'embedding', provider)
    assert len(Embedder().embed('whole value')) == 8
    assert 'truncate' not in calls[0] and calls[0]['timeout'] == 30
