"""Chaos/failure-injection тесты пайплайна: обрывы LLM и Qdrant.

Проверяют, что инкрементальная генерация не падает с необработанным crash
и не уходит в неверный статус при транзиентных/постоянных сбоях внешних сервисов:
  - LLMTimeoutError на чанке -> авто-ретрай -> восстановление -> done
  - Qdrant недоступен на финализации -> status="failed" + понятная ошибка
Всё изолировано: settings и реестр перенаправляются в tmp_path.
"""
from pathlib import Path

import pytest

from app.config import Settings
from app.models.schemas import Concept
from app.services.llm_client import LLMTimeoutError
from app.services.pipeline import Pipeline
from app.services.registry import DocumentRegistry


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        llm_model="openai/test",
        llm_base_url="http://localhost",
        llm_api_key="key",
        llm_timeout_seconds=0.05,
        llm_retry_attempts=1,
        llm_retry_backoff_seconds=0,
        llm_stream_idle_timeout_seconds=0.05,
        llm_max_total_timeout_seconds=0.5,
        llm_chunk_retry_attempts=2,
        llm_chunk_retry_backoff_seconds=0.01,
        embedding_provider="fake",
        embedding_dimensions=8,
    )


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    for mod in (
        "app.config",
        "app.services.pipeline",
        "app.services.staging",
        "app.services.registry",
        "app.services.embedder",
        "app.services.llm_client",
        "app.services.okf_generator",
        "app.services.vector_store",
    ):
        monkeypatch.setattr(f"{mod}.get_settings", lambda: settings)

    reg = DocumentRegistry()
    reg._docs = {}
    monkeypatch.setattr("app.services.pipeline.get_registry", lambda: reg)

    monkeypatch.setattr("app.services.pipeline.parse_document", lambda *a, **k: [])
    monkeypatch.setattr("app.services.pipeline.blocks_to_markdown", lambda b: "тестовый текст")
    monkeypatch.setattr("app.services.pipeline._collect_attachments", lambda b, d: [])

    src = tmp_path / "test.doc"
    src.write_text("test", encoding="utf-8")
    return reg, str(src)


def _concept() -> Concept:
    return Concept(id="c1", title="Один", type="concept", content="текст концепта")


class TestPipelineLLMChaos:
    def test_chunk_retry_recovers_after_transient_timeout(self, isolated_env, monkeypatch):
        reg, src = isolated_env
        doc_id = "retry-doc"
        reg.create(doc_id, "test.doc", "doc", 100)

        calls = {"n": 0}

        def flaky_generate(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise LLMTimeoutError("LLM вызов превысил 0.05s")
            return [_concept()]

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = flaky_generate
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.index_concepts = lambda *a, **k: None

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        doc = reg.get(doc_id)
        assert doc["status"] == "done", f"status={doc['status']} error={doc.get('error')}"
        assert calls["n"] == 2
        assert doc["error"] is None

    def test_chunk_retry_exhausted_pauses_document(self, isolated_env, monkeypatch):
        reg, src = isolated_env
        doc_id = "exhaust-doc"
        reg.create(doc_id, "test.doc", "doc", 100)

        def always_timeout(*args, **kwargs):
            raise LLMTimeoutError("LLM вызов превысил 0.05s")

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = always_timeout
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.index_concepts = lambda *a, **k: None

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        doc = reg.get(doc_id)
        assert doc["status"] == "paused"
        assert doc["error"]


class TestPipelineVectorChaos:
    def test_qdrant_connection_error_fails_gracefully(self, isolated_env, monkeypatch):
        reg, src = isolated_env
        doc_id = "qd-doc"
        reg.create(doc_id, "test.doc", "doc", 100)

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept()]
        pipeline.vector_store.ensure_collection = lambda: (_ for _ in ()).throw(
            ConnectionRefusedError("Qdrant refused")
        )
        pipeline.vector_store.index_concepts = lambda *a, **k: None

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        doc = reg.get(doc_id)
        assert doc["status"] == "failed"
        assert "Qdrant" in doc["error"]


class TestPipelineFinalizeRetry:
    def test_finalize_failure_keeps_staging_and_resume_skips_llm(self, isolated_env, monkeypatch):
        reg, src = isolated_env
        doc_id = "fin-retry"
        reg.create(doc_id, "test.doc", "doc", 100)

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept()]
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.index_concepts = lambda *a, **k: (_ for _ in ()).throw(
            ConnectionRefusedError("Qdrant refused")
        )

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        doc = reg.get(doc_id)
        assert doc["status"] == "failed"

        staging_dir = pipeline.settings.staging_dir / doc_id
        assert staging_dir.is_dir(), "staging должен сохраниться после сбоя финализации"
        assert (staging_dir / "manifest.json").is_file()
        assert (staging_dir / "chunk_00.json").is_file()

        llm_calls = {"n": 0}

        def fail_if_llm(*args, **kwargs):
            llm_calls["n"] += 1
            raise AssertionError("LLM не должен перегенерировать уже готовые чанки")

        pipeline.okf_generator.generate_chunk = fail_if_llm
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.index_concepts = lambda *a, **k: None

        pipeline._process(doc_id, src, "test.doc", [], resume=True)

        doc = reg.get(doc_id)
        assert doc["status"] == "done", f"status={doc['status']} error={doc.get('error')}"
        assert llm_calls["n"] == 0


class TestPipelineNoConcepts:
    def test_zero_concepts_skips_indexing_and_marks_done(self, isolated_env, monkeypatch):
        reg, src = isolated_env
        doc_id = "no-concepts"
        reg.create(doc_id, "test.doc", "doc", 100)

        indexed = {"called": False}

        def fail_if_indexed(*args, **kwargs):
            indexed["called"] = True
            raise AssertionError("Индексация не должна вызываться при нуле концептов")

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: []
        pipeline.vector_store.ensure_collection = fail_if_indexed
        pipeline.vector_store.index_concepts = fail_if_indexed
        pipeline.embedder.embed_texts = fail_if_indexed

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        doc = reg.get(doc_id)
        assert doc["status"] == "done"
        assert doc["okf_file_count"] == 0
        assert doc["error"] is None
        assert indexed["called"] is False
