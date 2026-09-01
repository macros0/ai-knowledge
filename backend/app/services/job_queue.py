"""Очередь массовых (системных) операций администратора.

Массовые удаление/перегенерация (bulk) выполняются фоновым worker-потоком, а не
синхронно в запросе — обычные запросы (чат) не блокируются массовой перегенерацией.

Ключевые правила (Этап 2а roadmap):
  - four-eyes: операция с числом документов >= approval_threshold_docs_<type>
    переходит в awaiting_approval и требует одобрения вторым администратором;
  - circuit breaker: если число ожидающих задач >= job_queue_max_pending, новые
    массовые операции отклоняются до освобождения очереди;
  - аудит массовой операции пишется ПО ДОКУМЕНТУ (по записи на каждый doc_id),
    а не одной записью на job — чтобы точечно отследить изменение документа.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Job
from app.db.session import session_scope
from app.services import audit as audit_mod

logger = logging.getLogger(__name__)

BULK_DELETE = "bulk_delete"
BULK_REGENERATE = "bulk_regenerate"
JOB_TYPES = frozenset({BULK_DELETE, BULK_REGENERATE})

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_AWAITING_APPROVAL = "awaiting_approval"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

# Статусы, занимающие место в очереди (для circuit breaker).
PENDING_STATUSES = frozenset({STATUS_QUEUED, STATUS_RUNNING, STATUS_AWAITING_APPROVAL})

# Терминальные статусы документа, означающие завершение обработки в пайплайне.
_DOC_TERMINAL = frozenset({"done", "paused", "failed", "error"})


class QueueOverloadedError(Exception):
    """Очередь массовых операций перегружена (circuit breaker)."""


class JobNotFoundError(Exception):
    pass


class SelfApprovalError(Exception):
    """Создатель задачи пытается одобрить её сам (нарушение four-eyes)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "created_by_id": job.created_by_id,
        "created_by": job.created_by,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "approved_by": job.approved_by,
        "approved_at": job.approved_at,
        "params": job.params,
        "result": job.result,
        "error": job.error,
    }


class JobQueue:
    def __init__(self, *, start_worker: bool = True):
        self._queue: queue.Queue[int] = queue.Queue()
        self._worker: threading.Thread | None = None
        if start_worker:
            self._start_worker()

    def _start_worker(self) -> None:
        self._worker = threading.Thread(
            target=self._run, name="job-queue-worker", daemon=True
        )
        self._worker.start()

    # --- Публичный API ---

    def submit(
        self,
        job_type: str,
        doc_ids: list[str],
        user,
        *,
        ip_address: str | None = None,
    ) -> dict:
        """Создаёт задачу и ставит её в очередь (или в awaiting_approval).

        user — доменная модель User (user_id/username). doc_ids — список ID
        документов. Бросает QueueOverloadedError при перегрузке очереди.
        """
        if job_type not in JOB_TYPES:
            raise ValueError(f"Неизвестный job_type: {job_type}")
        if not doc_ids:
            raise ValueError("Список документов пуст")

        settings = get_settings()
        # Circuit breaker: проверяем ДО создания записи в jobs.
        if self.pending_count() >= settings.job_queue_max_pending:
            raise QueueOverloadedError(
                "Очередь перегружена, новые массовые операции временно заблокированы"
            )

        threshold = (
            settings.approval_threshold_docs_delete
            if job_type == BULK_DELETE
            else settings.approval_threshold_docs_regenerate
        )
        needs_approval = len(doc_ids) >= threshold
        status = STATUS_AWAITING_APPROVAL if needs_approval else STATUS_QUEUED

        with session_scope() as s:
            job = Job(
                job_type=job_type,
                status=status,
                created_by_id=getattr(user, "user_id", None),
                created_by=getattr(user, "username", None),
                params={
                    "doc_ids": doc_ids,
                    "ip_address": ip_address,
                },
            )
            s.add(job)
            s.flush()
            job_id = job.id

        if not needs_approval:
            self._queue.put(job_id)
        return self._get_or_raise(job_id)

    def get(self, job_id: int) -> dict | None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            return _to_dict(job) if job else None

    def _get_or_raise(self, job_id: int) -> dict:
        job = self.get(job_id)
        if job is None:
            raise JobNotFoundError(f"Задача {job_id} не найдена")
        return job
        with session_scope() as s:
            job = s.get(Job, job_id)
            return _to_dict(job) if job else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[dict]:
        with session_scope() as s:
            jobs = (
                s.execute(select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset))
                .scalars()
                .all()
            )
            return [_to_dict(j) for j in jobs]

    def pending_count(self) -> int:
        with session_scope() as s:
            return (
                s.query(Job).filter(Job.status.in_(PENDING_STATUSES)).count()
            )

    def approve(self, job_id: int, approver, *, ip_address: str | None = None) -> dict:
        """Одобрение (four-eyes) задачи в awaiting_approval вторым администратором."""
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise JobNotFoundError(f"Задача {job_id} не найдена")
            if job.status != STATUS_AWAITING_APPROVAL:
                raise ValueError("Задача не ожидает одобрения")
            approver_id = getattr(approver, "user_id", None)
            if job.created_by_id is not None and job.created_by_id == approver_id:
                raise SelfApprovalError(
                    "Four-eyes: создатель задачи не может одобрить её сам"
                )
            job.status = STATUS_QUEUED
            job.approved_by = getattr(approver, "username", None) or getattr(approver, "user_id", None)
            job.approved_at = _utcnow()

        audit_mod.record(
            approver,
            audit_mod.JOB_APPROVE,
            audit_mod.TARGET_JOB,
            target_id=str(job_id),
            ip_address=ip_address,
        )
        self._queue.put(job_id)
        return self._get_or_raise(job_id)

    def cancel(self, job_id: int, canceller, *, ip_address: str | None = None) -> dict:
        """Отмена задачи в queued/awaiting_approval (running — не отменяется)."""
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise JobNotFoundError(f"Задача {job_id} не найдена")
            if job.status not in (STATUS_QUEUED, STATUS_AWAITING_APPROVAL):
                raise ValueError("Отменить можно только задачу, ожидающую выполнения")
            job.status = STATUS_CANCELLED
            job.finished_at = _utcnow()

        audit_mod.record(
            canceller,
            audit_mod.JOB_CANCEL,
            audit_mod.TARGET_JOB,
            target_id=str(job_id),
            ip_address=ip_address,
        )
        return self._get_or_raise(job_id)

    # --- Worker ---

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self._execute(job_id)
            except Exception as exc:
                logger.exception("Сбой выполнения массовой задачи %s", job_id)
                self._finish(job_id, [], [], error=str(exc))
            finally:
                self._queue.task_done()

    def _execute(self, job_id: int) -> None:
        job = self.get(job_id)
        if job is None or job["status"] != STATUS_QUEUED:
            return
        self._set_status(job_id, STATUS_RUNNING, started_at=_utcnow())

        params = job.get("params") or {}
        doc_ids = params.get("doc_ids") or []
        results: list[dict] = []
        errors: list[dict] = []

        from app.services.pipeline import Pipeline

        pipeline = Pipeline()
        for doc_id in doc_ids:
            try:
                if job["job_type"] == BULK_DELETE:
                    if pipeline.registry.get(doc_id) is None:
                        raise ValueError("Документ не найден")
                    pipeline.soft_delete(doc_id, deleted_by=job.get("created_by"))
                    audit_mod.record(
                        _JobUser(job),
                        audit_mod.DOCUMENT_BULK_DELETE,
                        audit_mod.TARGET_DOCUMENT,
                        target_id=doc_id,
                        ip_address=params.get("ip_address"),
                    )
                else:
                    self._regenerate_one(pipeline, doc_id)
                    audit_mod.record(
                        _JobUser(job),
                        audit_mod.DOCUMENT_BULK_REGENERATE,
                        audit_mod.TARGET_DOCUMENT,
                        target_id=doc_id,
                        ip_address=params.get("ip_address"),
                    )
                results.append({"doc_id": doc_id, "ok": True})
            except Exception as exc:
                logger.warning("Массовая операция: документ %s пропущен: %s", doc_id, exc)
                errors.append({"doc_id": doc_id, "error": str(exc)})

        self._finish(job_id, results, errors)

    def _regenerate_one(self, pipeline, doc_id: str) -> None:
        """Перегенерация одного документа с ожиданием завершения его пайплайна."""
        settings = get_settings()
        try:
            pipeline.regenerate(doc_id)
        except ValueError as exc:
            raise
        deadline = time.time() + settings.job_doc_timeout_seconds
        while time.time() < deadline:
            doc = pipeline.registry.get(doc_id)
            if doc is None:
                raise ValueError("Документ не найден")
            if doc.get("status") in _DOC_TERMINAL:
                if doc.get("status") in ("failed", "error"):
                    raise ValueError(doc.get("error") or "Перегенерация завершилась ошибкой")
                return
            time.sleep(1.0)
        raise ValueError("Превышено время ожидания перегенерации документа")

    def _set_status(self, job_id: int, status: str, **fields) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            job.status = status
            for key, value in fields.items():
                setattr(job, key, value)

    def _finish(
        self,
        job_id: int,
        results: list[dict],
        errors: list[dict],
        *,
        error: str | None = None,
    ) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            total = len((job.params or {}).get("doc_ids") or [])
            failed = bool(error) or (total > 0 and len(errors) == total)
            job.status = STATUS_FAILED if failed else STATUS_COMPLETED
            job.finished_at = _utcnow()
            job.result = {
                "processed": len(results),
                "errors": errors,
                "error": error,
            }


class _JobUser:
    """Прокси-«пользователь» для аудита из worker-потока (creator задачи)."""

    def __init__(self, job: dict):
        self.user_id = job.get("created_by_id") or "anonymous"
        self.username = job.get("created_by") or "anonymous"


_INSTANCE: JobQueue | None = None


def get_job_queue() -> JobQueue:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = JobQueue()
    return _INSTANCE
