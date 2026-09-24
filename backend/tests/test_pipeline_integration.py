"""Chaos/failure-injection тесты пайплайна: обрывы LLM и Qdrant.

Проверяют, что инкрементальная генерация не падает с необработанным crash
и не уходит в неверный статус при транзиентных/постоянных сбоях внешних сервисов:
  - LLMTimeoutError на чанке -> авто-ретрай -> восстановление -> done
  - Qdrant недоступен на финализации -> status="failed" + понятная ошибка
Всё изолировано: settings и реестр перенаправляются в tmp_path.
"""
import errno
import threading
import time
from pathlib import Path

import pytest

from app.config import Settings
from app.models.schemas import Concept
from app.services.errors import VectorStoreError
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
    monkeypatch.setattr("app.services.pipeline.markdown_attachment_spans", lambda b: ("тестовый текст", []))
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
            assert reg.get(doc_id)["error_code"] == "generation_retrying"
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
        assert doc["error_code"] is None

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
        assert doc["error_code"] == "generation_timeout"


def test_finalize_keeps_staging_when_done_status_cannot_be_persisted(isolated_env, monkeypatch):
    """A full database after indexing must leave resume checkpoints intact."""
    reg, _src = isolated_env
    doc_id = "finalize-db-full"
    reg.create(doc_id, "test.doc", "doc", 100)
    pipeline = Pipeline()
    staging = StagingStore(doc_id)
    staging.create(0)
    pipeline.okf_generator.build_okf_docs = lambda *_args, **_kwargs: ([], {})
    pipeline.vector_store.ensure_collection = lambda: None
    pipeline.vector_store.delete_orphaned_points = lambda *_args, **_kwargs: None
    monkeypatch.setattr(pipeline, "_chunks_lack_text", lambda *_args: False)
    original_update = reg.update

    def full_on_done(doc, **fields):
        if fields.get("status") == "done":
            raise OSError(errno.ENOSPC, "No space left on device")
        return original_update(doc, **fields)

    monkeypatch.setattr(reg, "update", full_on_done)

    with pytest.raises(OSError):
        pipeline._finalize(doc_id, "test.doc", staging, attachments=[], global_tags=[])

    assert staging.exists()


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
    def test_disk_full_while_writing_checkpoint_pauses_with_resumable_code(self, isolated_env, monkeypatch):
        """A checkpoint write failure must preserve the staging directory for resume."""
        reg, src = isolated_env
        doc_id = "disk-checkpoint"
        reg.create(doc_id, "test.doc", "doc", 100)

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept()]
        monkeypatch.setattr(
            StagingStore,
            "append_chunk",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError(errno.ENOSPC, "No space left on device")
            ),
        )

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        doc = reg.get(doc_id)
        assert doc["status"] == "paused"
        assert doc["error_code"] == "storage_full"
        assert (pipeline.settings.staging_dir / doc_id).is_dir()

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
    """Контракт «done без концептов» (инцидент 03.09.2026): чанки индексируются
    всегда (dual-index), молчаливой пустоты больше нет — ставится problem-код."""

    def _run(self, reg, src, doc_id, markdown):
        """Прогон _process с подменённым markdown и 0 концептов от LLM.

        Возвращает (pipeline, calls) — calls["chunks"] это тексты, переданные
        в index_chunks (None — если не вызывался).
        """
        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: []
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None

        calls = {"chunks": None}

        def cap_chunks(*args, **kwargs):
            calls["chunks"] = list(args[2]) if len(args) > 2 else list(kwargs.get("chunk_texts", []))
            return set()

        pipeline.vector_store.index_chunks = cap_chunks
        pipeline.vector_store.index_concepts = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("index_concepts не должен вызываться при нуле концептов")
        )

        import app.services.pipeline as pm

        original_btm = pm.markdown_attachment_spans
        pm.markdown_attachment_spans = lambda b: (markdown, [])
        try:
            pipeline._process(doc_id, src, "test.doc", [], resume=False)
        finally:
            pm.markdown_attachment_spans = original_btm
        return pipeline, calls

    def test_zero_concepts_indexes_chunks_and_sets_problem(self, isolated_env, monkeypatch):
        """0 концептов при живом тексте: done + problem=no_concepts + чанки в индексе."""
        reg, src = isolated_env
        doc_id = "no-concepts"
        reg.create(doc_id, "test.doc", "doc", 100)

        long_text = "Подробное описание функциональности. " * 10  # >200 символов
        pipeline, calls = self._run(reg, src, doc_id, long_text)

        doc = reg.get(doc_id)
        assert doc["status"] == "done", f"status={doc['status']} error={doc.get('error')}"
        assert doc["okf_concept_count"] == 0
        assert doc["problem"] == "no_concepts", f"problem={doc.get('problem')}"
        assert calls["chunks"], "чанки обязаны индексироваться даже без концептов"

    def test_scan_only_document_sets_no_text_layer(self, isolated_env, monkeypatch):
        """Скан-PDF без OCR (чанки из одних markdown-картинок): no_text_layer."""
        reg, src = isolated_env
        doc_id = "scan-only"
        reg.create(doc_id, "test.doc", "doc", 100)

        scan_md = "\n\n".join(
            f"![Страница {i} — изображение страницы (скан)](attachments/image-{i}.jpg)"
            for i in range(5)
        )
        pipeline, calls = self._run(reg, src, doc_id, scan_md)

        doc = reg.get(doc_id)
        assert doc["status"] == "done"
        assert doc["problem"] == "no_text_layer", f"problem={doc.get('problem')}"

    def test_degradation_events_aggregate_to_problem(self, isolated_env, monkeypatch):
        """Salvage при генерации чанка → problem=llm_partial_result при done."""
        from app.services import gen_quality

        reg, src = isolated_env
        doc_id = "degraded"
        reg.create(doc_id, "test.doc", "doc", 100)

        def generate_with_salvage(*args, **kwargs):
            gen_quality.record(gen_quality.LLM_SALVAGE, "chunk 1: тестовый salvage")
            return [_concept()]

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = generate_with_salvage
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()

        gen_quality.drain()  # чистый буфер потока перед прогоном
        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        doc = reg.get(doc_id)
        assert doc["status"] == "done", f"status={doc['status']} error={doc.get('error')}"
        assert doc["problem"] == "llm_partial_result", f"problem={doc.get('problem')}"
        assert gen_quality.drain() == [], "события должны дренироваться пайплайном"

    def test_classifier_fallback_has_accurate_problem_code(self, isolated_env, monkeypatch):
        from app.services import gen_quality

        reg, src = isolated_env
        doc_id = "classifier-fallback"
        reg.create(doc_id, "test.doc", "doc", 100)

        def generate_with_classifier_fallback(*args, **kwargs):
            gen_quality.record(gen_quality.CLASSIFIER_FALLBACK, "chunk 1: test")
            return [_concept()]

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = generate_with_classifier_fallback
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()

        gen_quality.drain()
        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        doc = reg.get(doc_id)
        assert doc["status"] == "done"
        assert doc["problem"] == "llm_classifier_fallback"

    def test_salvage_problem_precedes_classifier_fallback(self, isolated_env, monkeypatch):
        from app.services import gen_quality

        reg, src = isolated_env
        doc_id = "salvage-and-classifier"
        reg.create(doc_id, "test.doc", "doc", 100)

        def generate_with_both(*args, **kwargs):
            gen_quality.record(gen_quality.CLASSIFIER_FALLBACK, "chunk 1: classifier")
            gen_quality.record(gen_quality.LLM_SALVAGE, "chunk 1: salvage")
            return [_concept()]

        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = generate_with_both
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()

        gen_quality.drain()
        pipeline._process(doc_id, src, "test.doc", [], resume=False)
        assert reg.get(doc_id)["problem"] == "llm_partial_result"

    def test_clean_run_has_no_problem(self, isolated_env, monkeypatch):
        reg, src = isolated_env
        doc_id = "clean-run"
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
        assert doc["status"] == "done"
        assert doc["problem"] is None

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
        # Фаза 5: canonical-число концептов сверяется с БД, а не с числом .md-файлов
        # (бандлы по умолчанию не пишутся).
        from app.db.models import OkfConcept
        from app.db.session import session_scope

        with session_scope() as s:
            assert s.query(OkfConcept).filter(OkfConcept.doc_id == doc_id).count() == 3

    def test_chunks_persisted_in_db(self, isolated_env, monkeypatch):
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

        # Фаза 5: чанки — канонически в document_chunks (БД), а не в бандле.
        from app.db.models import DocumentChunk
        from app.db.session import session_scope

        with session_scope() as s:
            rows = (
                s.query(DocumentChunk)
                .filter(DocumentChunk.doc_id == doc_id)
                .order_by(DocumentChunk.chunk_index)
                .all()
            )
        assert [c.content for c in rows] == ["тестовый текст"]

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

        # Dual-write: этот тест проверяет формат .md-бандла, поэтому включаем флаг.
        pipeline.settings.okf_write_bundles = True

        pipeline._process(doc_id, src, "test.doc", [], resume=False)

        md_files = list((pipeline.settings.okf_dir / doc_id).glob("*.md"))
        assert md_files, "концепт должен быть записан в бандл"
        assert "chunk_index: 0" in md_files[0].read_text(encoding="utf-8")


class TestAttachmentTag:
    """Программный тег «attachment»: чанк с долей вложения ≥ порога → все концепты с тегом."""

    def _run(self, reg, src, doc_id, monkeypatch, markdown, spans):
        import app.services.pipeline as pm

        monkeypatch.setattr(pm, "markdown_attachment_spans", lambda b: (markdown, spans))
        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept(), _concept()]
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()
        pipeline._process(doc_id, src, "test.doc", [], resume=False)
        return pipeline

    @staticmethod
    def _tags(doc_id):
        from app.db.models import OkfConcept
        from app.db.session import session_scope

        with session_scope() as s:
            rows = s.query(OkfConcept).filter(OkfConcept.doc_id == doc_id).all()
        return [list(r.tags or []) for r in rows]

    def test_attachment_majority_chunk_tagged(self, isolated_env, monkeypatch):
        reg, src = isolated_env
        doc_id = "attach-majority"
        reg.create(doc_id, "test.doc", "doc", 100)
        markdown = "Вложение: " * 300  # целиком вложение (один чанк, доля 1.0)
        self._run(reg, src, doc_id, monkeypatch, markdown, [(0, len(markdown))])
        all_tags = self._tags(doc_id)
        assert all_tags and all("attachment" in tags for tags in all_tags)

    def test_mixed_chunk_below_threshold_not_tagged(self, isolated_env, monkeypatch):
        reg, src = isolated_env
        doc_id = "attach-mixed"
        reg.create(doc_id, "test.doc", "doc", 100)
        doc_part = "Содержимое документа " * 100
        attach_part = "Вложение " * 10
        markdown = doc_part + "\n\n" + attach_part
        spans = [(len(doc_part) + 2, len(markdown))]  # только хвост-вложение (~10%)
        self._run(reg, src, doc_id, monkeypatch, markdown, spans)
        all_tags = self._tags(doc_id)
        assert all_tags and all("attachment" not in tags for tags in all_tags)

    def test_disabled_flag_never_tags(self, isolated_env, monkeypatch):
        import app.services.pipeline as pm

        reg, src = isolated_env
        doc_id = "attach-disabled"
        reg.create(doc_id, "test.doc", "doc", 100)
        markdown = "Вложение: " * 300
        monkeypatch.setattr(pm, "markdown_attachment_spans", lambda b: (markdown, [(0, len(markdown))]))
        pipeline = Pipeline()
        pipeline.settings.okf_attachment_tag_enabled = False
        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept()]
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()
        pipeline._process(doc_id, src, "test.doc", [], resume=False)
        all_tags = self._tags(doc_id)
        assert all_tags and all("attachment" not in tags for tags in all_tags)


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

        # Этап 2b: ленивый backfill пишет чанки в document_chunks (БД), не в бандл.
        from app.db.models import DocumentChunk
        from app.db.session import session_scope

        with session_scope() as s:
            rows = (
                s.query(DocumentChunk)
                .filter(DocumentChunk.doc_id == doc_id)
                .order_by(DocumentChunk.chunk_index)
                .all()
            )
        assert [c.content for c in rows] == ["чанк 1", "чанк 2"]

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
        # Dual-write: тест проверяет пересоздание бандла — включаем флаг.
        pipeline.settings.okf_write_bundles = True

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


class TestPipelineDbStore:
    """Этап 2b (PostgreSQL SSOT): финализация пишет чанки + концепты (+prov) +
    вложения одной транзакцией; regenerate чистит их."""

    def _run_done(self, reg, src, doc_id, monkeypatch, attachments=None) -> Pipeline:
        pipeline = Pipeline()
        pipeline.okf_generator.generate_chunk = lambda *a, **k: [_concept()]
        pipeline.vector_store.ensure_collection = lambda: None
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
        pipeline.vector_store.index_concepts = lambda *a, **k: set()
        pipeline.vector_store.index_chunks = lambda *a, **k: set()
        if attachments is not None:
            monkeypatch.setattr(
                "app.services.pipeline._collect_attachments", lambda b, d: attachments
            )
        pipeline._process(doc_id, src, "test.doc", [], resume=False)
        return pipeline

    def test_finalize_writes_chunks_and_provenance(self, isolated_env, monkeypatch):
        import hashlib

        from app.db.models import DocumentChunk, OkfConcept
        from app.db.session import session_scope

        reg, src = isolated_env
        doc_id = "db-store"
        reg.create(doc_id, "test.doc", "doc", 100)
        self._run_done(reg, src, doc_id, monkeypatch)

        with session_scope() as s:
            chunks = s.query(DocumentChunk).filter(DocumentChunk.doc_id == doc_id).all()
            assert len(chunks) == 1
            c = chunks[0]
            assert c.chunk_index == 0
            assert c.content == "тестовый текст"
            assert c.section_title == ""
            assert c.content_hash == hashlib.sha256("тестовый текст".encode("utf-8")).hexdigest()
            assert c.char_count == len("тестовый текст")

            concepts = s.query(OkfConcept).filter(OkfConcept.doc_id == doc_id).all()
            assert len(concepts) == 1
            assert concepts[0].content == "текст концепта"
            assert concepts[0].chunk_index == 0
            # Провенанс (не путать с created_at — временем SQL INSERT).
            assert concepts[0].generated_at is not None
            assert concepts[0].model_id == "openai/test"
            assert concepts[0].prompt_version and len(concepts[0].prompt_version) == 12

    def test_finalize_writes_attachments(self, isolated_env, monkeypatch):
        import hashlib

        from app.config import get_settings
        from app.db.models import OkfAttachment
        from app.db.session import session_scope

        reg, src = isolated_env
        doc_id = "att-doc"
        reg.create(doc_id, "test.doc", "doc", 100)

        settings = get_settings()
        att_dir = settings.uploads_dir / doc_id / "attachments"
        att_dir.mkdir(parents=True, exist_ok=True)
        payload = b"\x89PNG\r\n\x1a\nfakepngdata"
        (att_dir / "diagram.png").write_bytes(payload)

        attachment = {
            "name": "diagram.png",
            "kind": "image",
            "caption": "",
            "saved_path": "attachments/diagram.png",
            "is_processable": False,
            "extraction_status": "saved",
        }
        self._run_done(reg, src, doc_id, monkeypatch, attachments=[attachment])

        with session_scope() as s:
            rows = s.query(OkfAttachment).filter(OkfAttachment.doc_id == doc_id).all()
            assert len(rows) == 1
            a = rows[0]
            assert a.saved_path == "attachments/diagram.png"
            assert a.kind == "image"
            assert a.is_processable is False
            assert a.extraction_status == "saved"
            assert a.size == len(payload)
            assert a.sha256 == hashlib.sha256(payload).hexdigest()

    def test_regenerate_wipes_chunks(self, isolated_env, monkeypatch):
        from app.config import get_settings
        from app.db.models import DocumentChunk
        from app.db.session import session_scope

        reg, src = isolated_env
        doc_id = "regen-wipe"
        reg.create(doc_id, "test.doc", "doc", 100)
        pipeline = self._run_done(reg, src, doc_id, monkeypatch)

        with session_scope() as s:
            assert s.query(DocumentChunk).filter(DocumentChunk.doc_id == doc_id).count() == 1

        settings = get_settings()
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        (settings.uploads_dir / f"{doc_id}.doc").write_text("исходник", encoding="utf-8")

        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline._start = lambda *a, **k: None
        pipeline.regenerate(doc_id)

        with session_scope() as s:
            assert s.query(DocumentChunk).filter(DocumentChunk.doc_id == doc_id).count() == 0


class TestCollectAttachmentsPortability:
    """saved_path в БД должен быть переносимым независимо от ОС обработки."""

    def test_windows_path_outside_base_normalized(self):
        from app.services.pipeline import _collect_attachments

        from docparser import Block

        win = "C:\\Users\\alexey\\ai-workspace\\data\\uploads\\doc1\\attachments\\embedded-0.pdf"
        blocks = [Block("attachment", "Вложение", meta={"name": "oleObject2.bin", "kind": "pdf", "saved_path": win})]

        rows = _collect_attachments(blocks, Path("/var/lib/okf/uploads/doc1"))

        assert rows[0]["saved_path"] == "attachments/embedded-0.pdf"

    def test_path_under_base_stays_relative(self, tmp_path):
        from app.services.pipeline import _collect_attachments

        from docparser import Block

        base = tmp_path / "uploads" / "doc1"
        (base / "attachments").mkdir(parents=True)
        saved = base / "attachments" / "image-0.png"
        saved.write_bytes(b"x")
        blocks = [Block("image", "", meta={"kind": "image", "saved_path": str(saved)})]

        rows = _collect_attachments(blocks, base)

        assert rows[0]["saved_path"] == "attachments/image-0.png"


class TestConcurrentStart:
    """Один doc_id — один пайплайн, даже при одновременных запросах.

    Эндпоинты синхронные, FastAPI исполняет их в тредпуле: дабл-клик или ретрай
    клиента дают два реально параллельных POST. Два пайплайна на один документ
    делят staging-каталог и manifest и гоняются на финальном атомарном переносе.
    """

    def _pipeline(self, monkeypatch, started):
        from app.services.pipeline import Pipeline

        p = Pipeline.__new__(Pipeline)  # без Embedder/VectorStore — нужен только _start
        p.registry = DocumentRegistry()
        p.registry.create("doc1", "x.docx", "docx", 10)
        p._abort_events = {}
        p._threads = {}
        p._start_lock = threading.Lock()
        p._chunk_locks = {}
        p._chunk_locks_guard = threading.Lock()

        def fake_run(doc_id, *a, **kw):
            started.append(doc_id)
            time.sleep(0.2)  # поток жив, пока конкуренты пытаются стартовать
            p._abort_events.pop(doc_id, None)
            p._threads.pop(doc_id, None)

        monkeypatch.setattr(p, "_run", fake_run)
        return p

    def test_only_one_of_parallel_starts_wins(self, monkeypatch):
        started: list[str] = []
        p = self._pipeline(monkeypatch, started)
        n = 8
        barrier = threading.Barrier(n)
        errors: list[Exception] = []

        def attempt():
            barrier.wait()
            try:
                p._start("doc1", "/tmp/x.docx", "x.docx", [], False)
            except ValueError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=attempt) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(started) == 1, f"запустилось пайплайнов: {len(started)}"
        assert len(errors) == n - 1, f"отклонено запросов: {len(errors)} из {n - 1}"
        assert all("уже обрабатывается" in str(e) for e in errors)


class TestChunkLockLifetime:
    """Словарь чанк-локов не должен расти на каждый обработанный документ."""

    def _pipeline(self):
        from app.services.pipeline import Pipeline

        p = Pipeline.__new__(Pipeline)
        p._chunk_locks = {}
        p._chunk_locks_guard = threading.Lock()
        return p

    def test_entry_removed_after_use(self):
        p = self._pipeline()
        with p._chunk_lock("doc1"):
            assert "doc1" in p._chunk_locks
        assert p._chunk_locks == {}, "запись лока осталась после выхода"

    def test_entry_removed_after_exception(self):
        p = self._pipeline()
        with pytest.raises(RuntimeError):
            with p._chunk_lock("doc1"):
                raise RuntimeError("сбой backfill")
        assert p._chunk_locks == {}

    def test_concurrent_users_share_one_lock(self):
        """Пока лок кем-то занят, запись жива — иначе второй поток взял бы другой."""
        p = self._pipeline()
        inside = threading.Event()
        release = threading.Event()
        seen: list = []

        def hold():
            with p._chunk_lock("doc1"):
                seen.append(p._chunk_locks["doc1"][0])
                inside.set()
                release.wait(timeout=5)

        t = threading.Thread(target=hold)
        t.start()
        assert inside.wait(timeout=5)
        # Второй участник видит ТУ ЖЕ запись, а не создаёт новую.
        with p._chunk_locks_guard:
            entry = p._chunk_locks["doc1"]
        release.set()
        t.join(timeout=5)

        assert seen and seen[0] is entry[0]
        assert p._chunk_locks == {}, "после выхода последнего запись должна исчезнуть"


class TestSharedPipelineInstance:
    """Состояние обработки общее для всех потребителей пайплайна.

    _threads/_abort_events/_start_lock — поля экземпляра, а Pipeline() звали в
    шести местах (API, bulk-job, корзина, purge). Следствие было не только в
    гонке на старте: soft_delete/remove/remove_if_deleted прерывают живой
    прогон через _abort_events[doc_id], и у свежего экземпляра словарь пуст —
    массовое удаление и purge не останавливали идущую обработку, а она
    дописывала статус и векторы уже удалённому документу.
    """

    def _stub_singleton(self, monkeypatch):
        """Инстанс без Embedder/VectorStore, подставленный как синглгон."""
        from app.services import pipeline as pipeline_mod

        p = pipeline_mod.Pipeline.__new__(pipeline_mod.Pipeline)
        p._abort_events = {}
        p._threads = {}
        p._start_lock = threading.Lock()
        p._chunk_locks = {}
        p._chunk_locks_guard = threading.Lock()

        class _FakeVectorStore:
            def set_document_deleted(self, doc_id, flag):
                pass

        class _FakeRegistry:
            def get(self, doc_id):
                return {"status": "uploaded"}

            def update(self, doc_id, **fields):
                pass

            def soft_delete(self, doc_id, deleted_by=None):
                pass

        p.vector_store = _FakeVectorStore()
        p.registry = _FakeRegistry()
        monkeypatch.setattr(pipeline_mod, "_INSTANCE", p)
        return p

    def test_api_and_services_get_one_instance(self, monkeypatch):
        from app.api import documents as docs
        from app.services.pipeline import get_pipeline

        p = self._stub_singleton(monkeypatch)
        assert docs.get_pipeline() is p
        assert get_pipeline() is p

    def test_soft_delete_aborts_run_started_by_another_consumer(self, monkeypatch):
        """Прогон стартовал через API, удаление пришло из bulk-job — прогон обязан прерваться."""
        from app.services.pipeline import get_pipeline

        p = self._stub_singleton(monkeypatch)
        aborted = threading.Event()

        def fake_run(doc_id, *a, **kw):
            # Живой прогон: ждёт своё abort-событие, как настоящий _process.
            event = p._abort_events[doc_id]
            if event.wait(timeout=5):
                aborted.set()
            p._abort_events.pop(doc_id, None)
            p._threads.pop(doc_id, None)

        monkeypatch.setattr(p, "_run", fake_run)
        p._start("doc1", "/tmp/x.docx", "x.docx", [], False)

        # Потребитель берёт пайплайн так же, как job_queue/trash — через get_pipeline().
        get_pipeline().soft_delete("doc1", deleted_by="demo.admin")

        assert aborted.is_set(), "живой прогон не был прерван удалением"
