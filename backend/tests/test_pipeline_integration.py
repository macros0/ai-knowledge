"""Chaos/failure-injection тесты пайплайна: обрывы LLM и Qdrant.

Проверяют, что инкрементальная генерация не падает с необработанным crash
и не уходит в неверный статус при транзиентных/постоянных сбоях внешних сервисов:
  - LLMTimeoutError на чанке -> авто-ретрай -> восстановление -> done
  - Qdrant недоступен на финализации -> status="failed" + понятная ошибка
Всё изолировано: settings и реестр перенаправляются в tmp_path.
"""
import json
import time
from pathlib import Path

import pytest

from app.config import Settings
from app.models.schemas import Concept
from app.services.errors import EmbedderError, VectorStoreError
from app.services.llm_client import LLMTimeoutError
from app.services.pipeline import Pipeline
from app.services.registry import DocumentRegistry
from app.services.staging import StagingStore


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
        "app.services.embedder",
        "app.services.llm_client",
        "app.services.okf_generator",
        "app.services.vector_store",
    ):
        monkeypatch.setattr(f"{mod}.get_settings", lambda: settings)

    # БД уже сконфигурирована автозапускаемой фикстурой _db (conftest.py).
    reg = DocumentRegistry()
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
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()

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
        pipeline.vector_store.index_concepts = lambda *a, **k: set()

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        doc = reg.get(doc_id)
        assert doc["status"] == "paused"
        assert doc["error"]


class TestPipelineVectorChaos:
    def test_qdrant_unavailable_pauses_document(self, isolated_env, monkeypatch):
        """Qdrant недоступен на финализации -> paused (transient, можно resume)."""
        reg, src = isolated_env
        doc_id = "qd-doc"
        reg.create(doc_id, "test.doc", "doc", 100)

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept()]
        pipeline.vector_store.ensure_collection = lambda: (_ for _ in ()).throw(
            VectorStoreError("Qdrant недоступен", cause=ConnectionRefusedError("refused"))
        )
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        doc = reg.get(doc_id)
        assert doc["status"] == "paused", f"status={doc['status']} error={doc.get('error')}"
        assert "Qdrant" in doc["error"] or "недоступ" in doc["error"]


class TestPipelineFinalizeRetry:
    def test_finalize_failure_keeps_staging_and_resume_skips_llm(self, isolated_env, monkeypatch):
        reg, src = isolated_env
        doc_id = "fin-retry"
        reg.create(doc_id, "test.doc", "doc", 100)

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept()]
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
        pipeline.vector_store.index_concepts = lambda *a, **k: (_ for _ in ()).throw(
            VectorStoreError("Qdrant недоступен", cause=ConnectionRefusedError("refused"))
        )
        pipeline.vector_store.index_chunks = lambda *a, **k: set()

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        doc = reg.get(doc_id)
        assert doc["status"] == "paused"

        staging_dir = pipeline.settings.staging_dir / doc_id
        assert staging_dir.is_dir(), "staging должен сохраниться после сбоя финализации"
        assert StagingStore(doc_id).exists(), "manifest должен сохраниться в БД"
        assert (staging_dir / "chunk_00.json").is_file()

        llm_calls = {"n": 0}

        def fail_if_llm(*args, **kwargs):
            llm_calls["n"] += 1
            raise AssertionError("LLM не должен перегенерировать уже готовые чанки")

        pipeline.okf_generator.generate_chunk = fail_if_llm
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()

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
        assert doc["okf_concept_count"] == 0
        assert doc["error"] is None
        assert indexed["called"] is False

    def test_okf_concept_count_is_concept_count_not_chunk_count(self, isolated_env, monkeypatch):
        reg, src = isolated_env
        doc_id = "multi-concept"
        reg.create(doc_id, "test.doc", "doc", 100)

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept(), _concept(), _concept()]
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        doc = reg.get(doc_id)
        assert doc["status"] == "done"
        assert doc["okf_concept_count"] == 3
        assert doc["okf_concept_count"] == len(list((pipeline.settings.okf_dir / doc_id).glob("*.md")))

    def test_chunks_persisted_in_bundle(self, isolated_env, monkeypatch):
        reg, src = isolated_env
        doc_id = "chunk-persist"
        reg.create(doc_id, "test.doc", "doc", 100)

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept()]
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        chunks_dir = pipeline.settings.okf_dir / doc_id / "chunks"
        assert (chunks_dir / "chunk_00.md").is_file(), "текст чанка должен попадать в бандл"
        assert (chunks_dir / "manifest.json").is_file()
        meta = json.loads((chunks_dir / "manifest.json").read_text(encoding="utf-8"))
        assert meta == [{"index": 0, "size": meta[0]["size"], "concepts_count": 1}]
        assert (chunks_dir / "chunk_00.md").read_text(encoding="utf-8") == "тестовый текст"

    def test_concept_frontmatter_has_chunk_index(self, isolated_env, monkeypatch):
        reg, src = isolated_env
        doc_id = "chunk-link"
        reg.create(doc_id, "test.doc", "doc", 100)

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept()]
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        md_files = list((pipeline.settings.okf_dir / doc_id).glob("*.md"))
        assert md_files, "концепт должен быть записан в бандл"
        assert "chunk_index: 0" in md_files[0].read_text(encoding="utf-8")


class TestEnsureChunks:
    def test_backfill_from_source_and_cache(self, isolated_env, monkeypatch):
        reg, src = isolated_env
        doc_id = "backfill"
        reg.create(doc_id, "test.doc", "doc", 100)
        reg.update(doc_id, status="done")

        pipeline = Pipeline()
        pipeline.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        (pipeline.settings.uploads_dir / "backfill.doc").write_text("исходник", encoding="utf-8")

        chunks = ["чанк 1", "чанк 2"]
        pipeline.okf_generator.chunk_text = lambda *a, **k: chunks

        meta = pipeline.ensure_chunks(doc_id)
        assert [m["index"] for m in meta] == [0, 1]

        chunks_dir = pipeline.settings.okf_dir / doc_id / "chunks"
        assert (chunks_dir / "chunk_00.md").read_text(encoding="utf-8") == "чанк 1"
        assert (chunks_dir / "manifest.json").is_file()

        calls = {"n": 0}

        def fail_parse(*a, **k):
            calls["n"] += 1
            raise AssertionError("backfill не должен повторяться из кэша")

        monkeypatch.setattr("app.services.pipeline.parse_document", fail_parse)
        meta2 = pipeline.ensure_chunks(doc_id)
        assert len(meta2) == 2
        assert calls["n"] == 0


class TestPipelineRegenerate:
    def _setup_done_doc(self, reg, pipeline, doc_id) -> Path:
        pipeline.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        (pipeline.settings.uploads_dir / f"{doc_id}.doc").write_text("исходник", encoding="utf-8")
        bundle = pipeline.settings.okf_dir / doc_id
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "old_concept.md").write_text("---\ntitle: Старый\n---\nстарый концепт", encoding="utf-8")
        reg.create(doc_id, "test.doc", "doc", 100)
        reg.update(doc_id, status="done", okf_concept_count=1)
        return bundle

    def test_regenerate_resets_state_synchronously(self, isolated_env):
        reg, _ = isolated_env
        doc_id = "regen-sync"
        pipeline = Pipeline()
        bundle = self._setup_done_doc(reg, pipeline, doc_id)

        delete_calls = {"n": 0}
        started = {"n": 0}
        pipeline.vector_store.delete_document = lambda *a, **k: delete_calls.__setitem__("n", delete_calls["n"] + 1)
        pipeline._start = lambda *a, **k: started.__setitem__("n", started["n"] + 1)

        pipeline.regenerate(doc_id)

        doc = reg.get(doc_id)
        assert doc["status"] == "processing", "статус должен стать processing сразу"
        assert doc["error"] is None
        assert delete_calls["n"] == 1, "старые векторы должны удаляться"
        assert not (bundle / "old_concept.md").exists(), "старый бандл должен быть удалён"
        assert not pipeline.settings.staging_dir.joinpath(doc_id).exists(), "staging должен очищаться"
        assert started["n"] == 1, "должен запускаться новый прогон"

    def test_regenerate_full_rerun_rebuilds_bundle(self, isolated_env):
        reg, _ = isolated_env
        doc_id = "regen-full"
        pipeline = Pipeline()
        bundle = self._setup_done_doc(reg, pipeline, doc_id)

        llm_calls = {"n": 0}
        delete_calls = {"n": 0}

        def generate(*args, **kwargs):
            llm_calls["n"] += 1
            return [_concept()]

        pipeline.okf_generator.generate_chunk = generate
        pipeline.vector_store.delete_document = lambda *a, **k: delete_calls.__setitem__("n", delete_calls["n"] + 1)
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None

        pipeline.regenerate(doc_id)

        result = pipeline.wait_for(doc_id, timeout=10)

        assert result["status"] == "done", f"status={result['status']} error={result.get('error')}"
        assert llm_calls["n"] >= 1, "LLM должен перегенерировать концепты с нуля"
        assert delete_calls["n"] >= 1, "векторы должны пересоздаваться"
        assert not (bundle / "old_concept.md").exists(), "старый бандл должен быть удалён"
        assert (bundle / "chunks").is_dir(), "новый бандл должен пересоздаваться"
        assert len(list(bundle.glob("*.md"))) == 1

    def test_wait_for_blocks_until_terminal_status(self, isolated_env):
        reg, _ = isolated_env
        doc_id = "regen-wait"
        pipeline = Pipeline()
        self._setup_done_doc(reg, pipeline, doc_id)

        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept()]
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None

        pipeline.regenerate(doc_id)
        result = pipeline.wait_for(doc_id, timeout=30)

        assert result["status"] == "done", f"status={result['status']} error={result.get('error')}"
        assert result["okf_concept_count"] == 1
        assert not pipeline._threads.get(doc_id), "поток должен завершиться и очиститься"


class TestFinalizeDevTagsHealing:
    def test_finalize_rereads_dev_tags_from_db_after_desync(self, isolated_env):
        """Самовосстановление рассинхрона dev_tags через _finalize.

        Воспроизводит конечное состояние после «убитого» daemon-потока
        (рестарт процесса во время фонового реиндекса): development_id уже
        записан в БД, а dev_tags в Qdrant отстаёт. _finalize обязан перечитать
        development_id из БД и записать актуальные dev_tags — независимо от
        того, что происходило с фоновой синхронизацией до этого.
        """
        from app.services.attribute_registry import get_attribute_registry
        from app.services.development_registry import get_development_registry

        reg, src = isolated_env
        get_attribute_registry().add("module", "PY")
        dev = get_development_registry().create("12010", "СЭДО", module="PY")

        doc_id = "heal-doc"
        reg.create(doc_id, "test.doc", "doc", 100)
        # БД актуальна (development_id), Qdrant «отстал» — dev_tags ещё не записаны.
        reg.update(doc_id, development_id=dev["id"])

        captured: dict = {}

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept()]
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None

        def cap_concepts(*args, **kwargs):
            captured["concepts_dev_tags"] = kwargs.get("dev_tags")
            return set()

        def cap_chunks(*args, **kwargs):
            captured["chunks_dev_tags"] = kwargs.get("dev_tags")
            return set()

        pipeline.vector_store.index_concepts = cap_concepts
        pipeline.vector_store.index_chunks = cap_chunks

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        assert captured["concepts_dev_tags"] == ["12010", "СЭДО", "PY"]
        assert captured["chunks_dev_tags"] == ["12010", "СЭДО", "PY"]


class TestHasDuplicatesFlag:
    def test_process_sets_has_duplicates_when_near_duplicate(self, isolated_env):
        from app.services.deduplication import index_document

        reg, src = isolated_env
        # Существующий документ с тем же содержимым, что и markdown в пайплайне.
        reg.create("aaaaaaaaaaaaaaaa", "existing.doc", "doc", 100)
        index_document("aaaaaaaaaaaaaaaa", "тестовый текст")

        doc_id = "bbbbbbbbbbbbbbbb"
        reg.create(doc_id, "test.doc", "doc", 100)

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept()]
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        doc = reg.get(doc_id)
        assert doc["has_duplicates"] is True

    def test_process_no_duplicates_flag_false(self, isolated_env):
        reg, src = isolated_env
        doc_id = "cccccccccccccccc"
        reg.create(doc_id, "test.doc", "doc", 100)

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept()]
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        doc = reg.get(doc_id)
        assert doc["has_duplicates"] is False
