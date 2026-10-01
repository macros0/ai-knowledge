"""Пайплайн обработки документа: parse -> OKF (staging) -> embed -> index (в фоновом потоке).

Генерация OKF идёт инкрементально: результат каждого чанка LLM сразу пишется в
staging-каталог (data/staging/{doc_id}/) с manifest.json. При сбое на любом чанке
обработанные данные сохраняются (status="paused"), повторный запуск (resume)
пропускает уже готовые чанки. Финальный бандл собирается в okf_bundles через
атомарный перенос, затем концепты индексируются в Qdrant.
"""
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings
from app.services.diagnostics.context import bind_context, current_context, new_operation, run_bound, set_generation_id
from app.services.diagnostics.events import operation_span, start_stage, finish_stage, record_failure
from app.services.diagnostics.recorder import emit_event
from app.db.session import session_scope
from app.db.models import DocumentChunk, DocumentGeneration, DocumentGenerationState
from app.services.chunk_store import replace_chunks
from app.services.dev_detector import detect
from app.services.development_registry import get_development_registry
from app.services.embedder import Embedder
from app import error_codes as codes
from app.services.errors import processing_error_code
from app.services.field_table import set_cache_fresh_since as set_table_cache_fresh_since
from app.services.errors import (
    ConflictError,
    DependencyUnavailableError,
    DomainError,
    NotFoundError,
)
from app.services import gen_quality
from app.services.generation_files import generation_paths, merge_legacy_backfill_files, prepare_generation_paths
from app.services.generation_artifacts import artifact_manifest, file_digest
from app.services.generation_publication import _merge_tags, publish_prepared_document
from app.services.generation_store import (
    lock_document_write, mark_generation_ready, prepare_generation_attempt,
)
from app.services.document_update import (
    DocumentUpdateCancelled, capture_published_update, request_update_cancel, restore_canceled_update,
)
from app.services.json_atomic import write_json_atomic
from app.services.language import detect_language
from app.services.llm_client import LLMTruncationError, is_fatal_error
from app.services.okf_generator import ATTACHMENT_TAG, OKFGenerator
from app.services import problem_codes
from app.services.registry import STALE_STATUSES, get_registry
from app.services.source_chunking import attachment_shares_by_source, chunk_blocks_by_source, indexable_blocks
from app.services.source_store import replace_sources
from app.services.parser_supervisor import (
    ParserBusyError,
    ParserIsolationError,
    ParserMemoryLimitError,
    ParserTimeoutError,
    ParserWorkerError,
    parse_document_supervised,
)
from app.services.parse_diagnostics import source_extraction_status, summarize_problems
from app.services.staging import StagingStore
from app.services.storage import (
    StorageFullError,
    clear_transient_storage_failure,
    is_storage_full,
    mark_transient_storage_failure,
    storage_failure_lock,
    transient_storage_failure_is_active,
)
from app.services.vector_store import VectorStore
from docparser import (
    SUPPORTED_EXTENSIONS,
    blocks_to_markdown,
    markdown_attachment_spans,
    parse_document,
    portable_name,
    ParseContext,
    PARSER_VERSION,
)

logger = logging.getLogger(__name__)

# Порог детектора no_text_layer: суммарный текст чанков (без markdown-ссылок
# на картинки) короче — считаем документ без текстового слоя (скан без OCR).
MIN_TEXT_LAYER_CHARS = 200


def validate_resume_parser_version(manifest: dict, parser_version: str) -> None:
    """Reject only versioned checkpoints created by a different parser."""
    checkpoint_version = manifest.get("parser_version")
    if checkpoint_version is not None and checkpoint_version != parser_version:
        raise DomainError(
            "Версия извлечения изменилась; запустите полную перегенерацию",
            code=codes.PARSER_VERSION_MISMATCH,
        )


def validate_resume_source_file_hash(manifest: dict, source_file_hash: str) -> None:
    """Reject a versioned checkpoint when its root file bytes changed."""
    checkpoint_hash = manifest.get("source_file_hash")
    if checkpoint_hash is not None and checkpoint_hash != source_file_hash:
        raise DomainError(
            "Изменился исходный файл; запустите полную перегенерацию",
            code=codes.PARTIAL_REGENERATION_UNAVAILABLE,
        )


_TABLE_CACHE_MARKER = "table-cache.json"

# Ленивый backfill чанков из HTTP-чтения повторяется не чаще этого интервала
# для документа, чей разбор упал или не дал текста (скан без OCR): иначе каждое
# открытие страницы заново запускало бы разбор до PARSER_TIMEOUT_SECONDS.
_INTERACTIVE_BACKFILL_RETRY_SECONDS = 600.0


class ChunkBackfillBusyError(DomainError):
    """Ленивый backfill уже идёт — этот документ или другой (лимит процесса)."""

    code = codes.RATE_LIMITED


def _table_cache_fresh_since(uploads_root: Path, fresh: bool, resume: bool) -> float | None:
    """Граница свежести кэша классификатора таблиц для этой попытки генерации.

    Перегенерация фиксирует момент старта в каталоге попытки, и resume той же
    попытки продолжает игнорировать записи кэша, сделанные до перегенерации.
    Обычная обработка использует общий кэш без ограничений (None).
    """
    marker = uploads_root / _TABLE_CACHE_MARKER
    if fresh:
        since = time.time()
        write_json_atomic(marker, {"fresh_since": since})
        return since
    if resume and marker.is_file():
        try:
            return float(json.loads(marker.read_text(encoding="utf-8"))["fresh_since"])
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Повреждённая отметка свежести кэша таблиц: %s", marker)
    return None


def _sha256_file(filepath: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(filepath).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Markdown-картинки/вложения: ![alt](path) — не текст.
_IMAGE_LINK_RE = re.compile(r"(?<!\\)!\[(?:\\.|[^\\\]])*\]\((?:\\.|[^\\)])*\)")


def _generation_problem(chunks_data: dict) -> str | None:
    """Map generation degradation events to the most specific problem code."""
    events = {
        item.get("event")
        for info in chunks_data.values()
        for item in ((info or {}).get("degradation") or [])
        if isinstance(item, dict)
    }
    if gen_quality.LLM_SALVAGE in events:
        return problem_codes.LLM_PARTIAL_RESULT
    if gen_quality.CLASSIFIER_FALLBACK in events:
        return problem_codes.LLM_CLASSIFIER_FALLBACK
    return None


def _source_locale_fields(detected: str | None, current_source: str | None) -> dict:
    """Поля source_locale для финализации с учётом ручной правки (Этап 7 фаза D).

    Ручная правка (`source_locale_source == 'manual'`) не перезаписывается
    повторным `regenerate` — возвращается пустой dict. Иначе значение
    пересчитывается: `detected` ставится с `source='detected'`, `None` (пустой
    текст) — сбрасывает и значение, и источник. Чистая функция — юнит-тестируема.
    """
    if current_source == "manual":
        return {}
    return {
        "source_locale": detected,
        "source_locale_source": "detected" if detected else None,
    }


class Pipeline:
    def __init__(self):
        self.settings = get_settings()
        self.registry = get_registry()
        self.embedder = Embedder()
        self.vector_store = VectorStore()
        self.okf_generator = OKFGenerator()
        self._abort_events: dict[str, threading.Event] = {}
        self._threads: dict[str, Future] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=self.settings.pipeline_max_workers,
            thread_name_prefix="document-pipeline",
        )
        self._pipeline_slots = threading.BoundedSemaphore(
            self.settings.pipeline_max_workers
            + max(self.settings.pipeline_max_pending, self.settings.pipeline_admin_max_pending)
        )
        # Проверка «не запущен» и регистрация потока должны быть одной
        # атомарной операцией: эндпоинты синхронные, FastAPI исполняет их в
        # тредпуле, поэтому два параллельных POST реально идут параллельно.
        self._start_lock = threading.Lock()
        # doc_id -> [лок, число ожидающих]. Счётчик нужен, чтобы удалять запись
        # по выходу последнего и не растить словарь на каждый документ.
        self._chunk_locks: dict[str, list] = {}
        self._chunk_locks_guard = threading.Lock()
        # Ленивый backfill из HTTP-чтения: один разбор на процесс, чтобы чтения
        # не занимали слоты парсера, нужные загрузкам (общий PARSER_MAX_CONCURRENT).
        self._interactive_backfill_slots = threading.BoundedSemaphore(1)
        # doc_id -> (истекает, код ошибки или None — «текста нет», сообщение).
        self._interactive_backfill_outcomes: dict[str, tuple[float, str | None, str]] = {}
        self._storage_failure_docs: set[tuple[str, int]] = set()
        self._storage_failure_docs_lock = threading.Lock()

    def _queue_limit(self, is_admin: bool) -> int:
        regular_limit = getattr(self.settings, "pipeline_max_pending", 0)
        return getattr(self.settings, "pipeline_admin_max_pending", regular_limit) if is_admin else regular_limit

    def queue_status(self, *, is_admin: bool = False) -> dict[str, int]:
        """Snapshot of this process's document admission slots, without document details."""
        with self._start_lock:
            tasks = tuple(self._threads.values())
        processing = sum(task.running() for task in tasks)
        queue_limit = self._queue_limit(is_admin)
        capacity = self.settings.pipeline_max_workers + queue_limit
        return {
            "processing": processing,
            "processing_limit": self.settings.pipeline_max_workers,
            "queued": len(tasks) - processing,
            "queue_limit": queue_limit,
            "available": max(0, capacity - len(tasks)),
        }

    def ingest(
        self,
        doc_id: str,
        filepath: str | Path,
        filename: str,
        user_tags: list[str] | None = None,
        *, is_admin: bool = False,
    ) -> None:
        self._start(doc_id, str(filepath), filename, user_tags or [], resume=False, is_admin=is_admin)

    def resume(self, doc_id: str, *, is_admin: bool = False) -> None:
        doc = self.registry.get(doc_id)
        if not doc:
            raise NotFoundError("Документ не найден", code=codes.DOCUMENT_NOT_FOUND)
        self._ensure_not_running(doc_id)
        if doc.get("status") == "done":
            staging = StagingStore(doc_id)
            manifest = staging.load() or {}
            if not staging.partial_chunks or any(
                not (staging.dir / f"chunk_{i:02d}.{ext}").is_file()
                for i in range(manifest.get("total_chunks", 0))
                for ext in ("md", "json")
            ):
                raise DomainError("Нет контрольных данных для догенерации", code=codes.PARTIAL_REGENERATION_UNAVAILABLE)
        filename = doc["filename"]
        ext = Path(filename).suffix.lower()
        filepath = self.settings.uploads_dir / f"{doc_id}{ext}"
        if not filepath.is_file():
            raise NotFoundError("Исходный файл документа не найден", code=codes.FILE_NOT_FOUND)
        self._start(doc_id, str(filepath), filename, doc.get("tags") or [], resume=True, is_admin=is_admin)

    def _ensure_not_running(self, doc_id: str) -> None:
        """Единая защита от запуска второго потока на один и тот же staging.

        Используется всеми входами пайплайна (ingest/resume/regenerate):
        если по doc_id уже живёт поток, повторный запуск отклоняется.
        """
        task = self._threads.get(doc_id)
        if task and not task.done():
            raise ConflictError("Документ уже обрабатывается", code=codes.ALREADY_PROCESSING)

    def regenerate(self, doc_id: str, *, is_admin: bool = False) -> None:
        """Полная перегенерация концептов документа с нуля (без учёта старых чекпоинтов).

        Сбрасывает staging и запускает новую версию. Опубликованные данные
        остаются доступны до успешной финализации новой попытки.
        После допуска в очередь статус меняется синхронно. Отклонённый запуск
        не удаляет checkpoints и не меняет состояние документа.
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

        self._start(doc_id, str(filepath), filename, doc.get("tags") or [], resume=False,
                    reset_staging=True, is_admin=is_admin)

    def wait_for(self, doc_id: str, timeout: float = 3600) -> dict:
        """Блокирующее ожидание терминального статуса документа.

        Пайплайн работает в in-process executor, который умирает вместе с процессом.
        Поэтому программные триггеры (скрипты, батчи, диагностика) из отдельного
        процесса обязаны держать процесс живым до завершения — иначе документ
        зависнет в промежуточном статусе (processing/splitting/...).

        Пример:
            p = get_pipeline()
            p.regenerate(doc_id)
            result = p.wait_for(doc_id)  # блокирует до done/error/failed/paused

        Возвращает финальную запись документа (или текущую по истечении timeout).
        """
        deadline = time.monotonic() + timeout
        terminal = {"done", "error", "failed", "paused"}
        while True:
            doc = self.registry.get(doc_id)
            if doc and doc.get("status") in terminal:
                return doc
            if time.monotonic() >= deadline:
                return doc or {}
            time.sleep(2)

    def cancel_update(
        self, doc_id: str, expected_update_id: str | None = None, *, user=None, ip_address=None,
    ) -> None:
        """Prevent publication durably; let a running worker stop before cleanup."""
        with self._start_lock:
            with session_scope() as session:
                from app.db.models import Document, DocumentGenerationState, DocumentUpdateAttempt

                if not lock_document_write(session, doc_id, allow_deleted=False):
                    raise NotFoundError("Документ не найден", code=codes.DOCUMENT_NOT_FOUND)
                attempt = session.get(DocumentUpdateAttempt, doc_id, populate_existing=True)
                if attempt is None:
                    document = session.get(Document, doc_id, populate_existing=True)
                    state = session.get(DocumentGenerationState, doc_id, populate_existing=True)
                    if (document.status == "done" or not state or not state.active_generation_id
                            or (expected_update_id and expected_update_id != state.active_generation_id)):
                        raise ConflictError("Нет текущего обновления для отмены", code=codes.DOCUMENT_UPDATE_CONFLICT)
                    capture_published_update(session, doc_id, self.settings)
                    session.flush()
                    attempt = session.get(DocumentUpdateAttempt, doc_id)
                    expected_update_id = attempt.id
                already_requested = attempt.cancel_requested
                request_update_cancel(session, doc_id, expected_update_id)
                if user is not None and not already_requested:
                    from app.services import audit

                    audit.record_in_session(
                        session, action_type=audit.DOCUMENT_UPDATE_CANCEL,
                        user_id=user.user_id, username=user.username, target_type=audit.TARGET_DOCUMENT,
                        target_id=doc_id, ip_address=ip_address,
                        new_value={"update_id": attempt.id, "previous_version_preserved": True},
                    )
            event = self._abort_events.get(doc_id)
            if event:
                event.set()
            task = self._threads.get(doc_id)
            if task and not task.done():
                if not task.cancel():
                    return  # The worker owns all its files until it exits.
                self._threads.pop(doc_id, None)
                self._abort_events.pop(doc_id, None)
                self._pipeline_slots.release()
            self._finish_canceled_update(doc_id)
            self._cleanup_document_generations(doc_id)

    def _finish_canceled_update(self, doc_id: str) -> None:
        with storage_failure_lock():
            with session_scope() as session:
                restored = restore_canceled_update(session, doc_id)
            if restored:
                clear_transient_storage_failure(doc_id)
                try:
                    StagingStore(doc_id).remove()
                except Exception:
                    logger.warning("[%s] Не удалось очистить отменённые checkpoints", doc_id, exc_info=True)

    def _start(
        self, doc_id: str, filepath: str, filename: str, user_tags: list[str], resume: bool,
        *, reset_staging: bool = False, is_admin: bool = False,
    ) -> None:
        with self._start_lock:
            self._ensure_not_running(doc_id)
            # Keep lightweight Pipeline.__new__ test doubles compatible with the
            # admission control; production instances initialize these in __init__.
            if not hasattr(self, "_pipeline_slots"):
                self._pipeline_slots = threading.BoundedSemaphore(1)
            if not hasattr(self, "_executor"):
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="document-pipeline")
            # Both roles share the executor and FIFO; an editor cannot consume
            # the larger admin ceiling even when physical semaphore slots remain.
            capacity = getattr(self.settings, "pipeline_max_workers", 1) + self._queue_limit(is_admin)
            if len(self._threads) >= capacity or not self._pipeline_slots.acquire(blocking=False):
                raise DomainError(
                    "Очередь обработки документов перегружена",
                    code=codes.QUEUE_OVERLOADED,
                )
            self._abort_events[doc_id] = threading.Event()
            previous_state = None
            captured_update = False
            previous_update_id = None
            try:
                doc = self.registry.get(doc_id)
                if doc is None or doc.get("deleted_at") is not None:
                    raise NotFoundError("Документ не найден", code=codes.DOCUMENT_NOT_FOUND)
                previous_state = {key: doc.get(key) for key in ("status", "error", "error_code")}
                previous_update_id = doc.get("update_id")
                with session_scope() as session:
                    captured_update = capture_published_update(session, doc_id, self.settings, fresh=not resume)
                if reset_staging:
                    # Admission and destructive checkpoint reset share the same
                    # lock: a second regenerate must not erase a running worker.
                    # Общий кэш классификатора таблиц не трогаем: «с нуля» для
                    # этого документа обеспечивает граница свежести в _process.
                    StagingStore(doc_id).remove()
                # Publish admission before submit: a busy executor may not start
                # this document for minutes, and resume must stop showing paused.
                self.registry.update(doc_id, status="queued", error=None, error_code=None)
                context = new_operation(current_context(), doc_id=doc_id)
                task = self._executor.submit(
                    run_bound, context, self._run, doc_id, filepath, filename, user_tags, resume, reset_staging,
                )
            except Exception:
                self._abort_events.pop(doc_id, None)
                self._pipeline_slots.release()
                if previous_state is not None:
                    self.registry.update(doc_id, **previous_state)
                if captured_update or previous_update_id:
                    from app.db.models import DocumentUpdateAttempt

                    with session_scope() as session:
                        attempt = session.get(DocumentUpdateAttempt, doc_id)
                        if attempt:
                            if captured_update:
                                session.delete(attempt)
                            else:
                                attempt.id = previous_update_id
                raise
            self._threads[doc_id] = task

    def _run(
        self, doc_id: str, filepath: str, filename: str, user_tags: list[str], resume: bool,
        fresh_table_cache: bool = False,
    ) -> None:
        # Wait until _start registers the Future before processing/cleanup.
        with self._start_lock:
            pass
        try:
            self._process(
                doc_id, filepath, filename, user_tags, resume=resume,
                fresh_table_cache=fresh_table_cache,
            )
        except DocumentUpdateCancelled:
            logger.info("[%s] Обновление отменено", doc_id)
        except Exception as exc:
            logger.exception("Ошибка обработки документа %s", filename)
            if is_storage_full(exc):
                self._record_storage_full(doc_id)
            else:
                self.registry.update(doc_id, status="error", error=str(exc), error_code=processing_error_code(exc))
        finally:
            # Поток executor переиспользуется: граница свежести кэша не должна
            # перейти к следующему документу.
            set_table_cache_fresh_since(None)
            aborted = self._abort_events.get(doc_id)
            if aborted and aborted.is_set():
                try:
                    doc = self.registry.get(doc_id)
                    if doc and doc.get("status") in {"queued", "processing", "splitting", "indexing"}:
                        self.registry.update(doc_id, status="paused", error=None, error_code=None)
                except Exception:
                    logger.warning("[%s] Не удалось сохранить остановку обработки", doc_id, exc_info=True)
            try:
                self._finish_canceled_update(doc_id)
            except Exception:
                logger.exception("[%s] Возврат опубликованной версии будет повторён при старте", doc_id)
            self._cleanup_document_generations(doc_id)
            self._abort_events.pop(doc_id, None)
            self._threads.pop(doc_id, None)
            self._pipeline_slots.release()

    def _cleanup_document_generations(self, doc_id: str) -> None:
        from app.services.generation_cleanup import cleanup_document_generations

        try:
            cleanup_document_generations(self.settings, self.vector_store, doc_id)
        except Exception:
            # Cleanup cannot change the already committed publication/status.
            # Generation rows remain in the DB for the periodic retry.
            logger.warning("[%s] Очистка старых версий будет повторена", doc_id, exc_info=True)

    def cleanup_inactive_generations(self) -> None:
        from sqlalchemy import or_, select
        from app.db.models import DocumentUpdateAttempt

        with session_scope() as session:
            doc_ids = list(session.scalars(select(DocumentGeneration.doc_id).where(or_(
                DocumentGeneration.phase.in_(["retired", "abandoned"]),
                DocumentGeneration.legacy_cleanup_pending.is_(True),
            )).distinct()))
            canceled = list(session.scalars(select(DocumentUpdateAttempt.doc_id).where(
                DocumentUpdateAttempt.cancel_requested.is_(True),
            )))
        with self._start_lock:
            running = {doc_id for doc_id, task in self._threads.items() if not task.done()}
            for doc_id in canceled:
                if doc_id not in running:
                    try:
                        self._finish_canceled_update(doc_id)
                    except Exception:
                        logger.warning("[%s] Возврат опубликованной версии будет повторён", doc_id, exc_info=True)
        for doc_id in set(doc_ids) | set(canceled):
            if doc_id not in running:
                self._cleanup_document_generations(doc_id)

    def _record_storage_full(self, doc_id: str, *, processed_chunks: int | None = None) -> None:
        """Ставит pause и сохраняет его после освобождения места в БД.

        Если том PostgreSQL/SQLite заполнен, обновить саму строку документа
        невозможно. В этот промежуток реестр накладывает оперативный статус на
        ответы API, а daemon повторяет компактный UPDATE до успеха.
        """
        fields: dict[str, object] = {
            "status": "paused",
            "error": str(StorageFullError()),
            "error_code": codes.STORAGE_FULL,
        }
        if processed_chunks is not None:
            fields["processed_chunks"] = processed_chunks
        try:
            self.registry.update(doc_id, **fields)
        except Exception:
            token = mark_transient_storage_failure(doc_id)
            logger.exception("Не удалось сохранить storage_full для документа %s", doc_id)
            self._schedule_storage_full_retry(doc_id, fields, token)
            return
        clear_transient_storage_failure(doc_id)

    def _schedule_storage_full_retry(self, doc_id: str, fields: dict[str, object], token: int) -> None:
        key = (doc_id, token)
        with self._storage_failure_docs_lock:
            if key in self._storage_failure_docs:
                return
            self._storage_failure_docs.add(key)

        def retry() -> None:
            try:
                while True:
                    with storage_failure_lock():
                        if not transient_storage_failure_is_active(doc_id, token):
                            return
                        try:
                            self.registry.update(doc_id, **fields)
                        except Exception as exc:
                            logger.warning(
                                "[%s] Не удалось зафиксировать storage_full в БД: %s", doc_id, exc
                            )
                        else:
                            clear_transient_storage_failure(doc_id)
                            return
                    time.sleep(5)
            finally:
                with self._storage_failure_docs_lock:
                    self._storage_failure_docs.discard(key)

        threading.Thread(target=retry, name=f"storage-status-{doc_id}", daemon=True).start()

    def _stop_task(self, doc_id: str) -> None:
        """Signal cancellation, but never authorize deletion while work is alive.

        Caller holds _start_lock through the subsequent delete/trash mutation.
        On timeout ownership and the abort signal remain for the worker; a later
        retry can delete once it has actually finished.
        """
        event = self._abort_events.get(doc_id)
        if event:
            event.set()
        task = self._threads.get(doc_id)
        if task and not task.done():
            if task.cancel():
                self._threads.pop(doc_id, None)
                self._abort_events.pop(doc_id, None)
                self._pipeline_slots.release()
                # A cancelled queued task has no worker to persist a pause.
                # Keep it resumable if the user restores it from the trash.
                self.registry.update(doc_id, status="paused", error=None, error_code=None)
                return
            try:
                task.result(timeout=2.0)
            except FutureTimeoutError:
                if not task.done():
                    raise ConflictError(
                        "Обработка останавливается. Повторите удаление после её завершения",
                        code=codes.PROCESSING_STOPPING,
                    ) from None
            except Exception:
                pass

    def _process(
        self, doc_id: str, filepath: str, filename: str, user_tags: list[str], resume: bool,
        *, fresh_table_cache: bool = False,
    ) -> None:
        context = current_context()
        if context.doc_id != doc_id or not context.operation_id:
            context = new_operation(context, doc_id=doc_id)
        with bind_context(context), operation_span():
            return self._process_bound(doc_id, filepath, filename, user_tags, resume,
                                       fresh_table_cache=fresh_table_cache)

    def _process_bound(
        self, doc_id: str, filepath: str, filename: str, user_tags: list[str], resume: bool,
        *, fresh_table_cache: bool = False,
    ) -> None:
        with session_scope() as session:
            capture_published_update(session, doc_id, self.settings)
        self.registry.update(doc_id, status="processing", error=None, error_code=None, problem=None)
        source_file_hash = _sha256_file(filepath)
        staging = StagingStore(doc_id)
        with session_scope() as session:
            generation = prepare_generation_attempt(session, doc_id, resume=resume)
            generation_id, phase = generation.id, generation.phase
            if phase == "preparing":
                paths = prepare_generation_paths(session, self.settings, doc_id, generation_id)
            else:
                paths = generation_paths(self.settings, doc_id, generation_id)
        set_generation_id(generation_id)
        set_table_cache_fresh_since(_table_cache_fresh_since(paths.uploads_root, fresh_table_cache, resume))
        if phase == "ready":
            checkpoint = staging.load() or {}
            validate_resume_parser_version(
                checkpoint, ParseContext(filename, mail_enabled=self.settings.mail_import_enabled).parser_version,
            )
            validate_resume_source_file_hash(checkpoint, source_file_hash)
            self._publish_prepared_generation(doc_id, generation_id, staging)
            return
        # Вложения (бинарники) пишутся парсером в uploads/<doc_id>/attachments/ —
        # рядом с оригиналом, а не в будущий бандл (Этап 2b: бандл — производная
        # проекция, вложения — байты-источники в FS, описанные в okf_attachments).
        doc_root = self.settings.uploads_dir / doc_id
        attachments_dir = paths.attachments
        # This scope owns a new private directory, including parser errors and
        # rejected checkpoints. A successful publish moves it out of this scope.
        with tempfile.TemporaryDirectory(prefix=".attachments-attempt-", dir=paths.uploads_root) as attempt:
            parse_attachments_dir = Path(attempt)
            parse_context = ParseContext(filename, mail_enabled=self.settings.mail_import_enabled)
            parse_started = start_stage("parse")
            if self.settings.parser_supervisor_enabled and parse_document.__module__.startswith("docparser"):
                supervised = parse_document_supervised(
                    filepath,
                    filename,
                    attachments_dir=parse_attachments_dir,
                    timeout_seconds=self.settings.parser_timeout_seconds,
                    max_memory_mb=self.settings.parser_max_memory_mb,
                    max_concurrent=self.settings.parser_max_concurrent,
                    mail_enabled=self.settings.mail_import_enabled,
                )
                blocks, parse_context.sources = supervised
                parse_warnings = list(supervised.warnings)
                parser_version = supervised.parser_version
            else:
                blocks = parse_document(
                    filepath,
                    filename,
                    attachments_dir=parse_attachments_dir,
                    context=parse_context,
                )
                parse_warnings = list(parse_context.warnings)
                parser_version = parse_context.parser_version
            finish_stage("parse", parse_started, counts={"processed": len(blocks)})
            markdown, attach_spans = markdown_attachment_spans(blocks)
            # Keep the existing canonical markdown contract for ordinary files and
            # test/legacy parser adapters. Rebuild when disabled mail or administrative
            # attachment markers must be removed from generation and search input.
            filtered_blocks = indexable_blocks(blocks)
            indexable_markdown = (
                markdown if len(filtered_blocks) == len(blocks)
                else blocks_to_markdown(filtered_blocks)
            )
            # Only skip generation when there is no text at all. The diagnostic
            # threshold of 200 chars is not safe here: short text can be meaningful.
            text_source_ids = {
                (block.meta or {}).get("source_id") or "root"
                for block in filtered_blocks
                if not (block.type == "heading" and (block.meta or {}).get("mail"))
                and _IMAGE_LINK_RE.sub("", blocks_to_markdown([block])).strip()
            }
            # Keep mail subjects in canonical text for display/search and stable
            # evidence offsets, but metadata alone must never trigger generation.
            has_text = bool(text_source_ids) if blocks else bool(_IMAGE_LINK_RE.sub("", indexable_markdown).strip())

            # Автоопределение номера разработки: только на «свежем» проходе и если
            # regex по имени файла (на этапе upload) ничего не нашёл. Non-fatal —
            # ошибка LLM/справочника не прерывает обработку документа.
            effects_path = paths.uploads_root / "parse-effects.json"
            parse_effects = json.loads(effects_path.read_text(encoding="utf-8")) if resume and effects_path.is_file() else {}
            if has_text and not resume and self.settings.dev_detection_enabled:
                doc = self.registry.get(doc_id)
                if doc and not doc.get("development_id"):
                    detection = detect(markdown, filename, doc_id)
                    if detection.confidence is not None:
                        parse_effects["development"] = {
                            "development_confidence": detection.confidence,
                            "development_suggestion": detection.suggestion,
                            **({"development_id": detection.development_id} if detection.development_id is not None else {}),
                        }
                        parse_effects["development_base"] = {
                            key: doc.get(key) for key in (
                                "development_id", "development_confirmed_by",
                                "development_confidence", "development_suggestion",
                            )
                        }

            # Дедупликация (Этап 4.2): content_hash + MinHash/LSH-бакеты. Non-fatal —
            # сбой сигнатуры не прерывает обработку, документ просто не участвует в
            # поиске дублей до следующего реиндекса.
            if self.settings.dedup_enabled:
                try:
                    from app.services.deduplication import prepare_document_signature
                    from app.services.mail_identity import mail_fingerprint_from_parse

                    parse_effects["signature"] = prepare_document_signature(
                        markdown,
                        mail_fingerprint_from_parse(parse_context.sources, blocks, parse_attachments_dir),
                    )
                except Exception:
                    logger.warning(
                        "[%s] Индексация сигнатуры дедупликации не удалась", doc_id, exc_info=True
                    )
            write_json_atomic(effects_path, parse_effects)

            source_chunks = chunk_blocks_by_source(blocks, self.okf_generator)
            # Preview/test adapters can supply canonical markdown independently of
            # parser blocks. Such legacy callers retain the old root-only contract.
            if not source_chunks and indexable_markdown:
                source_chunks = [
                    {"source_id": "root", "content": chunk}
                    for chunk in self.okf_generator.chunk_text(indexable_markdown)
                    if chunk
                ]
            # Source offsets and their SHA-256 are persisted against staging text.
            # Canonicalize here, before both LLM evidence resolution and file write:
            # otherwise MSG bodies with CRLF get a span hash for one string and a
            # DocumentChunk hash for a different LF-normalized string on Windows.
            chunks = [_normalize_newlines(item["content"]) for item in source_chunks]
            chunk_source_ids = [item["source_id"] for item in source_chunks]
            mail_source_ids = {
                node.source_id
                for node in parse_context.sources
                if node.kind == "mail" or bool((node.metadata or {}).get("mail"))
            }
            total = len(chunks)
            # Доля символов вложения в каждом чанке (программный тег «attachment»,
            # post-LLM; [] когда вложений нет — быстрый путь без накладных расходов).
            attachment_shares: list[float] = []
            if self.settings.okf_attachment_tag_enabled:
                attachment_shares = attachment_shares_by_source(blocks, self.okf_generator)
                if len(attachment_shares) != total:
                    # Legacy preview/test adapters supply markdown without blocks.
                    attachment_shares = self.okf_generator.attachment_shares(markdown, attach_spans)
            # Сброс residue телеметрии: события от dev-детекции и пр. не должны
            # приписываться первому чанку.
            gen_quality.drain()
            if resume and staging.exists():
                manifest = staging.load()
                validate_resume_parser_version(manifest, parser_version)
                validate_resume_source_file_hash(manifest, source_file_hash)
                # Never mix a reusable checkpoint with a new chunk layout. An
                # empty checkpoint has no chunk text or concepts to preserve and
                # can safely start from this fresh layout.
                has_reusable_checkpoint = bool(manifest.get("processed_chunks")) or any(
                    staging.dir.glob("chunk_*.md")
                )
                if has_reusable_checkpoint and (
                    total != manifest.get("total_chunks") or any(
                        not (staging.dir / f"chunk_{i:02d}.md").is_file()
                        or not _same_checkpoint_chunk(staging.dir / f"chunk_{i:02d}.md", chunk)
                        or (
                            (manifest.get("chunks_data", {}).get(str(i)) or {}).get("source_id", "root")
                            != chunk_source_ids[i]
                        )
                        for i, chunk in enumerate(chunks)
                    )
                ):
                    raise DomainError("Изменилась разбивка документа на чанки", code=codes.PARTIAL_REGENERATION_UNAVAILABLE)
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
                staging.create(
                    total, global_tags=user_tags, parser_version=parser_version,
                    source_file_hash=source_file_hash, generation_id=generation_id,
                )
            staging.bind_generation(generation_id)

            # A parser attempt remains private until it is known to be compatible
            # with an existing checkpoint. Publishing earlier could delete accepted
            # attachment bytes when resume is rejected. Rebase the parser's absolute
            # paths after the directory move so DB rows point at durable files.
            if parse_attachments_dir != attachments_dir:
                _publish_attachment_attempt(parse_attachments_dir, attachments_dir)
                _rebase_attachment_paths(blocks, parse_attachments_dir, attachments_dir)
        source_rows = _source_rows(parse_context.sources, blocks, doc_root, parser_version=parser_version)
        attachments = _collect_attachments(blocks, doc_root)
        self.registry.update(doc_id, status="splitting", total_chunks=total, processed_chunks=len(staging.processed_chunks))

        for i, chunk in enumerate(chunks):
            staging.save_chunk_text(i, chunk)
            staging.set_chunk_source(i, chunk_source_ids[i])

        max_chunk_retries = max(1, self.settings.llm_chunk_retry_attempts)
        chunk_backoff = self.settings.llm_chunk_retry_backoff_seconds
        # Провенанс генерации (Этап 2b): модель/промпт — константы прохода,
        # generated_at — момент успешной генерации конкретного чанка (ниже).
        run_model_id = self.settings.llm_model
        run_prompt_version = self.okf_generator.prompt_version()

        try:
            partial_chunks = set(staging.partial_chunks)
            for i, chunk in enumerate(chunks):
                if staging.has_chunk(i) and i not in partial_chunks:
                    continue
                if self._abort_events.get(doc_id, threading.Event()).is_set():
                    logger.info("Генерация %s прервана по запросу удаления", doc_id)
                    return

                concepts = None
                degradation: list[dict] = []
                for chunk_attempt in range(1, max_chunk_retries + 1):
                    try:
                        generation_started = start_stage("generate", chunk_index=i, retry_index=chunk_attempt - 1)
                        gen_quality.drain()
                        self.registry.update(doc_id, current_chunk=i + 1)
                        if not has_text or (blocks and chunk_source_ids[i] not in text_source_ids):
                            concepts = []
                        elif chunk_source_ids[i] in mail_source_ids:
                            concepts = self.okf_generator.generate_chunk(
                                chunk, filename, i + 1, total, doc_id=doc_id, source_is_mail=True,
                            )
                        else:
                            concepts = self.okf_generator.generate_chunk(chunk, filename, i + 1, total, doc_id=doc_id)
                        # Телеметрия деградации этого чанка (salvage JSON,
                        # fallback классификатора) — до любых других вызовов.
                        degradation = gen_quality.drain()
                        finish_stage("generate", generation_started, chunk_index=i,
                                     retry_index=chunk_attempt - 1, counts={"concepts": len(concepts or [])})
                        if self._abort_events.get(doc_id, threading.Event()).is_set():
                            logger.info("Генерация %s прервана после чанка %d", doc_id, i + 1)
                            return
                        self.registry.update(doc_id, error=None, error_code=None)
                        incomplete = gen_quality.has_salvage(degradation)
                        # Keep the previous partial checkpoint until a complete
                        # replacement is ready; never discard a usable result
                        # because a subsequent recovery attempt failed.
                        if not incomplete or not staging.has_chunk(i):
                            if user_tags and concepts:
                                for concept in concepts:
                                    concept.tags = _merge_tags(concept.tags, user_tags)
                            if attachment_shares and concepts and attachment_shares[i] >= self.settings.okf_attachment_tag_threshold:
                                for concept in concepts:
                                    concept.tags = _merge_tags(concept.tags, [ATTACHMENT_TAG])
                            provenance = {
                                "generated_at": datetime.now(timezone.utc).isoformat(),
                                "model_id": run_model_id,
                                "prompt_version": run_prompt_version,
                            }
                            staging.append_chunk(i, concepts, degradation=degradation, provenance=provenance, global_tags=user_tags)
                        if incomplete and chunk_attempt < max_chunk_retries:
                            emit_event("retry_scheduled", fields={"stage": "generate", "chunk_index": i,
                                                                  "retry_index": chunk_attempt, "counts": {"attempts": max_chunk_retries}})
                            logger.info("[%s] Догенерация чанка %d/%d: попытка %d/%d",
                                        doc_id, i + 1, total, chunk_attempt + 1, max_chunk_retries)
                            if self._abort_events.get(doc_id, threading.Event()).wait(timeout=chunk_backoff * chunk_attempt):
                                return
                            continue
                        break
                    except Exception as exc:
                        gen_quality.drain()
                        if (
                            isinstance(exc, LLMTruncationError)
                            or is_fatal_error(exc)
                            or chunk_attempt == max_chunk_retries
                        ):
                            self.registry.update(doc_id, error=str(exc), error_code=processing_error_code(exc))
                            raise
                        delay = chunk_backoff * chunk_attempt
                        emit_event("retry_scheduled", exception=exc,
                                   fields={"stage": "generate", "chunk_index": i, "retry_index": chunk_attempt,
                                           "error_code": processing_error_code(exc)})
                        msg = (
                            f"Сбой генерации чанка ({exc}). "
                            f"Повтор {chunk_attempt}/{max_chunk_retries} через {int(delay)}с..."
                        )
                        logger.warning("Чанк %d/%d: %s", i + 1, total, msg)
                        self.registry.update(doc_id, error=msg, error_code=codes.GENERATION_RETRYING)
                        if self._abort_events.get(doc_id, threading.Event()).wait(timeout=delay):
                            return

                self.registry.update(doc_id, processed_chunks=len(staging.processed_chunks), current_chunk=None)
        except Exception as exc:
            record_failure(exc, stage="generate")
            logger.warning("Генерация OKF прервана на документе %s: %s", doc_id, exc, exc_info=True)
            if is_storage_full(exc):
                self._record_storage_full(doc_id, processed_chunks=len(staging.processed_chunks))
            else:
                self.registry.update(
                    doc_id,
                    status="paused",
                    error=str(exc),
                    error_code=processing_error_code(exc),
                    processed_chunks=len(staging.processed_chunks),
                )
            return

        self.registry.update(doc_id, status="indexing")
        try:
            self._finalize(
                doc_id,
                filename,
                staging,
                attachments=attachments,
                global_tags=user_tags,
                source_rows=source_rows,
                parser_version=parser_version,
                parse_warnings=parse_warnings,
                has_text=has_text,
            )
        except DocumentUpdateCancelled:
            return
        except DependencyUnavailableError as exc:
            record_failure(exc, stage="index")
            # Staging не удаляем: чекпоинты всех чанков сохраняются, чтобы
            # повторный resume повторил только финализацию (embed+index),
            # не перегенерируя концепты через LLM.
            logger.warning("Финализация документа %s прервана (зависимость недоступна): %s", doc_id, exc.user_message, exc_info=True)
            self.registry.update(
                doc_id,
                status="paused",
                error=exc.user_message,
                error_code=processing_error_code(exc),
            )
            return
        except Exception as exc:
            # Staging не удаляем: чекпоинты всех чанков сохраняются, чтобы
            # повторный resume повторил только финализацию (embed+index),
            # не перегенерируя концепты через LLM.
            record_failure(exc, stage="index")
            logger.exception("Финализация документа %s не удалась", doc_id)
            if is_storage_full(exc):
                self._record_storage_full(doc_id)
            else:
                self.registry.update(doc_id, status="failed", error=str(exc), error_code=processing_error_code(exc))
            return

    def _finalize(
        self,
        doc_id: str,
        filename: str,
        staging: StagingStore,
        attachments: list[dict],
        global_tags: list[str],
        source_rows: list[dict] | None = None,
        parser_version: str | None = None,
        parse_warnings: list[dict] | None = None,
        has_text: bool | None = None,
    ) -> None:
        checkpoint = staging.load() or {}
        generation_id = checkpoint.get("generation_id")
        with session_scope() as session:
            if generation_id is None:
                generation_id = prepare_generation_attempt(session, doc_id, resume=True).id
            paths = prepare_generation_paths(session, self.settings, doc_id, generation_id)
        staging.bind_generation(generation_id)
        indexing_started = start_stage("index")
        # Денормализованная проекция разработки (номер/название/модуль) в payload
        # Qdrant `dev_tags` — отдельное поле, не смешивается с `tags`.
        dev_tags: list[str] = []
        doc = self.registry.get(doc_id)
        if doc is not None:
            global_tags = list(doc.get("tags") or [])
        if doc and doc.get("development_id"):
            dev_tags = get_development_registry().dev_tags(doc["development_id"])
        # Язык документа для payload Qdrant (Этап 7 фаза D): считаем ДО индексации,
        # чтобы source_locale попадал в точки прямо при upsert (без доп. set_payload).
        # Ручная правка (manual) не перезаписывается — эффективное значение берётся
        # из текущего поля документа.
        current_source_locale = doc.get("source_locale_source") if doc else None
        current_locale = doc.get("source_locale") if doc else None

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
        concept_source_ids = [
            (chunks_data.get(str(chunk_of_slug.get(slug))) or {}).get("source_id")
            for slug in slugs
        ]

        # Retained checkpoints can outlive edits to document tags. Reconcile
        # each checkpoint's original user tags, preserving generated tags and
        # all content/provenance of the chunks that did not need regeneration.
        for concept, slug in zip(concepts, slugs):
            info = chunks_data.get(str(chunk_of_slug.get(slug))) or {}
            original_tags = set(info.get("global_tags", manifest.get("global_tags", [])))
            removed = original_tags - set(global_tags)
            concept.tags = _merge_tags([tag for tag in concept.tags if tag not in removed], global_tags)

        chunks_meta: list[dict] = []
        chunk_rows: list[dict] = []
        chunk_files: list[tuple[Path, int, str]] = []
        for chunk_file in sorted(staging.dir.glob("chunk_*.md"), key=lambda path: int(path.stem.split("_")[-1])):
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
                    "source_id": info.get("source_id"),
                }
            )

        # Этап 2b / Фаза 5: бандл — экспорт, не рабочее состояние. По умолчанию
        # okf_write_bundles=false — okf_docs строятся в памяти (БД — canonical),
        # файлы .md не пишутся. При true (dual-write) — как раньше: tmp + atomic move.
        if self.settings.okf_write_bundles:
            target = paths.bundle
            tmp_dir = paths.bundle.parent / f".tmp-{generation_id}"
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_dir.mkdir(parents=True, exist_ok=True)

            attach_src = paths.attachments
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
                source_ids=concept_source_ids,
                sources=source_rows or [],
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
                source_ids=concept_source_ids,
            )

        # Провенанс генерации (Этап 2b): per-chunk generated_at/model_id/prompt
        # из staging chunks_data — в metadata концептов, откуда replace_concepts
        # пишет их в okf_concepts. Не путать с created_at (время SQL INSERT).
        chunk_rows_by_index = {row["chunk_index"]: row for row in chunk_rows}
        for doc in okf_docs:
            ci = doc.metadata.get("chunk_index")
            prov = provenance_of_chunk.get(ci) if ci is not None else None
            if ci in chunk_rows_by_index:
                doc.metadata["source_id"] = chunk_rows_by_index[ci].get("source_id")
            if prov:
                doc.metadata["generated_at"] = prov.get("generated_at")
                doc.metadata["model_id"] = prov.get("model_id")
                doc.metadata["prompt_version"] = prov.get("prompt_version")

        # Problem-коды (инцидент 03.09.2026: done ≠ «документ полон»).
        # Приоритет: no_text_layer/no_concepts (0 концептов) >
        # llm_partial_result (salvage) > llm_classifier_fallback >
        # index_partial_failure
        # (чанк-индексация пропущена) — первична причина, из-за которой
        # документ может быть неполон или неищем.
        problem: str | None = None
        if not okf_docs:
            lacks_text = not has_text if has_text is not None else (
                sum(len(_IMAGE_LINK_RE.sub("", row["content"]).strip()) for row in chunk_rows) < MIN_TEXT_LAYER_CHARS
            )
            problem = (
                problem_codes.NO_TEXT_LAYER
                if lacks_text
                else problem_codes.NO_CONCEPTS
            )
            logger.warning(
                "[%s] Документ %s не содержит концептов (problem=%s); чанки индексируются",
                doc_id, filename, problem,
            )
        else:
            problem = _generation_problem(chunks_data)
            if problem:
                degraded = [
                    idx for idx, info in chunks_data.items() if (info or {}).get("degradation")
                ]
                logger.warning(
                    "[%s] Документ %s: чанки с деградацией генерации %s (problem=%s)",
                    doc_id, filename, degraded, problem,
                )

        self.vector_store.ensure_collection()
        # Язык документа: детекция идёт по чанкам (тот же вход, что раньше на
        # финализации), но теперь ДО индексации — payload точек сразу корректен.
        detected_locale = detect_language(
            "".join(r["content"] for r in chunk_rows)[:100_000]
        )
        locale_fields = _source_locale_fields(detected_locale, current_source_locale)
        effective_locale = locale_fields.get("source_locale", current_locale)

        from app.services.mail_scope import build_record_mail_scopes

        concept_scopes, chunk_scopes = build_record_mail_scopes(
            source_rows or [], [doc.metadata for doc in okf_docs], chunk_rows,
        )
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
            concept_point_ids = self.vector_store.index_concepts(
                doc_id, okf_docs, vectors, dev_tags=dev_tags, source_locale=effective_locale,
                generation_id=generation_id, mail_scopes=concept_scopes,
            )
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
                    source_locale=effective_locale,
                    source_ids=[row.get("source_id") for row in chunk_rows],
                    generation_id=generation_id, mail_scopes=chunk_scopes,
                )
                keep_point_ids |= chunk_point_ids
                logger.info("[%s] Проиндексировано %d чанков", doc_id, len(chunk_texts))
            elif chunks_meta:
                problem = problem or problem_codes.INDEX_PARTIAL_FAILURE

        # Очистка осиротевших старых точек (после успешного upsert новых).
        # Удаляются только точки doc_id, чьи point_id не вошли в новый набор.
        # Для документа без концептов и чанков удаляет ВСЕ старые точки —
        # регенерация в пустоту не оставляет устаревших векторов в поиске.
        self.vector_store.delete_orphaned_points(doc_id, keep_point_ids, generation_id=generation_id)

        total_chunks = manifest.get("total_chunks", 0) if manifest else 0
        problem = summarize_problems(parse_warnings, problem, None, None)
        document_fields = dict(
            status="done",
            okf_concept_count=len(okf_docs),
            error=None,
            error_code=None,
            problem=problem,
            parser_version=parser_version,
            parse_warnings=parse_warnings or [],
            **locale_fields,
        )
        prepared = {
            "doc_id": doc_id, "generation_id": generation_id,
            "concepts": [item.model_dump(mode="json") for item in okf_docs],
            "chunks": chunk_rows, "sources": source_rows, "attachments": attachments,
            "document_fields": document_fields,
            "index_metadata": {"global_tags": global_tags, "source_locale": effective_locale, "dev_tags": dev_tags},
            "parse_effects": json.loads((paths.uploads_root / "parse-effects.json").read_text(encoding="utf-8"))
            if (paths.uploads_root / "parse-effects.json").is_file() else {},
            "artifacts": artifact_manifest(self.settings, doc_id, generation_id),
            "point_ids": sorted(keep_point_ids),
        }
        publication_path = paths.uploads_root / "publication.json"
        write_json_atomic(publication_path, prepared)
        with session_scope() as session:
            mark_generation_ready(session, doc_id, generation_id, publication_hash=file_digest(publication_path))
        finish_stage("index", indexing_started, counts={"concepts": len(okf_docs), "chunks": len(chunk_rows), "points": len(keep_point_ids)})
        self._publish_prepared_generation(doc_id, generation_id, staging)
        logger.info(
            "Документ %s обработан: %d OKF-концептов, %d чанков%s",
            filename, len(okf_docs), total_chunks,
            f" (problem={problem})" if problem else "",
        )

    def _publish_prepared_generation(self, doc_id: str, generation_id: str, staging: StagingStore) -> None:
        publication_started = start_stage("publish")
        if not publish_prepared_document(self.settings, self.vector_store, doc_id, generation_id):
            return
        finish_stage("publish", publication_started)
        # Keep incomplete generation checkpoints for selective recovery, even
        # after successful indexing. Clean runs release them after the DB update.
        try:
            if not staging.partial_chunks:
                staging.remove()
        except Exception:
            logger.warning("[%s] Опубликовано; очистка staging будет повторена позже", doc_id, exc_info=True)

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
        with self._start_lock:
            self._stop_task(doc_id)
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
        with self._start_lock:
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
        with self._start_lock:
            self._stop_task(doc_id)
            self.registry.delete(doc_id)
            self._physical_cleanup(doc_id)

    def remove_if_deleted(self, doc_id: str) -> bool:
        """Физическое удаление с precondition «документ в корзине» (для purge).

        Сначала атомарно удаляет строку БД (registry.delete_if_deleted): если
        документ успели восстановить после purge_expired() — возвращает False,
        не трогая точки/файлы. Удаление строки БД первым — осознанное: поиск
        отсекает хиты с doc_id, отсутствующим в БД (services/search_filter.py),
        поэтому между удалением строки и физической чисткой утекать нечему.
        """
        with self._start_lock:
            self._stop_task(doc_id)
            if not self.registry.delete_if_deleted(doc_id):
                return False
            self._physical_cleanup(doc_id)
            return True

    @contextmanager
    def _chunk_lock(self, doc_id: str, *, blocking: bool = True):
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
            if not lock.acquire(blocking=blocking):
                raise ChunkBackfillBusyError("Текст документа уже строится")
            try:
                yield
            finally:
                lock.release()
        finally:
            with self._chunk_locks_guard:
                entry[1] -= 1
                if entry[1] <= 0 and self._chunk_locks.get(doc_id) is entry:
                    del self._chunk_locks[doc_id]

    def ensure_chunks(self, doc_id: str, *, interactive: bool = False) -> list[dict]:
        """Возвращает мету чанков документа, при необходимости строя их из исходника.

        Источники по приоритету (Этап 2b — PostgreSQL SSOT):
          1. document_chunks (БД) — канонический источник текста чанков;
          2. staging (документ в процессе генерации) — живые чанки;
          3. ленивый backfill: пере-парсинг исходника (без LLM) в document_chunks.

        interactive=True — вызов из HTTP-чтения (любой viewer): см.
        _interactive_backfill. Скрипты backfill зовут без флага и ждут разбора.
        """
        meta = _chunks_meta_from_db(doc_id)
        if meta:
            return meta

        with session_scope() as session:
            state = session.get(DocumentGenerationState, doc_id)
            if state and state.active_generation_id:
                # Zero canonical chunks is also a published result. A read
                # must not replace it by parsing the upload outside publication.
                return []
        staging = StagingStore(doc_id)
        if staging.exists():
            return _chunks_meta_from_dir(staging.dir, staging.load())

        if interactive:
            return self._interactive_backfill(doc_id)
        with self._chunk_lock(doc_id):
            meta = _chunks_meta_from_db(doc_id)
            if meta:
                return meta
            self._backfill_chunks(doc_id)
            return _chunks_meta_from_db(doc_id)

    def _interactive_backfill(self, doc_id: str) -> list[dict]:
        """Ленивый backfill, который GET-запрос не может превратить в нагрузку.

        - документ в обработке — чанки построит пайплайн, разбор не запускается;
        - неудачный или пустой разбор запоминается на
          _INTERACTIVE_BACKFILL_RETRY_SECONDS и не повторяется на каждом чтении;
        - одновременно идёт не больше одного такого разбора на процесс, и никто
          его не ждёт: занято — ChunkBackfillBusyError (429 с Retry-After),
          а не поток пула запросов, заблокированный на время разбора.
        """
        doc = self.registry.get(doc_id)
        if doc is not None and doc.get("status") in STALE_STATUSES:
            return []
        remembered = self._remembered_backfill_outcome(doc_id)
        if remembered is not None:
            code, message = remembered
            if code is None:
                return []
            raise DomainError(message, code=code)
        if not self._interactive_backfill_slots.acquire(blocking=False):
            raise ChunkBackfillBusyError("Идёт построение текста другого документа")
        try:
            with self._chunk_lock(doc_id, blocking=False):
                meta = _chunks_meta_from_db(doc_id)
                if meta:
                    return meta
                try:
                    self._backfill_chunks(doc_id)
                except ParserBusyError as exc:
                    raise ChunkBackfillBusyError("Все процессы разбора документов заняты") from exc
                except ParserIsolationError as exc:
                    # Проблема окружения, а не документа: не запоминаем.
                    raise DomainError(
                        "Защищённый разбор документов недоступен",
                        code=codes.PARSER_ISOLATION_UNAVAILABLE,
                    ) from exc
                except (ParserTimeoutError, ParserMemoryLimitError, ParserWorkerError) as exc:
                    code = (
                        codes.PARSER_TIMEOUT if isinstance(exc, ParserTimeoutError)
                        else codes.PARSER_RESOURCE_LIMIT if isinstance(exc, ParserMemoryLimitError)
                        else codes.TEXT_NOT_FOUND
                    )
                    message = "Не удалось извлечь текст документа"
                    self._remember_backfill_outcome(doc_id, code, message)
                    raise DomainError(message, code=code) from exc
                meta = _chunks_meta_from_db(doc_id)
                if not meta:
                    self._remember_backfill_outcome(doc_id, None, "")
                return meta
        finally:
            self._interactive_backfill_slots.release()

    def _remembered_backfill_outcome(self, doc_id: str) -> tuple[str | None, str] | None:
        with self._chunk_locks_guard:
            outcome = self._interactive_backfill_outcomes.get(doc_id)
            if outcome is None:
                return None
            expires, code, message = outcome
            if expires <= time.monotonic():
                del self._interactive_backfill_outcomes[doc_id]
                return None
            return code, message

    def _remember_backfill_outcome(self, doc_id: str, code: str | None, message: str) -> None:
        now = time.monotonic()
        with self._chunk_locks_guard:
            outcomes = self._interactive_backfill_outcomes
            for key in [key for key, value in outcomes.items() if value[0] <= now]:
                del outcomes[key]
            outcomes[doc_id] = (now + _INTERACTIVE_BACKFILL_RETRY_SECONDS, code, message)

    def _backfill_chunks(self, doc_id: str) -> None:
        """Строит чанки из исходного файла и пишет их в document_chunks (без LLM).

        Этап 2b: чанки — канонически в БД; FS-бандл больше не кэш для этого пути.
        """
        with session_scope() as session:
            if not _can_backfill_legacy_chunks(session, doc_id):
                return
        doc = self.registry.get(doc_id)
        if not doc:
            raise NotFoundError("Документ не найден", code=codes.DOCUMENT_NOT_FOUND)
        filename = doc["filename"]
        ext = Path(filename).suffix.lower()
        filepath = self.settings.uploads_dir / f"{doc_id}{ext}"
        if not filepath.is_file():
            raise NotFoundError("Исходный файл документа не найден", code=codes.FILE_NOT_FOUND)
        self.settings.staging_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="chunk-backfill-", dir=self.settings.staging_dir) as temporary:
            attempt = Path(temporary)
            parse_context = ParseContext(filename, mail_enabled=self.settings.mail_import_enabled)
            if self.settings.parser_supervisor_enabled and parse_document.__module__.startswith("docparser"):
                supervised = parse_document_supervised(
                    filepath, filename, attachments_dir=attempt,
                    timeout_seconds=self.settings.parser_timeout_seconds,
                    max_memory_mb=self.settings.parser_max_memory_mb,
                    max_concurrent=self.settings.parser_max_concurrent,
                    mail_enabled=self.settings.mail_import_enabled,
                )
                blocks, parse_context.sources = supervised
                parse_context.parser_version = supervised.parser_version
            else:
                blocks = parse_document(filepath, filename, attachments_dir=attempt, context=parse_context)
            source_chunks = chunk_blocks_by_source(blocks, self.okf_generator)
            if not source_chunks:
                # Preserve the root-only contract of legacy parser adapters.
                source_chunks = [
                    {"source_id": "root", "content": chunk}
                    for chunk in self.okf_generator.chunk_text(blocks_to_markdown(indexable_blocks(blocks)))
                    if chunk
                ]
            rows = [
                {
                    "chunk_index": i,
                    "section_title": _extract_section_title(chunk["content"]),
                    "content": chunk["content"],
                    "content_hash": hashlib.sha256(chunk["content"].encode("utf-8")).hexdigest(),
                    "char_count": len(chunk["content"]),
                    "source_id": chunk["source_id"],
                }
                for i, chunk in enumerate(source_chunks)
            ]
            with session_scope() as session:
                if not lock_document_write(session, doc_id, allow_deleted=False):
                    return
                if not _can_backfill_legacy_chunks(session, doc_id):
                    return
                destination = merge_legacy_backfill_files(self.settings, doc_id, attempt)
                _rebase_attachment_paths(blocks, attempt, destination)
                replace_sources(
                    session, doc_id,
                    _source_rows(parse_context.sources, blocks, self.settings.uploads_dir / doc_id,
                                 parser_version=parse_context.parser_version),
                )
                replace_chunks(session, doc_id, rows)
        logger.info("Backfill чанков %s: %d", doc_id, len(rows))


def _can_backfill_legacy_chunks(session, doc_id: str) -> bool:
    state = session.get(DocumentGenerationState, doc_id)
    if state and (state.active_generation_id or state.candidate_generation_id):
        return False
    return session.query(DocumentChunk.doc_id).filter_by(doc_id=doc_id).first() is None


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
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
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
    except Exception as exc:
        dest.unlink(missing_ok=True)
        if is_storage_full(exc):
            raise StorageFullError() from exc
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



def _publish_attachment_attempt(attempt_dir: Path, destination_dir: Path) -> None:
    """Replace document attachments only after the supervised parse succeeded."""
    backup_dir = destination_dir.parent / f".attachments-previous-{uuid.uuid4().hex}"
    moved_previous = False
    try:
        if destination_dir.exists():
            destination_dir.replace(backup_dir)
            moved_previous = True
        attempt_dir.replace(destination_dir)
    except Exception:
        if moved_previous and backup_dir.exists() and not destination_dir.exists():
            backup_dir.replace(destination_dir)
        raise
    finally:
        if destination_dir.exists() and backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)


def _rebase_attachment_paths(blocks, attempt_dir: Path, destination_dir: Path) -> None:
    """Point parser block metadata to files after an attempt directory move."""
    old_root = Path(attempt_dir).resolve()
    new_root = Path(destination_dir).resolve()
    for block in blocks:
        meta = getattr(block, "meta", None)
        if not isinstance(meta, dict) or not meta.get("saved_path"):
            continue
        try:
            relative = Path(meta["saved_path"]).resolve().relative_to(old_root)
        except (OSError, TypeError, ValueError):
            continue
        meta["saved_path"] = str(new_root / relative)


def _same_checkpoint_chunk(path: Path, current: str) -> bool:
    """Compare Markdown logically, independent of platform newline translation."""
    try:
        saved = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return _normalize_newlines(saved) == _normalize_newlines(current)


def _normalize_newlines(value: str) -> str:
    # Earlier Windows checkpoints were written with the default text-mode
    # translation, which expands a source CRLF into CRCRLF. Treat it as the
    # original single logical line break so those checkpoints remain resumable.
    return value.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")


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
        status = meta.get("extraction_status") or ("parsed" if parsed else "unsupported")
        attachments.append(
            {
                "name": meta.get("name", ""),
                "kind": meta.get("kind", "other"),
                "caption": meta.get("caption", ""),
                "saved_path": relative,
                "is_processable": parsed,
                "extraction_status": status,
                "source_id": meta.get("source_id"),
            }
        )
    return attachments


def _source_rows(sources, blocks, base_dir: Path, parser_version: str = PARSER_VERSION) -> list[dict]:
    """Обогащает parser source tree статусом и относительным путём артефакта."""
    base = Path(base_dir).resolve()
    saved_by_source: dict[str, str] = {}
    status_by_source: dict[str, str] = {}
    for block in blocks:
        # Extracted page/inline images belong to the source's content. Only
        # its attachment marker identifies the original downloadable file.
        if getattr(block, "type", None) != "attachment":
            continue
        meta = getattr(block, "meta", {}) or {}
        source_id = meta.get("source_id")
        if not source_id:
            continue
        if meta.get("extraction_status"):
            status_by_source[source_id] = meta["extraction_status"]
        saved = meta.get("saved_path")
        if saved:
            try:
                saved_by_source[source_id] = Path(saved).resolve().relative_to(base).as_posix()
            except ValueError:
                saved_by_source[source_id] = f"attachments/{portable_name(saved)}"

    rows: list[dict] = []
    for node in sources:
        saved_path = saved_by_source.get(node.source_id)
        is_root = node.source_id == "root"
        artifact_kind = "original" if is_root or saved_path else "container_only"
        rows.append(
            {
                "source_id": node.source_id,
                "parent_source_id": node.parent_source_id,
                "ordinal": node.ordinal,
                "kind": node.kind,
                "display_name": node.display_name,
                "metadata": node.metadata,
                "saved_path": saved_path,
                "extraction_status": source_extraction_status(
                    node.warnings,
                    status_by_source.get(node.source_id, "parsed" if is_root else "unsupported"),
                ),
                "artifact_kind": artifact_kind,
                "container_source_id": node.parent_source_id if artifact_kind == "container_only" else None,
                "parser_version": parser_version,
                "warnings": node.warnings,
            }
        )
    return rows


_INSTANCE: Pipeline | None = None
_INSTANCE_LOCK = threading.Lock()


def get_pipeline() -> Pipeline:
    """Общий для процесса пайплайн — один инстанс на всех потребителей.

    Состояние обработки (_threads, executor, _abort_events, _start_lock, _chunk_locks) —
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
        if _INSTANCE is not None:
            _INSTANCE._executor.shutdown(wait=False, cancel_futures=True)
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
