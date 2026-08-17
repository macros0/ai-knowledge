"""Тесты embedder: пустой вход и батчинг против лимитов провайдера."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.embedder import Embedder


def _settings() -> Settings:
    return Settings(
        data_dir=Path("."),
        embedding_provider="http",
        embedding_model="ollama/bge-m3",
        embedding_api_base="http://localhost:11434",
        embedding_batch_size=64,
        embedding_dimensions=8,
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
    monkeypatch.setattr("app.config.get_settings", lambda: _settings())

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
    monkeypatch.setattr("app.config.get_settings", lambda: _settings())

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
    monkeypatch.setattr("app.config.get_settings", lambda: _settings())

    vectors = Embedder().embed_texts(["один", "два"])
    assert len(calls) == 1
    assert len(vectors) == 2


def test_batch_error_is_raised(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("embedding server down")

    monkeypatch.setattr("app.services.embedder.litellm.embedding", boom)
    monkeypatch.setattr("app.config.get_settings", lambda: _settings())

    with pytest.raises(RuntimeError, match="embedding server down"):
        Embedder().embed_texts(["x"] * 10)