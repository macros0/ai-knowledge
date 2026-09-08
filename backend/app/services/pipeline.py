"""Пайплайн обработки документа: parse -> OKF (staging) -> embed -> index (в фоновом потоке).

Генерация OKF идёт инкрементально: результат каждого чанка LLM сразу пишется в
staging-каталог (data/staging/{doc_id}/) с manifest.json. При сбое на любом чанке
обработанные данные сохраняются (status="paused"), повторный запуск (resume)
пропускает уже готовые чанки. Финальный бандл собирается в okf_bundles через
атомарный перенос, затем концепты индексируются в Qdrant.
"""
import hashlib
import logging
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings
from app.db.session import session_scope
from app.services.attachment_store import replace_attachments
from app.services.chunk_store import replace_chunks
from app.services.concept_store import replace_concepts
from app.services.dev_detector import attach_development, detect
from app.services.development_registry import get_development_registry
from app.services.embedder import Embedder
from app import error_codes as codes
from app.services.errors import (
    ConflictError,
    DependencyUnavailableError,
    DomainError,
    NotFoundError,
)
from app.services import gen_quality
from app.services.json_atomic import write_json_atomic
from app.services.language import detect_language
from app.services.llm_client import LLMTruncationError, is_fatal_error
from app.services.okf_generator import ATTACHMENT_TAG, OKFGenerator
from app.services import problem_codes
from app.services.registry import get_registry
from app.services.staging import StagingStore
from app.services.vector_store import VectorStore
from docparser import (
    SUPPORTED_EXTENSIONS,
    blocks_to_markdown,
    markdown_attachment_spans,
    parse_document,
    portable_name,
)

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
        # Проверка «не запущен» и регистрация потока должны быть одной
        # атомарной операцией: эндпоинты синхронные, FastAPI исполняет их в
        # тредпуле, поэтому два параллельных POST реально идут параллельно.
        self._start_lock = threading.Lock()
        # doc_id -> [лок, число ожидающих]. Счётчик нужен, чтобы удалять запись
        # по выходу последнего и не растить словарь на каждый документ.
        self._chunk_locks: dict[str, list] = {}
        self._chunk_locks_guard = threading.Lock()

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
            raise NotFoundError("Документ не найден", code=codes.DOCUMENT_NOT_FOUND)
        self._ensure_not_running(doc_id)
        filename = doc["filename"]
        ext = Path(filename).suffix.lower()
        filepath = self.settings.uploads_dir / f"{doc_id}{ext}"
        if not filepath.is_file():
            raise NotFoundError("Исходный файл документа не найден", code=codes.FILE_NOT_FOUND)
        self._start(doc_id, str(filepath), filename, doc.get("tags") or [], resume=True)

    def _ensure_not_running(self, doc_id: str) -> None:
        """Единая защита от запуска второго потока на один и тот же staging.

        Используется всеми входами пайплайна (ingest/resume/regenerate):
        если по doc_id уже живёт поток, повторный запуск отклоняется.
        """
        thread = self._threads.get(doc_id)
        if thread and thread.is_alive():
            raise ConflictError("Документ уже обрабатывается", code=codes.ALREADY_PROCESSING)

    def regenerate(self, doc_id: str) -> None:
        """Полная перегенерация концептов документа с нуля (без учёта старых чекпоинтов).

        Удаляет производные данные (векторы, OKF-бандл, staging) и запускает
        полный пайплайн: parse -> чанки -> LLM (с текущими промптами) -> индекс.
        Статус переводится в "processing" синхронно, чтобы клиент сразу видел
        активную обработку и включил поллинг прогресса.
        """
        doc = self.registry.get(doc_id)
        if not doc:
            raise NotFoundError("Документ не найден", code=codes.DOCUMENT_NOT_FOUND)
        self._ensure_not_running(doc_id)
        filename = doc["filename"]
        ext = Path(filename).suffix.lower()
        filepath = self.settings.uploads_dir / f"{doc_id}{ext}"
        if not filepath.is_file():
            raise NotFoundError("Исходный файл документа не найден", code=codes.FILE_NOT_FOUND)

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
        # Вложения (бинарники) парсер пишет в uploads/<doc_id>/attachments/ — при
        # регенерации чистим их, иначе stale-файлы прежнего парсинга остаются.
        att_dir = self.settings.uploads_dir / doc_id / "attachments"
        if att_dir.is_dir():
            shutil.rmtree(att_dir, ignore_errors=True)
        StagingStore(doc_id).remove()
        with session_scope() as s:
            replace_chunks(s, doc_id, [])
            replace_concepts(s, doc_id, [])
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
            p = get_pipeline()
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
        thread = threading.Thread(
            target=self._run,
            args=(doc_id, filepath, filename, user_tags, resume),
            daemon=True,
        )
        # Всё под одним локом, включая start(): _ensure_not_running смотрит на
        # is_alive(), а у зарегистрированного, но не запущенного потока он ещё
        # False — иначе между регистрацией и стартом осталось бы окно, в которое
        # проходит второй запрос. Два пайплайна на один doc_id — это общий
        # staging, общий manifest и гонка на финальном атомарном переносе.
        with self._start_lock:
            self._ensure_not_running(doc_id)
            self._abort_events[doc_id] = threading.Event()
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
        # Вложения (бинарники) пишутся парсером в uploads/<doc_id>/attachments/ —
        # рядом с оригиналом, а не в будущий бандл (Этап 2b: бандл — производная
        # проекция, вложения — байты-источники в FS, описанные в okf_attachments).
        doc_root = self.settings.uploads_dir / doc_id
        attachments_dir = doc_root / "attachments"
        blocks = parse_document(filepath, filename, attachments_dir=attachments_dir)
        markdown, attach_spans = markdown_attachment_spans(blocks)
        attachments = _collect_attachments(blocks, doc_root)

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
        # Доля символов вложения в каждом чанке (программный тег «attachment»,
        # post-LLM; [] когда вложений нет — быстрый путь без накладных расходов).
        attachment_shares: list[float] = []
        if self.settings.okf_attachment_tag_enabled and attach_spans:
            attachment_shares = self.okf_generator.attachment_shares(markdown, attach_spans)
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
        # Провенанс генерации (Этап 2b): модель/промпт — константы прохода,
        # generated_at — момент успешной генерации конкретного чанка (ниже).
        run_model_id = self.settings.llm_model
        run_prompt_version = self.okf_generator.prompt_version()

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
                if attachment_shares and concepts and attachment_shares[i] >= self.settings.okf_attachment_tag_threshold:
                    for concept in concepts:
                        concept.tags = _merge_tags(concept.tags, [ATTACHMENT_TAG])
                if concepts is not None:
                    provenance = {
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "model_id": run_model_id,
                        "prompt_version": run_prompt_version,
                    }
                    staging.append_chunk(i, concepts, degradation=degradation, provenance=provenance)
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

        manifest = staging.load() or {}
        chunks_data = manifest.get("chunks_data", {}) or {}
        chunk_of_slug: dict[str, int] = {}
        provenance_of_chunk: dict[int, dict] = {}
        for idx_str, info in chunks_data.items():
            info = info or {}
            for slug in info.get("slugs", []):
                chunk_of_slug.setdefault(slug, int(idx_str))
            prov = info.get("provenance")
            if prov:
                try:
                    provenance_of_chunk[int(idx_str)] = prov
                except (TypeError, ValueError):
                    pass

        chunks_meta: list[dict] = []
        chunk_rows: list[dict] = []
        chunk_files: list[tuple[Path, int, str]] = []
        for chunk_file in sorted(staging.dir.glob("chunk_*.md")):
            idx = int(chunk_file.stem.split("_")[-1])
            info = chunks_data.get(str(idx)) or {}
            text = chunk_file.read_text(encoding="utf-8")
            chunk_files.append((chunk_file, idx, text))
            chunks_meta.append(
                {
                    "index": idx,
                    "size": chunk_file.stat().st_size,
                    "concepts_count": info.get("concepts_count", 0),
                }
            )
            chunk_rows.append(
                {
                    "chunk_index": idx,
                    "section_title": _extract_section_title(text),
                    "content": text,
                    "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "char_count": len(text),
                }
            )

        # Этап 2b / Фаза 5: бандл — экспорт, не рабочее состояние. По умолчанию
        # okf_write_bundles=false — okf_docs строятся в памяти (БД — canonical),
        # файлы .md не пишутся. При true (dual-write) — как раньше: tmp + atomic move.
        if self.settings.okf_write_bundles:
            target = self.settings.okf_dir / doc_id
            tmp_dir = self.settings.okf_dir / f".tmp-{doc_id}"
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_dir.mkdir(parents=True, exist_ok=True)

            attach_src = self.settings.uploads_dir / doc_id / "attachments"
            if attach_src.is_dir():
                shutil.copytree(attach_src, tmp_dir / "attachments")

            chunks_dir = tmp_dir / "chunks"
            chunks_dir.mkdir(parents=True, exist_ok=True)
            for chunk_file, _idx, _text in chunk_files:
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
        else:
            okf_docs, _manifest = self.okf_generator.build_okf_docs(
                doc_id,
                filename,
                concepts,
                attachments=attachments,
                global_tags=global_tags,
                slugs=slugs,
                chunk_of_slug=chunk_of_slug,
            )

        # Провенанс генерации (Этап 2b): per-chunk generated_at/model_id/prompt
        # из staging chunks_data — в metadata концептов, откуда replace_concepts
        # пишет их в okf_concepts. Не путать с created_at (время SQL INSERT).
        for doc in okf_docs:
            ci = doc.metadata.get("chunk_index")
            prov = provenance_of_chunk.get(ci) if ci is not None else None
            if prov:
                doc.metadata["generated_at"] = prov.get("generated_at")
                doc.metadata["model_id"] = prov.get("model_id")
                doc.metadata["prompt_version"] = prov.get("prompt_version")

        # Единая транзакция финализации БД: чанки + концепты (+prov) + вложения.
        # Commit — на выходе из session_scope; Qdrant вызывается строго ПОСЛЕ
        # успешного commit (он пересобираемая проекция БД). Падение Qdrant после
        # commit оставляет документ paused — resume идемпотентно повторяет
        # replace_* + upsert векторов.
        with session_scope() as session:
            replace_chunks(session, doc_id, chunk_rows)
            replace_concepts(session, doc_id, okf_docs)
            replace_attachments(
                session,
                doc_id,
                attachments,
                storage_root=self.settings.uploads_dir / doc_id,
            )

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
        # Этап 2b: текст чанков берётся из chunk_rows (в памяти), а не из бандла.
        if self.settings.search_index_chunks_enabled:
            chunk_count = len(chunk_rows)
            if chunk_count:
                chunk_texts = [r["content"] for r in chunk_rows]
                chunk_section_titles = [r["section_title"] or "" for r in chunk_rows]
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
            elif chunks_meta:
                problem = problem or problem_codes.INDEX_PARTIAL_FAILURE

        # Очистка осиротевших старых точек (после успешного upsert новых).
        # Удаляются только точки doc_id, чьи point_id не вошли в новый набор.
        # Для документа без концептов и чанков удаляет ВСЕ старые точки —
        # регенерация в пустоту не оставляет устаревших векторов в поиске.
        self.vector_store.delete_orphaned_points(doc_id, keep_point_ids)

        total_chunks = manifest.get("total_chunks", 0) if manifest else 0
        staging.remove()
        source_locale = detect_language(
            "".join(r["content"] for r in chunk_rows)[:100_000]
        )
        self.registry.update(
            doc_id,
            status="done",
            okf_concept_count=len(okf_docs),
            error=None,
            problem=problem,
            source_locale=source_locale,
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
        done с 0 концептов и 0 точек при зелёном статусе). Считаем суммарный
        текст чанков за вычетом ссылок-вложений: короче порога — текстового
        слоя нет. Этап 2b: читает document_chunks (БД), а не бандл.
        """
        from app.db.models import DocumentChunk
        from app.db.session import session_scope

        with session_scope() as s:
            rows = s.query(DocumentChunk.content).filter(DocumentChunk.doc_id == doc_id).all()
        total = 0
        for (content,) in rows:
            text = _IMAGE_LINK_RE.sub("", content or "")
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

    @contextmanager
    def _chunk_lock(self, doc_id: str):
        """Лок на ленивый backfill чанков документа, живущий не дольше нужды.

        Раньше словарь только рос — по объекту на каждый документ, обработанный
        за всё время жизни процесса. Удалять запись в finally прогона (_run,
        рядом с _threads/_abort_events) нельзя: этот лок берёт и read-путь
        (ensure_chunks), не связанный с прогоном. Удаление занятого лока
        привело бы к тому, что следующий вызов создаст ДРУГОЙ объект и два
        потока зайдут в backfill одновременно — ровно то, от чего лок и стоит.
        Поэтому запись удаляет тот, кто вышел последним.
        """
        with self._chunk_locks_guard:
            entry = self._chunk_locks.get(doc_id)
            if entry is None:
                entry = [threading.Lock(), 0]
                self._chunk_locks[doc_id] = entry
            entry[1] += 1
            lock = entry[0]
        try:
            with lock:
                yield
        finally:
            with self._chunk_locks_guard:
                entry[1] -= 1
                if entry[1] <= 0 and self._chunk_locks.get(doc_id) is entry:
                    del self._chunk_locks[doc_id]

    def ensure_chunks(self, doc_id: str) -> list[dict]:
        """Возвращает мету чанков документа, при необходимости строя их из исходника.

        Источники по приоритету (Этап 2b — PostgreSQL SSOT):
          1. document_chunks (БД) — канонический источник текста чанков;
          2. staging (документ в процессе генерации) — живые чанки;
          3. ленивый backfill: пере-парсинг исходника (без LLM) в document_chunks.
        """
        meta = _chunks_meta_from_db(doc_id)
        if meta:
            return meta

        staging = StagingStore(doc_id)
        if staging.exists():
            return _chunks_meta_from_dir(staging.dir, staging.load())

        with self._chunk_lock(doc_id):
            meta = _chunks_meta_from_db(doc_id)
            if meta:
                return meta
            self._backfill_chunks(doc_id)
            return _chunks_meta_from_db(doc_id)

    def _backfill_chunks(self, doc_id: str) -> None:
        """Строит чанки из исходного файла и пишет их в document_chunks (без LLM).

        Этап 2b: чанки — канонически в БД; FS-бандл больше не кэш для этого пути.
        """
        doc = self.registry.get(doc_id)
        if not doc:
            raise NotFoundError("Документ не найден", code=codes.DOCUMENT_NOT_FOUND)
        filename = doc["filename"]
        ext = Path(filename).suffix.lower()
        filepath = self.settings.uploads_dir / f"{doc_id}{ext}"
        if not filepath.is_file():
            raise NotFoundError("Исходный файл документа не найден", code=codes.FILE_NOT_FOUND)
        blocks = parse_document(
            filepath, filename,
            attachments_dir=self.settings.uploads_dir / doc_id / "attachments",
        )
        markdown = blocks_to_markdown(blocks)
        chunks = self.okf_generator.chunk_text(markdown)
        rows = [
            {
                "chunk_index": i,
                "section_title": _extract_section_title(chunk),
                "content": chunk,
                "content_hash": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                "char_count": len(chunk),
            }
            for i, chunk in enumerate(chunks)
        ]
        with session_scope() as s:
            replace_chunks(s, doc_id, rows)
        logger.info("Backfill чанков %s: %d", doc_id, len(chunks))


def _chunks_meta_from_db(doc_id: str) -> list[dict]:
    """Мета чанков из document_chunks (БД): [{index, size, concepts_count}]."""
    from sqlalchemy import func

    from app.db.models import DocumentChunk, OkfConcept

    with session_scope() as s:
        chunks = (
            s.query(DocumentChunk)
            .filter(DocumentChunk.doc_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        )
        if not chunks:
            return []
        counts = dict(
            s.query(OkfConcept.chunk_index, func.count())
            .filter(OkfConcept.doc_id == doc_id, OkfConcept.chunk_index.isnot(None))
            .group_by(OkfConcept.chunk_index)
            .all()
        )
    return [
        {
            "index": c.chunk_index,
            "size": len((c.content or "").encode("utf-8")),
            "concepts_count": counts.get(c.chunk_index, 0),
        }
        for c in chunks
    ]


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
    DomainError. Возвращает (doc_id, dest, записанные байты).

    Оба отказа несут стабильный код (unsupported_file_type / file_too_large):
    раньше это были неразличимые ValueError, и роутер выбирал статус по
    подстроке русского сообщения — управляющий поток на тексте диагностики.
    """
    ext = Path(original_filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise DomainError(
            f"Неподдерживаемый тип файла: {ext}. Допустимы: {sorted(SUPPORTED_EXTENSIONS)}",
            code=codes.UNSUPPORTED_FILE_TYPE,
        )
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
                    raise DomainError(
                        f"Файл превышает максимальный размер {limit // (1024 * 1024)} МБ",
                        code=codes.FILE_TOO_LARGE,
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
    """Собирает метаданные вложений (маркер-блоки + изображения).

    base_dir — корень хранилища документа (uploads/<doc_id>/), поэтому saved_path
    выходит относительным ("attachments/<имя>"). Бинарники остаются в FS; строка
    БД (okf_attachments) — источник истины об их принадлежности/статусе.
    """
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
                relative = Path(saved).resolve().relative_to(base).as_posix()
            except ValueError:
                # Путь не под base_dir: бандл с другой машины/ОС (перенос данных,
                # старый staging). as_posix() здесь НЕ нормализует windows-
                # разделители — Path берёт флейвор текущей ОС, и в БД лёг бы
                # абсолютный путь машины-источника, который дальше течёт в
                # выгрузку бандла (инцидент 2026-09-07). Приводим к тому же
                # каноническому виду, что и остальные: attachments/<файл>.
                relative = f"attachments/{portable_name(saved)}"
        parsed = bool(meta.get("parsed"))
        note = meta.get("note", "")
        if parsed:
            status = "parsed"
        elif note and "глубина" in note:
            status = "skipped_depth"
        elif note and "лимит" in note:
            status = "skipped_size"
        elif saved:
            status = "saved"
        else:
            status = "unsupported"
        attachments.append(
            {
                "name": meta.get("name", ""),
                "kind": meta.get("kind", "other"),
                "caption": meta.get("caption", ""),
                "saved_path": relative,
                "is_processable": parsed,
                "extraction_status": status,
            }
        )
    return attachments


_INSTANCE: Pipeline | None = None
_INSTANCE_LOCK = threading.Lock()


def get_pipeline() -> Pipeline:
    """Общий для процесса пайплайн — один инстанс на всех потребителей.

    Состояние обработки (_threads, _abort_events, _start_lock, _chunk_locks) —
    поля экземпляра, поэтому у каждого `Pipeline()` они свои и пустые. Пока
    потребители создавали инстансы сами (bulk-job, корзина, purge), это давало
    два дефекта:

      - _start_lock защищал от параллельного старта только внутри своего
        экземпляра — окно, которое закрывал 40c806b, оставалось открытым между
        экземплярами;
      - soft_delete/remove/remove_if_deleted прерывают живой прогон через
        _abort_events[doc_id]; у свежего экземпляра словарь пуст, поэтому
        массовое удаление и purge не прерывали идущую обработку — пайплайн
        дописывал статус и векторы уже удалённому документу.

    Оба лечатся одним общим инстансом. Прямой `Pipeline()` остаётся законным
    для изолированных потребителей (тесты, offline-скрипты), которым разделять
    состояние не с кем.
    """
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = Pipeline()
        return _INSTANCE


def reset_pipeline() -> None:
    """Сбрасывает синглтон (для тестов — по образцу db.session.configure_for_tests).

    Pipeline фиксирует get_settings() в __init__, а каждый тест поднимает свои
    settings поверх временного каталога. Без сброса первый же тест, дошедший до
    get_pipeline(), закреплял бы свои пути за всем прогоном — вплоть до
    _physical_cleanup, чистящего каталог чужого теста.
    """
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = None


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