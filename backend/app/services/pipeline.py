"""Пайплайн обработки документа: parse -> OKF (staging) -> embed -> index (в фоновом потоке).

Генерация OKF идёт инкрементально: результат каждого чанка LLM сразу пишется в
staging-каталог (data/staging/{doc_id}/) с manifest.json. При сбое на любом чанке
обработанные данные сохраняются (status="paused"), повторный запуск (resume)
пропускает уже готовые чанки. Финальный бандл собирается в okf_bundles через
атомарный перенос, затем концепты индексируются в Qdrant.
"""
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings
from app.services.concept_store import replace_concepts
from app.services.dev_detector import attach_development, detect
from app.services.development_registry import get_development_registry
from app.services.embedder import Embedder
from app.services.errors import DependencyUnavailableError
from app.services import gen_quality
from app.services.json_atomic import write_json_atomic
from app.services.llm_client import LLMTruncationError, is_fatal_error
from app.services.okf_generator import OKFGenerator
from app.services import problem_codes
from app.services.registry import get_registry
from app.services.staging import StagingStore
from app.services.vector_store import VectorStore
from docparser import SUPPORTED_EXTENSIONS, blocks_to_markdown, parse_document

logger = logging.getLogger(__name__)

# Порог детектора no_text_layer: суммарный текст чанков (без markdown-ссылок
# на картинки) короче — считаем документ без текстового слоя (скан без OCR).
MIN_TEXT_LAYER_CHARS = 200

# Markdown-картинки/вложения: ![alt](path) — не текст.
_IMAGE_LINK_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


class Pipeline:
    def __init__(self):
        self.settings = get_settings()
        self.registry = get_registry()
        self.embedder = Embedder()
        self.vector_store = VectorStore()
        self.okf_generator = OKFGenerator()
        self._abort_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._chunk_locks: dict[str, threading.Lock] = {}

    def ingest(
        self,
        doc_id: str,
        filepath: str | Path,
        filename: str,
        user_tags: list[str] | None = None,
    ) -> None:
        self._start(doc_id, str(filepath), filename, user_tags or [], resume=False)

    def resume(self, doc_id: str) -> None:
        doc = self.registry.get(doc_id)
        if not doc:
            raise ValueError("Документ не найден")
        self._ensure_not_running(doc_id)
        filename = doc["filename"]
        ext = Path(filename).suffix.lower()
        filepath = self.settings.uploads_dir / f"{doc_id}{ext}"
        if not filepath.is_file():
            raise ValueError("Исходный файл документа не найден")
        self._start(doc_id, str(filepath), filename, doc.get("tags") or [], resume=True)

    def _ensure_not_running(self, doc_id: str) -> None:
        """Единая защита от запуска второго потока на один и тот же staging.

        Используется всеми входами пайплайна (ingest/resume/regenerate):
        если по doc_id уже живёт поток, повторный запуск отклоняется.
        """
        thread = self._threads.get(doc_id)
        if thread and thread.is_alive():
            raise ValueError("Документ уже обрабатывается")

    def regenerate(self, doc_id: str) -> None:
        """Полная перегенерация концептов документа с нуля (без учёта старых чекпоинтов).

        Удаляет производные данные (векторы, OKF-бандл, staging) и запускает
        полный пайплайн: parse -> чанки -> LLM (с текущими промптами) -> индекс.
        Статус переводится в "processing" синхронно, чтобы клиент сразу видел
        активную обработку и включил поллинг прогресса.
        """
        doc = self.registry.get(doc_id)
        if not doc:
            raise ValueError("Документ не найден")
        self._ensure_not_running(doc_id)
        filename = doc["filename"]
        ext = Path(filename).suffix.lower()
        filepath = self.settings.uploads_dir / f"{doc_id}{ext}"
        if not filepath.is_file():
            raise ValueError("Исходный файл документа не найден")

        try:
            self.vector_store.delete_document(doc_id)
        except Exception as exc:
            logger.warning("Не удалось удалить векторы документа %s: %s", doc_id, exc)
        target = self.settings.okf_dir / doc_id
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        StagingStore(doc_id).remove()
        replace_concepts(doc_id, [])
        # Сброс кэша классификации таблиц: пользователь явно хочет пересчитать
        # концепты с нуля (возможно, после правки промпта/логики классификатора).
        table_cache = self.settings.cache_dir / "table_classify"
        if table_cache.is_dir():
            shutil.rmtree(table_cache, ignore_errors=True)
        self.registry.update(doc_id, status="processing", error=None, problem=None)
        self._start(doc_id, str(filepath), filename, doc.get("tags") or [], resume=False)

    def wait_for(self, doc_id: str, timeout: float = 3600) -> dict:
        """Блокирующее ожидание терминального статуса документа.

        Пайплайн работает в daemon-потоке, который умирает вместе с процессом.
        Поэтому программные триггеры (скрипты, батчи, диагностика) из отдельного
        процесса обязаны держать процесс живым до завершения — иначе документ
        зависнет в промежуточном статусе (processing/splitting/...).

        Пример:
            p = Pipeline()
            p.regenerate(doc_id)
            result = p.wait_for(doc_id)  # блокирует до done/error/failed/paused

        Возвращает финальную запись документа (или текущую по истечении timeout).
        """
        # Дедлайн считаем ДО join: join сам съедает бюджет ожидания, иначе
        # wait_for мог ждать до 2×timeout (join полностью + цикл заново).
        deadline = time.monotonic() + timeout
        thread = self._threads.get(doc_id)
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        terminal = {"done", "error", "failed", "paused"}
        while True:
            doc = self.registry.get(doc_id)
            if doc and doc.get("status") in terminal:
                return doc
            if time.monotonic() >= deadline:
                return doc or {}
            time.sleep(2)

    def _start(self, doc_id: str, filepath: str, filename: str, user_tags: list[str], resume: bool) -> None:
        self._ensure_not_running(doc_id)
        self._abort_events[doc_id] = threading.Event()
        thread = threading.Thread(
            target=self._run,
            args=(doc_id, filepath, filename, user_tags, resume),
            daemon=True,
        )
        self._threads[doc_id] = thread
        thread.start()

    def _run(self, doc_id: str, filepath: str, filename: str, user_tags: list[str], resume: bool) -> None:
        try:
            self._process(doc_id, filepath, filename, user_tags, resume=resume)
        except Exception as exc:
            logger.exception("Ошибка обработки документа %s", filename)
            self.registry.update(doc_id, status="error", error=str(exc))
        finally:
            self._abort_events.pop(doc_id, None)
            self._threads.pop(doc_id, None)

    def _process(self, doc_id: str, filepath: str, filename: str, user_tags: list[str], resume: bool) -> None:
        self.registry.update(doc_id, status="processing", error=None, problem=None)
        attachments_dir = self.settings.okf_dir / doc_id / "attachments"
        blocks = parse_document(filepath, filename, attachments_dir=attachments_dir)
        markdown = blocks_to_markdown(blocks)
        attachments = _collect_attachments(blocks, attachments_dir)

        # Автоопределение номера разработки: только на «свежем» проходе и если
        # regex по имени файла (на этапе upload) ничего не нашёл. Non-fatal —
        # ошибка LLM/справочника не прерывает обработку документа.
        if not resume and self.settings.dev_detection_enabled:
            doc = self.registry.get(doc_id)
            if doc and not doc.get("development_id"):
                detection = detect(markdown, filename, doc_id)
                if detection.confidence is not None:
                    attach_development(doc_id, detection)

        # Дедупликация (Этап 4.2): content_hash + MinHash/LSH-бакеты. Non-fatal —
        # сбой сигнатуры не прерывает обработку, документ просто не участвует в
        # поиске дублей до следующего реиндекса.
        if self.settings.dedup_enabled:
            try:
                from app.services.deduplication import find_duplicates_for_document, index_document

                index_document(doc_id, markdown)
                dup = find_duplicates_for_document(doc_id)
                if dup["level2"] or dup["level3"]:
                    self.registry.update(doc_id, has_duplicates=True)
            except Exception:
                logger.warning(
                    "[%s] Индексация сигнатуры дедупликации не удалась", doc_id, exc_info=True
                )

        chunks = self.okf_generator.chunk_text(markdown)
        total = len(chunks)
        staging = StagingStore(doc_id)
        # Сброс residue телеметрии: события от dev-детекции и пр. не должны
        # приписываться первому чанку.
        gen_quality.drain()
        if resume and staging.exists():
            manifest = staging.load()
            done = len(manifest.get("processed_chunks", [])) if manifest else 0
            logger.info("Resume документа %s: продолжено с %d/%d чанков", doc_id, done, total)
        else:
            if resume:
                logger.warning(
                    "[%s] Возобновление без чекпоинтов: staging отсутствует, генерация начнётся с 0",
                    doc_id,
                )
            if staging.exists():
                staging.remove()
            staging.create(total, global_tags=user_tags)
        self.registry.update(doc_id, status="splitting", total_chunks=total, processed_chunks=len(staging.processed_chunks))

        for i, chunk in enumerate(chunks):
            staging.save_chunk_text(i, chunk)

        max_chunk_retries = self.settings.llm_chunk_retry_attempts
        chunk_backoff = self.settings.llm_chunk_retry_backoff_seconds

        try:
            for i, chunk in enumerate(chunks):
                if staging.has_chunk(i):
                    continue
                if self._abort_events.get(doc_id, threading.Event()).is_set():
                    logger.info("Генерация %s прервана по запросу удаления", doc_id)
                    return

                concepts = None
                degradation: list[dict] = []
                for chunk_attempt in range(1, max_chunk_retries + 1):
                    try:
                        self.registry.update(doc_id, current_chunk=i + 1)
                        concepts = self.okf_generator.generate_chunk(chunk, filename, i + 1, total, doc_id=doc_id)
                        # Телеметрия деградации этого чанка (salvage JSON,
                        # fallback классификатора) — до любых других вызовов.
                        degradation = gen_quality.drain()
                        if self._abort_events.get(doc_id, threading.Event()).is_set():
                            logger.info("Генерация %s прервана после чанка %d", doc_id, i + 1)
                            return
                        self.registry.update(doc_id, error=None)
                        break
                    except Exception as exc:
                        if (
                            isinstance(exc, LLMTruncationError)
                            or is_fatal_error(exc)
                            or chunk_attempt == max_chunk_retries
                        ):
                            self.registry.update(doc_id, error=str(exc))
                            raise
                        delay = chunk_backoff * chunk_attempt
                        msg = (
                            f"Сбой генерации чанка ({exc}). "
                            f"Повтор {chunk_attempt}/{max_chunk_retries} через {int(delay)}с..."
                        )
                        logger.warning("Чанк %d/%d: %s", i + 1, total, msg)
                        self.registry.update(doc_id, error=msg)
                        if self._abort_events.get(doc_id, threading.Event()).wait(timeout=delay):
                            return

                if user_tags and concepts:
                    for concept in concepts:
                        concept.tags = _merge_tags(concept.tags, user_tags)
                if concepts is not None:
                    staging.append_chunk(i, concepts, degradation=degradation)
                self.registry.update(doc_id, processed_chunks=len(staging.processed_chunks), current_chunk=None)
        except Exception as exc:
            logger.warning("Генерация OKF прервана на документе %s: %s", doc_id, exc)
            self.registry.update(
                doc_id,
                status="paused",
                error=str(exc),
                processed_chunks=len(staging.processed_chunks),
            )
            return

        self.registry.update(doc_id, status="indexing")
        try:
            self._finalize(doc_id, filename, staging, attachments=attachments, global_tags=user_tags)
        except DependencyUnavailableError as exc:
            # Staging не удаляем: чекпоинты всех чанков сохраняются, чтобы
            # повторный resume повторил только финализацию (embed+index),
            # не перегенерируя концепты через LLM.
            logger.warning("Финализация документа %s прервана (зависимость недоступна): %s", doc_id, exc.user_message)
            self.registry.update(
                doc_id,
                status="paused",
                error=exc.user_message,
            )
            return
        except Exception as exc:
            # Staging не удаляем: чекпоинты всех чанков сохраняются, чтобы
            # повторный resume повторил только финализацию (embed+index),
            # не перегенерируя концепты через LLM.
            logger.exception("Финализация документа %s не удалась", doc_id)
            self.registry.update(doc_id, status="failed", error=str(exc))
            return

    def _finalize(
        self,
        doc_id: str,
        filename: str,
        staging: StagingStore,
        attachments: list[dict],
        global_tags: list[str],
    ) -> None:
        # Денормализованная проекция разработки (номер/название/модуль) в payload
        # Qdrant `dev_tags` — отдельное поле, не смешивается с `tags`.
        dev_tags: list[str] = []
        doc = self.registry.get(doc_id)
        if doc and doc.get("development_id"):
            dev_tags = get_development_registry().dev_tags(doc["development_id"])

        concepts = staging.concepts()
        slugs = staging.slugs()
        target = self.settings.okf_dir / doc_id
        tmp_dir = self.settings.okf_dir / f".tmp-{doc_id}"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        attach_src = self.settings.okf_dir / doc_id / "attachments"
        if attach_src.is_dir():
            shutil.copytree(attach_src, tmp_dir / "attachments")

        manifest = staging.load() or {}
        chunks_data = manifest.get("chunks_data", {}) or {}
        chunk_of_slug: dict[str, int] = {}
        for idx_str, info in chunks_data.items():
            for slug in (info or {}).get("slugs", []):
                chunk_of_slug.setdefault(slug, int(idx_str))

        chunks_meta: list[dict] = []
        chunks_dir = tmp_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        for chunk_file in sorted(staging.dir.glob("chunk_*.md")):
            idx = int(chunk_file.stem.split("_")[-1])
            info = chunks_data.get(str(idx), {})
            chunks_meta.append(
                {
                    "index": idx,
                    "size": chunk_file.stat().st_size,
                    "concepts_count": info.get("concepts_count", 0),
                }
            )
            shutil.copy2(chunk_file, chunks_dir / chunk_file.name)
        write_json_atomic(chunks_dir / "manifest.json", chunks_meta)

        okf_docs = self.okf_generator.save_bundle(
            doc_id,
            filename,
            concepts,
            attachments=attachments,
            global_tags=global_tags,
            bundle_root=tmp_dir,
            slugs=slugs,
            chunk_of_slug=chunk_of_slug,
        )
        _atomic_move(tmp_dir, target)
        for doc in okf_docs:
            doc.filepath = str(target / Path(doc.filepath).name)

        # Canonical-копия концептов в БД (полный текст, без обрезки до
        # okf_max_concept_chars). При нуле концептов очищает устаревшие записи.
        replace_concepts(doc_id, okf_docs)

        # Problem-коды (инцидент 03.09.2026: done ≠ «документ полон»).
        # Приоритет: no_text_layer/no_concepts (0 концептов) >
        # llm_partial_result (salvage при генерации) > index_partial_failure
        # (чанк-индексация пропущена) — первична причина, из-за которой
        # документ может быть неполон или неищем.
        problem: str | None = None
        if not okf_docs:
            problem = (
                problem_codes.NO_TEXT_LAYER
                if self._chunks_lack_text(doc_id)
                else problem_codes.NO_CONCEPTS
            )
            logger.warning(
                "[%s] Документ %s не содержит концептов (problem=%s); чанки индексируются",
                doc_id, filename, problem,
            )
        elif any((info or {}).get("degradation") for info in chunks_data.values()):
            problem = problem_codes.LLM_PARTIAL_RESULT
            degraded = [
                idx for idx, info in chunks_data.items() if (info or {}).get("degradation")
            ]
            logger.warning(
                "[%s] Документ %s: чанки с деградацией генерации %s (problem=%s)",
                doc_id, filename, degraded, problem,
            )

        self.vector_store.ensure_collection()
        keep_point_ids: set[str] = set()
        if okf_docs:
            cap = self.settings.okf_max_concept_chars
            # Dense-эмбеддинг строится из title + content: title содержит коды/номера
            # разделов (например, "12410"), которые иначе не попадали в вектор и
            # концепт не находился по поиску по коду.
            vectors = self.embedder.embed_texts(
                [f"{doc.metadata.get('title', '')}\n{doc.content[:cap]}" for doc in okf_docs]
            )
            # Upsert-before-delete: сначала записываем новые точки, потом удаляем
            # осиротевшие старые. point_id детерминирован (uuid5 от filepath),
            # поэтому upsert идемпотентно перезаписывает совпадающие точки.
            # Если Qdrant отвалится между upsert и cleanup, новые точки уже на месте.
            concept_point_ids = self.vector_store.index_concepts(doc_id, okf_docs, vectors, dev_tags=dev_tags)
            keep_point_ids = set(concept_point_ids)

        # Чанки индексируются ВСЕГДА, включая документы без концептов: dual-index
        # даёт документу поисковую представленность через chunk-ветку (BM25/dense
        # по сырому тексту), даже когда LLM не создала ни одного концепта.
        if self.settings.search_index_chunks_enabled:
            chunks_dir = target / "chunks"
            chunk_count = len(chunks_meta)
            chunk_texts: list[str] = []
            for i in range(chunk_count):
                chunk_path = chunks_dir / f"chunk_{i:02d}.md"
                if chunk_path.is_file():
                    chunk_texts.append(chunk_path.read_text(encoding="utf-8"))
                else:
                    logger.warning("[%s] Чанк %d не найден в бандле, пропускаем индексацию чанков", doc_id, i)
                    chunk_texts = []
                    break
            if not chunk_texts and chunk_count:
                problem = problem or problem_codes.INDEX_PARTIAL_FAILURE
            if chunk_texts:
                chunk_section_titles = [_extract_section_title(t) for t in chunk_texts]
                cap = self.settings.okf_max_chunk_index_chars
                embed_inputs = []
                for st, t in zip(chunk_section_titles, chunk_texts):
                    embed_inputs.append(f"{st}\n{t[:cap]}" if st else t[:cap])
                chunk_vectors = self.embedder.embed_texts(embed_inputs)
                chunk_point_ids = self.vector_store.index_chunks(
                    doc_id, filename, chunk_texts, global_tags, chunk_vectors,
                    section_titles=chunk_section_titles, dev_tags=dev_tags,
                )
                keep_point_ids |= chunk_point_ids
                logger.info("[%s] Проиндексировано %d чанков", doc_id, len(chunk_texts))

        # Очистка осиротевших старых точек (после успешного upsert новых).
        # Удаляются только точки doc_id, чьи point_id не вошли в новый набор.
        # Для документа без концептов и чанков удаляет ВСЕ старые точки —
        # регенерация в пустоту не оставляет устаревших векторов в поиске.
        self.vector_store.delete_orphaned_points(doc_id, keep_point_ids)

        total_chunks = manifest.get("total_chunks", 0) if manifest else 0
        staging.remove()
        self.registry.update(
            doc_id,
            status="done",
            okf_concept_count=len(okf_docs),
            error=None,
            problem=problem,
        )
        logger.info(
            "Документ %s обработан: %d OKF-концептов, %d чанков%s",
            filename, len(okf_docs), total_chunks,
            f" (problem={problem})" if problem else "",
        )

    @staticmethod
    def _chunks_lack_text(doc_id: str) -> bool:
        """Детектор no_text_layer: чанки документа практически без текста.

        Скан-PDF без OCR даёт чанки из одних markdown-ссылок на картинки
        (инцидент 03.09.2026: «Тренировочная зона», 286 страниц-сканов →
        done с 0 концептов и 0 точек при зелёном статусе). Считаем
        суммарный текст чанков за вычетом ссылок-вложений: короче порога —
        текстового слоя нет.
        """
        chunks_dir = get_settings().okf_dir / doc_id / "chunks"
        if not chunks_dir.is_dir():
            return True
        total = 0
        for md in sorted(chunks_dir.glob("chunk_*.md")):
            text = _IMAGE_LINK_RE.sub("", md.read_text(encoding="utf-8"))
            total += len(text.strip())
        return total < MIN_TEXT_LAYER_CHARS

    def soft_delete(self, doc_id: str, deleted_by: str | None = None) -> None:
        """Мягкое удаление в корзину (Этап 4a.2): помечает, но не удаляет данные.

        Прерывает живой пайплайн, ставит `deleted=true` в Qdrant (set_payload, без
        Delete Points) и `deleted_at` в БД. Векторы/файлы/концепты остаются на
        месте — восстановление не требует пере-эмбеддинга.
        """
        event = self._abort_events.get(doc_id)
        if event:
            event.set()
        thread = self._threads.get(doc_id)
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        try:
            self.vector_store.set_document_deleted(doc_id, True)
        except Exception as exc:
            logger.warning(
                "Не удалось пометить документ %s удалённым в Qdrant: %s", doc_id, exc
            )
        self.registry.soft_delete(doc_id, deleted_by)

    def restore(self, doc_id: str) -> None:
        """Восстановление из корзины: снимает флаг deleted в Qdrant и БД.

        Точки физически не удалялись — эмбеддинги не пересчитываются.
        """
        try:
            self.vector_store.set_document_deleted(doc_id, False)
        except Exception as exc:
            logger.warning(
                "Не удалось снять флаг удаления документа %s в Qdrant: %s", doc_id, exc
            )
        self.registry.restore(doc_id)

    def _physical_cleanup(self, doc_id: str) -> None:
        """Удаляет точки Qdrant, файлы и staging (без строки БД)."""
        try:
            self.vector_store.delete_document(doc_id)
        except Exception as exc:
            logger.warning("Не удалось удалить векторы документа %s: %s", doc_id, exc)
        for base in (self.settings.uploads_dir, self.settings.okf_dir):
            target = base / doc_id
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
        StagingStore(doc_id).remove()
        for f in self.settings.uploads_dir.glob(f"{doc_id}.*"):
            f.unlink(missing_ok=True)

    def remove(self, doc_id: str) -> None:
        event = self._abort_events.get(doc_id)
        if event:
            event.set()
        thread = self._threads.get(doc_id)
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._physical_cleanup(doc_id)
        self.registry.delete(doc_id)

    def remove_if_deleted(self, doc_id: str) -> bool:
        """Физическое удаление с precondition «документ в корзине» (для purge).

        Сначала атомарно удаляет строку БД (registry.delete_if_deleted): если
        документ успели восстановить после purge_expired() — возвращает False,
        не трогая точки/файлы. Удаление строки БД первым — осознанное: поиск
        отсекает хиты с doc_id, отсутствующим в БД (services/search_filter.py),
        поэтому между удалением строки и физической чисткой утекать нечему.
        """
        event = self._abort_events.get(doc_id)
        if event:
            event.set()
        thread = self._threads.get(doc_id)
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        if not self.registry.delete_if_deleted(doc_id):
            return False
        self._physical_cleanup(doc_id)
        return True

    def ensure_chunks(self, doc_id: str) -> list[dict]:
        """Возвращает мету чанков документа, при необходимости строя их из исходника.

        Источники по приоритету:
          1. okf_bundles/{doc_id}/chunks/manifest.json — финализированный бандл;
          2. staging (документ в процессе генерации) — живые чанки;
          3. ленивый backfill: пере-парсинг исходника (без LLM), кэш в бандл.
        """
        bundle_chunks = self.settings.okf_dir / doc_id / "chunks"
        manifest_path = bundle_chunks / "manifest.json"
        if manifest_path.is_file():
            meta = _read_chunks_manifest(manifest_path)
            if meta:
                return meta

        staging = StagingStore(doc_id)
        if staging.exists():
            return _chunks_meta_from_dir(staging.dir, staging.load())

        lock = self._chunk_locks.setdefault(doc_id, threading.Lock())
        with lock:
            if manifest_path.is_file():
                meta = _read_chunks_manifest(manifest_path)
                if meta:
                    return meta
            self._backfill_chunks(doc_id)
            return _read_chunks_manifest(manifest_path)

    def _backfill_chunks(self, doc_id: str) -> None:
        """Строит чанки из исходного файла и кэширует их в бандл (без LLM)."""
        doc = self.registry.get(doc_id)
        if not doc:
            raise ValueError("Документ не найден")
        filename = doc["filename"]
        ext = Path(filename).suffix.lower()
        filepath = self.settings.uploads_dir / f"{doc_id}{ext}"
        if not filepath.is_file():
            raise ValueError("Исходный файл документа не найден")
        scratch = self.settings.okf_dir / f".tmp-chunks-{doc_id}"
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            blocks = parse_document(filepath, filename, attachments_dir=scratch / "attachments")
            markdown = blocks_to_markdown(blocks)
            chunks = self.okf_generator.chunk_text(markdown)
            chunks_dir = scratch / "chunks"
            chunks_dir.mkdir(parents=True, exist_ok=True)
            meta: list[dict] = []
            for i, chunk in enumerate(chunks):
                f = chunks_dir / f"chunk_{i:02d}.md"
                f.write_text(chunk, encoding="utf-8")
                meta.append({"index": i, "size": f.stat().st_size, "concepts_count": 0})
            write_json_atomic(chunks_dir / "manifest.json", meta)
            _atomic_move(chunks_dir, self.settings.okf_dir / doc_id / "chunks")
            logger.info("Backfill чанков %s: %d", doc_id, len(chunks))
        finally:
            if scratch.exists():
                shutil.rmtree(scratch, ignore_errors=True)


def _read_chunks_manifest(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _chunks_meta_from_dir(directory: Path, manifest: dict | None = None) -> list[dict]:
    manifest = manifest or {}
    meta: list[dict] = []
    for f in sorted(directory.glob("chunk_*.md")):
        idx = int(f.stem.split("_")[-1])
        info = manifest.get("chunks_data", {}).get(str(idx), {})
        meta.append(
            {
                "index": idx,
                "size": f.stat().st_size,
                "concepts_count": info.get("concepts_count", 0),
            }
        )
    return meta


def save_upload_stream(
    fileobj: BinaryIO,
    original_filename: str,
    max_bytes: int | None = None,
) -> tuple[str, Path, int]:
    """Потоково сохраняет загруженный файл чанками по 1 МБ.

    Вызывается из обычного def-эндпоинта — FastAPI сам уводит его в threadpool,
    поэтому event loop не блокируется на больших файлах. Лимит размера проверяется
    по факту дочитывания (max_bytes), при превышении файл удаляется и бросается
    ValueError. Возвращает (doc_id, dest, записанные байты).
    """
    ext = Path(original_filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Неподдерживаемый тип файла: {ext}. Допустимы: {sorted(SUPPORTED_EXTENSIONS)}")
    settings = get_settings()
    limit = max_bytes or settings.max_upload_mb * 1024 * 1024
    doc_id = uuid.uuid4().hex[:16]
    dest = settings.uploads_dir / f"{doc_id}{ext}"
    written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dest.open("wb") as out:
            while True:
                chunk = fileobj.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise ValueError(
                        f"Файл превышает максимальный размер {limit // (1024 * 1024)} МБ"
                    )
                out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return doc_id, dest, written


def _atomic_move(src: Path, dst: Path) -> None:
    """Атомарный перенос каталога (src -> dst) в рамках одной ФС.

    Если staging и okf_bundles на разных файловых системах (EXDEV), os.replace
    недоступен — используется shutil.move (copy + delete).
    """
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst, ignore_errors=True)
        else:
            dst.unlink(missing_ok=True)
    try:
        os.replace(src, dst)
    except OSError:
        shutil.move(str(src), str(dst))


def _merge_tags(base: list[str], extra: list[str]) -> list[str]:
    seen = set(base)
    merged = list(base)
    for tag in extra:
        tag = tag.strip()
        if tag and tag not in seen:
            seen.add(tag)
            merged.append(tag)
    return merged


def _collect_attachments(blocks, base_dir: Path) -> list[dict]:
    base = Path(base_dir).resolve()
    attachments = []
    for b in blocks:
        if getattr(b, "type", None) not in ("attachment", "image"):
            continue
        meta = b.meta or {}
        saved = meta.get("saved_path")
        relative = None
        if saved:
            try:
                relative = str(Path(saved).resolve().relative_to(base))
            except ValueError:
                relative = str(Path(saved))
        attachments.append(
            {
                "name": meta.get("name", ""),
                "kind": meta.get("kind", "other"),
                "caption": meta.get("caption", ""),
                "saved_path": relative,
            }
        )
    return attachments


def _extract_section_title(chunk_text: str) -> str:
    """Извлекает ближайший предшествующий заголовок секции из текста чанка.

    Ищет последний '# heading' в первых 50 строках чанка. Если чанк начинается
    с заголовка — возвращает его. Заголовок даёт семантический якорь для
    dense-эмбеддинга и отображается в UI как title чанка.
    """
    lines = chunk_text.split("\n")
    last_heading = ""
    for line in lines[:50]:
        stripped = line.strip()
        if stripped.startswith("#"):
            last_heading = stripped.lstrip("#").strip()
    return last_heading