"""Admission control for asynchronous raw-document export jobs.

This module deliberately owns only export jobs.  Generic ``JobQueue`` must not
recover, approve, cancel, or execute them: an export has its own filesystem
leases and retention rules implemented by the dedicated worker.
"""
from __future__ import annotations

from collections.abc import Callable
import queue
import re
import shutil
import threading
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from app import error_codes as codes
from app.config import get_settings
from app.db.models import Job
from app.db.session import session_scope
from app.services import audit as audit_mod
from app.services.bulk_export import (
    BuiltExportPart,
    ExportDocument,
    ExportPart,
    ExportPartTooLargeError,
    ExportSourceChangedError,
    build_export_parts,
    partition_documents,
    safe_archive_filename,
)
from app.services.errors import DomainError
from app.services.registry import DocumentRegistry
from app.services.storage import is_storage_full

BULK_EXPORT = "bulk_export"
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_READY = "ready"
PENDING_STATUSES = frozenset({STATUS_QUEUED, STATUS_RUNNING})
_DOC_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_ARTIFACT_DIR_RE = re.compile(r"^(\d+)\.(building|ready|deleting)$")
_MANIFEST_HEADROOM_BYTES = 1024 * 1024
_ADMISSION_LOCK = threading.RLock()
_export_queue: ExportQueue | None = None
_EXPORT_QUEUE_LOCK = threading.Lock()
logger = logging.getLogger(__name__)
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


class ExportAuditUnavailableError(DomainError):
    """The requested audit record could not be written atomically with Job."""


class ExportAdmissionError(DomainError):
    """A user-correctable export admission failure with an optional retry hint."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retry_after_seconds: int | None = None,
    ):
        super().__init__(message, code=code)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class ExportPlan:
    """Immutable source snapshot captured before a Job is created."""

    documents: tuple[ExportDocument, ...]
    parts: tuple[ExportPart, ...]
    total_bytes: int
    estimated_output_bytes: int

    def to_job_params(self, ip_address: str | None) -> dict:
        part_by_doc_id = {
            document.doc_id: part.number
            for part in self.parts
            for document in part.documents
        }
        return {
            "documents": [
                {
                    "doc_id": document.doc_id,
                    "filename": safe_archive_filename(document.filename),
                    "source_basename": document.path.name,
                    "size_bytes": document.size_bytes,
                    "mtime_ns": document.mtime_ns,
                    "part_number": part_by_doc_id[document.doc_id],
                }
                for document in self.documents
            ],
            "parts": [
                {
                    "number": part.number,
                    "document_ids": [document.doc_id for document in part.documents],
                    "source_bytes": part.source_bytes,
                }
                for part in self.parts
            ],
            "total_source_bytes": self.total_bytes,
            "estimated_output_bytes": self.estimated_output_bytes,
            "request_ip": ip_address,
        }

    def audit_value(self, settings) -> dict:
        return {
            "document_ids": [document.doc_id for document in self.documents],
            "document_count": len(self.documents),
            "total_source_bytes": self.total_bytes,
            "estimated_output_bytes": self.estimated_output_bytes,
            "part_count": len(self.parts),
            "limits": {
                "max_docs": settings.bulk_export_max_docs,
                "max_total_bytes": settings.bulk_export_max_total_mb * 1024 * 1024,
                "part_size_bytes": settings.bulk_export_part_size_mb * 1024 * 1024,
                "retained_limit_bytes": settings.bulk_export_max_retained_mb * 1024 * 1024,
                "min_free_bytes": settings.bulk_export_min_free_mb * 1024 * 1024,
            },
        }


@dataclass(frozen=True)
class ExportDownloadLease:
    path: Path
    filename: str
    size_bytes: int
    release: Callable[[], None]


@dataclass(frozen=True)
class ExportCleanupResult:
    expired: int
    skipped_leased: int
    delete_retries: int
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ExportRollbackPlan:
    """Read-only description of export state selected for an operator rollback."""

    job_ids: tuple[int, ...]
    artifact_names: tuple[str, ...]
    artifact_bytes: int
    active_lease_job_ids: tuple[int, ...]
    include_ready: bool


@dataclass(frozen=True)
class ExportRollbackResult:
    failed_jobs: int
    deleted_ready: int
    deleted_bytes: int
    skipped_leases: tuple[int, ...]
    audit_events: int
    errors: tuple[str, ...]


class ExportQueue:
    """Validate and persist export requests before the dedicated worker sees them."""

    def __init__(self, *, start_worker: bool = True):
        # Task 4 turns this into an actual worker.  Keeping the queue here makes
        # post-commit scheduling explicit and testable without running threads.
        self._queue: queue.Queue[int | object] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = object()
        self._accepting = True
        self._leases: dict[tuple[int, int], int] = {}
        if start_worker:
            self._start_worker()

    def _start_worker(self) -> None:
        self._worker = threading.Thread(
            target=self._run, name="bulk-export-worker", daemon=True
        )
        self._worker.start()

    def submit(self, doc_ids: list[str], user, *, ip_address: str | None = None) -> dict:
        """Persist an immutable source snapshot and enqueue it after commit."""
        settings = get_settings()
        if not settings.bulk_export_enabled:
            raise ExportAdmissionError(
                "Массовый экспорт отключён", code=codes.BULK_EXPORT_DISABLED
            )

        unique_ids = _ordered_unique_ids(doc_ids)
        if not unique_ids:
            raise ExportAdmissionError(
                "Список документов пуст", code=codes.EMPTY_DOCUMENT_LIST
            )
        if len(unique_ids) > settings.bulk_export_max_docs:
            raise ExportAdmissionError(
                "Превышен лимит документов экспорта", code=codes.DOCUMENT_LIMIT_EXCEEDED
            )

        with _ADMISSION_LOCK:
            if not self._accepting:
                raise ExportAdmissionError(
                    "Очередь экспорта останавливается", code=codes.BULK_EXPORT_QUEUE_FULL
                )
            plan = self._preflight(unique_ids, settings)
            try:
                with session_scope() as s:
                    _assert_capacity(s, settings, user, plan.estimated_output_bytes)
                    params = plan.to_job_params(ip_address)
                    job = Job(
                        job_type=BULK_EXPORT,
                        status=STATUS_QUEUED,
                        created_by_id=getattr(user, "user_id", None),
                        created_by=getattr(user, "username", None),
                        params=params,
                    )
                    s.add(job)
                    s.flush()
                    try:
                        audit_mod.record_in_session(
                            s,
                            action_type=audit_mod.DOCUMENT_BULK_EXPORT_REQUESTED,
                            user_id=getattr(user, "user_id", None),
                            username=getattr(user, "username", None),
                            target_type=audit_mod.TARGET_JOB,
                            target_id=str(job.id),
                            new_value=plan.audit_value(settings),
                            ip_address=ip_address,
                        )
                    except Exception as exc:
                        raise ExportAuditUnavailableError(
                            "Не удалось зарегистрировать аудит массового экспорта",
                            code=codes.BULK_EXPORT_AUDIT_UNAVAILABLE,
                        ) from exc
                    result = _job_to_dict(job)
            except DomainError:
                raise
        self._queue.put(result["id"])
        return result

    def get(self, job_id: int) -> dict | None:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None or job.job_type != BULK_EXPORT:
                return None
            return _job_to_dict(job)

    def ready_dir(self, job_id: int):
        return get_settings().exports_dir / f"{job_id}.ready"

    def active_leases(self, job_id: int) -> int:
        with _ADMISSION_LOCK:
            return sum(count for (leased_job_id, _part), count in self._leases.items() if leased_job_id == job_id)

    def plan_rollback(self, *, include_ready: bool) -> ExportRollbackPlan:
        """Inspect export-only state without changing jobs, audit, or artifacts."""
        with _ADMISSION_LOCK, session_scope() as session:
            jobs = list(session.execute(select(Job.id).where(Job.job_type == BULK_EXPORT)).scalars())
            leased = tuple(sorted(job_id for job_id in jobs if self.active_leases(job_id)))
        root = get_settings().exports_dir
        names: list[str] = []
        total_bytes = 0
        if root.is_dir():
            for path in root.iterdir():
                if not path.is_dir() or _ARTIFACT_DIR_RE.fullmatch(path.name) is None:
                    continue
                if path.name.endswith(".ready") and not include_ready:
                    continue
                names.append(path.name)
                total_bytes += sum(file.stat().st_size for file in path.rglob("*") if file.is_file())
        return ExportRollbackPlan(tuple(sorted(jobs)), tuple(sorted(names)), total_bytes, leased, include_ready)

    def apply_rollback(self, plan: ExportRollbackPlan, *, actor) -> ExportRollbackResult:
        """Retire only the export state described by a freshly reviewed plan.

        A live download lease is a global precondition: returning without any
        mutation is safer than deleting a part while a response is streaming.
        """
        with _ADMISSION_LOCK:
            current = self.plan_rollback(include_ready=plan.include_ready)
            if current.active_lease_job_ids:
                return ExportRollbackResult(
                    0, 0, 0, current.active_lease_job_ids, 0, ("active_download_lease",)
                )
            if current != plan:
                return ExportRollbackResult(0, 0, 0, (), 0, ("rollback_plan_changed",))

            ready_dirs: list[tuple[int, Path, Path]] = []
            with session_scope() as session:
                jobs = [session.get(Job, job_id) for job_id in plan.job_ids]
                ready_ids = [
                    job.id for job in jobs
                    if job is not None and job.status == STATUS_COMPLETED
                    and (job.result or {}).get("artifact_status") == "available"
                    and plan.include_ready
                ]
            for job_id in ready_ids:
                ready = self.ready_dir(job_id)
                deleting = get_settings().exports_dir / f"{job_id}.deleting"
                if not ready.is_dir() or deleting.exists():
                    return ExportRollbackResult(0, 0, 0, (), 0, (f"ready_artifact_conflict:{job_id}",))
                ready.replace(deleting)
                ready_dirs.append((job_id, ready, deleting))

            failed_jobs = audit_events = 0
            try:
                with session_scope() as session:
                    for job_id in plan.job_ids:
                        job = session.get(Job, job_id)
                        if job is None or job.job_type != BULK_EXPORT:
                            continue
                        result = dict(job.result or {})
                        if job.status in PENDING_STATUSES:
                            job.status = STATUS_FAILED
                            job.finished_at = _utcnow()
                            job.error = "operator_rollback"
                            result["artifact_status"] = "rolled_back"
                            result["error_code"] = "operator_rollback"
                            job.result = result
                            audit_mod.record_in_session(
                                session, action_type=audit_mod.DOCUMENT_BULK_EXPORT_FAILED,
                                user_id=getattr(actor, "user_id", "system"),
                                username=getattr(actor, "username", "system"),
                                target_type=audit_mod.TARGET_JOB, target_id=str(job.id),
                                new_value={"reason": "operator_rollback"},
                            )
                            failed_jobs += 1
                            audit_events += 1
                        elif job.id in ready_ids:
                            result["artifact_status"] = "deleted"
                            result["deleted_at"] = _utcnow().isoformat()
                            job.result = result
                            audit_mod.record_in_session(
                                session, action_type=audit_mod.DOCUMENT_BULK_EXPORT_DELETED,
                                user_id=getattr(actor, "user_id", "system"),
                                username=getattr(actor, "username", "system"),
                                target_type=audit_mod.TARGET_JOB, target_id=str(job.id),
                                new_value={"reason": "code_rollback"},
                            )
                            audit_events += 1
            except Exception as exc:
                for _, ready, deleting in ready_dirs:
                    if deleting.exists() and not ready.exists():
                        deleting.replace(ready)
                return ExportRollbackResult(0, 0, 0, (), 0, (f"audit_or_database_failure:{exc}",))

            deleted_ready = deleted_bytes = 0
            errors: list[str] = []
            for _, _, deleting in ready_dirs:
                try:
                    deleted_bytes += sum(item.stat().st_size for item in deleting.rglob("*") if item.is_file())
                    shutil.rmtree(deleting)
                    deleted_ready += 1
                except OSError as exc:
                    errors.append(f"delete_failed:{deleting.name}:{exc}")
            for job_id in plan.job_ids:
                for suffix in ("building",):
                    path = get_settings().exports_dir / f"{job_id}.{suffix}"
                    if path.exists():
                        try:
                            shutil.rmtree(path)
                        except OSError as exc:
                            errors.append(f"delete_failed:{path.name}:{exc}")
            return ExportRollbackResult(
                failed_jobs, deleted_ready, deleted_bytes, (), audit_events, tuple(errors)
            )

    def acquire_download(
        self, job_id: int, part_number: int, user, ip_address: str | None
    ) -> ExportDownloadLease:
        with _ADMISSION_LOCK:
            job = self.get(job_id)
            if job is None:
                raise ExportAdmissionError("Экспорт не найден", code=codes.BULK_EXPORT_GONE)
            result = job.get("result") or {}
            if job["status"] != STATUS_COMPLETED or result.get("artifact_status") != "available":
                raise ExportAdmissionError("Экспорт не готов к скачиванию", code=codes.BULK_EXPORT_NOT_READY)
            expires_at = _parse_utc(result.get("expires_at"))
            if expires_at is None or _utcnow() >= expires_at:
                raise ExportAdmissionError("Срок хранения экспорта истёк", code=codes.BULK_EXPORT_GONE)
            part = next(
                (part for part in result.get("parts") or [] if part.get("number") == part_number),
                None,
            )
            if part is None:
                raise ExportAdmissionError("Часть экспорта не найдена", code=codes.BULK_EXPORT_PART_NOT_FOUND)
            filename = part.get("filename")
            if not isinstance(filename, str) or not filename.startswith(f"documents-export-{job_id}-part-"):
                raise ExportAdmissionError("Артефакт экспорта недоступен", code=codes.BULK_EXPORT_GONE)
            ready_dir = self.ready_dir(job_id).resolve()
            path = (ready_dir / filename).resolve()
            if not path.is_relative_to(ready_dir) or not path.is_file() or path.name != filename:
                raise ExportAdmissionError("Артефакт экспорта недоступен", code=codes.BULK_EXPORT_GONE)
            size_bytes = int(part.get("size_bytes", -1))
            if size_bytes < 0 or path.stat().st_size != size_bytes:
                raise ExportAdmissionError("Артефакт экспорта недоступен", code=codes.BULK_EXPORT_GONE)
            key = (job_id, part_number)
            self._leases[key] = self._leases.get(key, 0) + 1
            try:
                with session_scope() as session:
                    audit_mod.record_in_session(
                        session,
                        action_type=audit_mod.DOCUMENT_BULK_EXPORT_DOWNLOAD_STARTED,
                        user_id=getattr(user, "user_id", None),
                        username=getattr(user, "username", None),
                        target_type=audit_mod.TARGET_JOB,
                        target_id=str(job_id),
                        ip_address=ip_address,
                        new_value={
                            "part_number": part_number,
                            "filename": filename,
                            "size_bytes": size_bytes,
                            "export_count": len(result.get("parts") or []),
                        },
                    )
            except Exception as exc:
                self._release_lease(key)
                raise ExportAuditUnavailableError(
                    "Не удалось зарегистрировать аудит скачивания",
                    code=codes.BULK_EXPORT_AUDIT_UNAVAILABLE,
                ) from exc
            released = False

            def release() -> None:
                nonlocal released
                with _ADMISSION_LOCK:
                    if not released:
                        released = True
                        self._release_lease(key)

            return ExportDownloadLease(path=path, filename=filename, size_bytes=size_bytes, release=release)

    def _release_lease(self, key: tuple[int, int]) -> None:
        count = self._leases.get(key, 0)
        if count <= 1:
            self._leases.pop(key, None)
        else:
            self._leases[key] = count - 1

    def delete_artifacts(
        self,
        job_id: int,
        user,
        ip_address: str | None,
        *,
        reason: str = "deleted",
    ) -> dict:
        if reason not in {"deleted", "expired"}:
            raise ValueError("Некорректная причина удаления экспорта")
        if reason == "expired" and not isinstance(user, audit_mod.SystemUser):
            raise PermissionError("Истечение экспорта может фиксировать только система")
        with _ADMISSION_LOCK:
            job = self.get(job_id)
            if job is None:
                raise ExportAdmissionError("Экспорт не найден", code=codes.BULK_EXPORT_GONE)
            result = job.get("result") or {}
            if result.get("artifact_status") != "available":
                return job
            if self.active_leases(job_id):
                raise ExportAdmissionError("Экспорт сейчас скачивается", code=codes.BULK_EXPORT_NOT_READY)
            ready_dir = self.ready_dir(job_id)
            deleting_dir = get_settings().exports_dir / f"{job_id}.deleting"
            if not ready_dir.is_dir():
                raise ExportAdmissionError("Артефакт экспорта недоступен", code=codes.BULK_EXPORT_GONE)
            if deleting_dir.exists():
                shutil.rmtree(deleting_dir, ignore_errors=True)
            ready_dir.replace(deleting_dir)
            action = (
                audit_mod.DOCUMENT_BULK_EXPORT_EXPIRED
                if reason == "expired"
                else audit_mod.DOCUMENT_BULK_EXPORT_DELETED
            )
            try:
                with session_scope() as session:
                    current = session.get(Job, job_id)
                    if current is None or current.job_type != BULK_EXPORT:
                        raise ExportAdmissionError("Экспорт не найден", code=codes.BULK_EXPORT_GONE)
                    updated_result = dict(current.result or {})
                    updated_result["artifact_status"] = reason
                    updated_result["deleted_at"] = _utcnow().isoformat()
                    current.result = updated_result
                    audit_mod.record_in_session(
                        session,
                        action_type=action,
                        user_id=getattr(user, "user_id", None),
                        username=getattr(user, "username", None),
                        target_type=audit_mod.TARGET_JOB,
                        target_id=str(job_id),
                        ip_address=ip_address,
                        new_value={"reason": reason, "part_count": len(updated_result.get("parts") or [])},
                    )
            except Exception:
                if deleting_dir.exists() and not ready_dir.exists():
                    deleting_dir.replace(ready_dir)
                raise
            shutil.rmtree(deleting_dir, ignore_errors=True)
            return self.get(job_id) or job

    def cleanup_expired(self, now: datetime | None = None) -> ExportCleanupResult:
        now = now or _utcnow()
        expired = skipped_leased = delete_retries = 0
        errors: list[str] = []
        with session_scope() as session:
            job_ids = list(
                session.execute(
                    select(Job.id).where(Job.job_type == BULK_EXPORT, Job.status == STATUS_COMPLETED)
                ).scalars()
            )
        for job_id in job_ids:
            job = self.get(job_id)
            expires_at = _parse_utc((job or {}).get("result", {}).get("expires_at"))
            if job is None or (job.get("result") or {}).get("artifact_status") != "available" or expires_at is None or now < expires_at:
                continue
            if self.active_leases(job_id):
                skipped_leased += 1
                continue
            try:
                self.delete_artifacts(job_id, audit_mod.SystemUser(), None, reason="expired")
                expired += 1
            except Exception:
                errors.append(str(job_id))
        return ExportCleanupResult(expired, skipped_leased, delete_retries, tuple(errors))

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=300)
            except queue.Empty:
                self.run_maintenance_once()
                continue
            try:
                if item is self._stop:
                    return
                self._execute(int(item))
            except Exception:
                # Один повреждённый экспорт не должен останавливать worker и
                # оставлять следующие задания без потребителя.
                logger.exception("Необработанный сбой worker массовых экспортов")
            finally:
                self._queue.task_done()
                try:
                    self.run_maintenance_once()
                except Exception:
                    logger.exception("Сбой обслуживания артефактов экспорта")

    def _execute(self, job_id: int) -> None:
        job = self.get(job_id)
        if job is None or job["status"] != STATUS_QUEUED:
            return
        settings = get_settings()
        building_dir = settings.exports_dir / f"{job_id}.building"
        ready_dir = self.ready_dir(job_id)
        try:
            self._set_running(job_id, total=len((job.get("params") or {}).get("documents") or []))
            parts = self._rehydrate_parts(job)
            built = build_export_parts(
                job_id,
                parts,
                building_dir,
                archive_limit_bytes=settings.bulk_export_part_size_mb * 1024 * 1024,
                on_part_built=lambda part: self._record_part_progress(job_id, part, len(parts)),
            )
            self._verify_built_parts(built)
            if ready_dir.exists():
                raise RuntimeError("Каталог готового экспорта уже существует")
            building_dir.replace(ready_dir)
            result = self._completion_result(job, built)
            self._finish_completed(job_id, result)
        except Exception as exc:
            logger.exception("Сбой массового экспорта %s", job_id)
            try:
                self._retire_artifacts(job_id)
            except Exception:
                # После освобождения места maintenance удалит остатки; ошибка
                # очистки не вправе скрыть исходный исход задания.
                logger.exception("Не удалось очистить артефакты экспорта %s", job_id)
            self._finish_failed(job_id, _failure_code(exc), total=len((job.get("params") or {}).get("documents") or []))

    def _rehydrate_parts(self, job: dict) -> tuple[ExportPart, ...]:
        settings = get_settings()
        root = settings.uploads_dir.resolve()
        params = job.get("params") or {}
        documents: dict[str, ExportDocument] = {}
        for value in params.get("documents") or []:
            doc_id = value.get("doc_id")
            basename = value.get("source_basename")
            if (
                not isinstance(doc_id, str)
                or not _DOC_ID_RE.fullmatch(doc_id)
                or not isinstance(basename, str)
                or basename != Path(basename).name
                or not basename.startswith(f"{doc_id}.")
            ):
                raise ExportSourceChangedError(str(doc_id))
            path = settings.uploads_dir / basename
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                raise ExportSourceChangedError(doc_id)
            documents[doc_id] = ExportDocument(
                doc_id=doc_id,
                filename=str(value.get("filename") or "document.bin"),
                path=path,
                size_bytes=int(value.get("size_bytes")),
                mtime_ns=int(value.get("mtime_ns")),
            )
        parts: list[ExportPart] = []
        for value in params.get("parts") or []:
            doc_ids = value.get("document_ids") or []
            try:
                part_documents = tuple(documents[doc_id] for doc_id in doc_ids)
            except (KeyError, TypeError) as exc:
                raise ExportSourceChangedError("unknown") from exc
            if not part_documents:
                raise ExportSourceChangedError("unknown")
            parts.append(
                ExportPart(
                    number=int(value.get("number")),
                    documents=part_documents,
                    source_bytes=int(value.get("source_bytes")),
                )
            )
        if not parts or sum(len(part.documents) for part in parts) != len(documents):
            raise ExportSourceChangedError("unknown")
        return tuple(parts)

    def _set_running(self, job_id: int, *, total: int) -> None:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None or job.job_type != BULK_EXPORT or job.status != STATUS_QUEUED:
                return
            job.status = STATUS_RUNNING
            job.started_at = _utcnow()
            job.result = {
                "processed": 0,
                "total": total,
                "parts_completed": 0,
                "parts_total": len((job.params or {}).get("parts") or []),
                "artifact_status": "building",
            }

    def _record_part_progress(self, job_id: int, part: BuiltExportPart, parts_total: int) -> None:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None or job.job_type != BULK_EXPORT or job.status != STATUS_RUNNING:
                return
            prior = dict(job.result or {})
            job.result = {
                **prior,
                "processed": int(prior.get("processed", 0)) + part.document_count,
                "parts_completed": int(prior.get("parts_completed", 0)) + 1,
                "parts_total": parts_total,
            }

    @staticmethod
    def _verify_built_parts(parts: list[BuiltExportPart]) -> None:
        for part in parts:
            if not part.path.is_file() or part.path.stat().st_size != part.size_bytes:
                raise RuntimeError("Часть экспорта не прошла проверку")
            with zipfile.ZipFile(part.path) as archive:
                if archive.testzip() is not None:
                    raise RuntimeError("Часть экспорта повреждена")

    def _completion_result(self, job: dict, parts: list[BuiltExportPart]) -> dict:
        finished_at = _utcnow()
        expires_at = finished_at + timedelta(hours=get_settings().bulk_export_ttl_hours)
        params = job.get("params") or {}
        total = len(params.get("documents") or [])
        return {
            "processed": total,
            "total": total,
            "parts_completed": len(parts),
            "parts_total": len(parts),
            "artifact_status": "available",
            "source_bytes": int(params.get("total_source_bytes", 0)),
            "total_bytes": sum(part.size_bytes for part in parts),
            "expires_at": expires_at.isoformat(),
            "parts": [
                {
                    "number": part.number,
                    "filename": part.filename,
                    "size_bytes": part.size_bytes,
                    "document_count": part.document_count,
                }
                for part in parts
            ],
            "finished_at": finished_at.isoformat(),
        }

    def _finish_completed(self, job_id: int, result: dict) -> None:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None or job.job_type != BULK_EXPORT:
                return
            job.status = STATUS_COMPLETED
            job.finished_at = _utcnow()
            job.result = result
            audit_mod.record_in_session(
                session,
                action_type=audit_mod.DOCUMENT_BULK_EXPORT_COMPLETED,
                user_id=job.created_by_id,
                username=job.created_by,
                target_type=audit_mod.TARGET_JOB,
                target_id=str(job.id),
                new_value={
                    "document_count": result["total"],
                    "source_bytes": result["source_bytes"],
                    "total_bytes": result["total_bytes"],
                    "part_count": result["parts_total"],
                },
            )

    def _finish_failed(self, job_id: int, error_code: str, *, total: int) -> None:
        public_error = (
            "Недостаточно свободного места на диске"
            if error_code == codes.STORAGE_FULL
            else "Источник документа изменился до завершения экспорта"
            if error_code == codes.BULK_EXPORT_SOURCE_CHANGED
            else "Не удалось подготовить массовый экспорт"
        )
        try:
            with session_scope() as session:
                job = session.get(Job, job_id)
                if job is None or job.job_type != BULK_EXPORT:
                    return
                job.status = STATUS_FAILED
                job.finished_at = _utcnow()
                job.error = public_error
                job.result = {"processed": 0, "total": total, "error_code": error_code, "artifact_status": "none"}
                audit_mod.record_in_session(
                    session,
                    action_type=audit_mod.DOCUMENT_BULK_EXPORT_FAILED,
                    user_id=job.created_by_id,
                    username=job.created_by,
                    target_type=audit_mod.TARGET_JOB,
                    target_id=str(job.id),
                    new_value={"error_code": error_code, "processed": 0, "total": total},
                )
        except Exception:
            logger.exception("Не удалось атомарно записать сбой экспорта %s", job_id)

    def _retire_artifacts(self, job_id: int) -> None:
        settings = get_settings()
        for suffix in ("building", "ready"):
            source = settings.exports_dir / f"{job_id}.{suffix}"
            deleting = settings.exports_dir / f"{job_id}.deleting"
            if source.exists():
                if deleting.exists():
                    shutil.rmtree(deleting, ignore_errors=True)
                source.replace(deleting)
            if deleting.exists():
                shutil.rmtree(deleting, ignore_errors=True)

    def recover_after_restart(self) -> None:
        with _ADMISSION_LOCK, session_scope() as session:
            queued = list(
                session.execute(
                    select(Job.id).where(Job.job_type == BULK_EXPORT, Job.status == STATUS_QUEUED)
                ).scalars()
            )
            running = list(
                session.execute(
                    select(Job.id).where(Job.job_type == BULK_EXPORT, Job.status == STATUS_RUNNING)
                ).scalars()
            )
        for job_id in running:
            self._retire_artifacts(job_id)
            self._finish_failed(job_id, codes.JOB_INTERRUPTED, total=0)
        for job_id in queued:
            self._queue.put(job_id)
        self.run_maintenance_once()

    def run_maintenance_once(self) -> None:
        settings = get_settings()
        settings.exports_dir.mkdir(parents=True, exist_ok=True)
        for path in settings.exports_dir.iterdir():
            if not path.is_dir():
                continue
            match = _ARTIFACT_DIR_RE.fullmatch(path.name)
            if match is None:
                if path.name.endswith((".building", ".ready", ".deleting")):
                    logger.warning("Удалён неизвестный каталог экспорта: %s", path.name)
                    shutil.rmtree(path, ignore_errors=True)
                continue
            job_id, state = int(match.group(1)), match.group(2)
            if state == "deleting":
                shutil.rmtree(path, ignore_errors=True)
                continue
            job = self.get(job_id)
            if state == "building" or job is None or job["status"] != STATUS_COMPLETED:
                self._retire_artifacts(job_id)

    def shutdown(self, timeout_seconds: float = 5.0) -> None:
        with _ADMISSION_LOCK:
            self._accepting = False
            self._queue.put(self._stop)
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=max(0.0, timeout_seconds))

    def _preflight(self, doc_ids: list[str], settings) -> ExportPlan:
        documents = _snapshot_sources(doc_ids, settings)
        total_source_bytes = sum(item.size_bytes for item in documents)
        total_limit_bytes = settings.bulk_export_max_total_mb * 1024 * 1024
        if total_source_bytes > total_limit_bytes:
            raise ExportAdmissionError(
                "Превышен лимит объёма экспорта", code=codes.BULK_EXPORT_SIZE_LIMIT
            )
        payload_limit = settings.bulk_export_part_size_mb * 1024 * 1024 - _MANIFEST_HEADROOM_BYTES
        try:
            parts = partition_documents(documents, payload_limit)
        except (ExportPartTooLargeError, ValueError) as exc:
            raise ExportAdmissionError(
                "Документ не помещается в часть экспорта", code=codes.BULK_EXPORT_SIZE_LIMIT
            ) from exc
        return ExportPlan(
            documents=tuple(documents),
            parts=tuple(parts),
            total_bytes=total_source_bytes,
            estimated_output_bytes=total_source_bytes + _MANIFEST_HEADROOM_BYTES * len(parts),
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _failure_code(exc: Exception) -> str:
    if is_storage_full(exc):
        return codes.STORAGE_FULL
    if isinstance(exc, ExportSourceChangedError):
        return codes.BULK_EXPORT_SOURCE_CHANGED
    if isinstance(exc, ExportPartTooLargeError):
        return codes.BULK_EXPORT_SIZE_LIMIT
    return codes.INTERNAL_ERROR


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def shutdown_export_queue_if_started(timeout_seconds: float = 5.0) -> None:
    global _export_queue
    with _EXPORT_QUEUE_LOCK:
        if _export_queue is not None:
            _export_queue.shutdown(timeout_seconds)
            _export_queue = None


def _reset_export_queue_for_tests() -> None:
    global _export_queue
    shutdown_export_queue_if_started()


def get_export_queue() -> ExportQueue:
    """Return the process-wide export queue without involving generic jobs."""
    global _export_queue
    with _EXPORT_QUEUE_LOCK:
        if _export_queue is None:
            _export_queue = ExportQueue()
        return _export_queue


def _ordered_unique_ids(doc_ids: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for doc_id in doc_ids:
        if not isinstance(doc_id, str) or not _DOC_ID_RE.fullmatch(doc_id):
            raise ExportAdmissionError(
                "Некорректный идентификатор документа", code=codes.BULK_EXPORT_SOURCE_CONFLICT
            )
        if doc_id not in seen:
            seen.add(doc_id)
            result.append(doc_id)
    return result


def _snapshot_sources(doc_ids: list[str], settings) -> list[ExportDocument]:
    metadata = DocumentRegistry().get_export_metadata_many(doc_ids)
    root = settings.uploads_dir.resolve()
    documents: list[ExportDocument] = []
    for doc_id in doc_ids:
        record = metadata.get(doc_id)
        if record is None or record["deleted_at"] is not None:
            raise ExportAdmissionError("Источник экспорта недоступен", code=codes.BULK_EXPORT_SOURCE_CONFLICT)
        matches = sorted(settings.uploads_dir.glob(f"{doc_id}.*"))
        if len(matches) != 1:
            raise ExportAdmissionError("Источник экспорта неоднозначен или отсутствует", code=codes.BULK_EXPORT_SOURCE_CONFLICT)
        source = matches[0]
        if source.is_symlink() or not source.is_file():
            raise ExportAdmissionError("Источник экспорта недоступен", code=codes.BULK_EXPORT_SOURCE_CONFLICT)
        resolved = source.resolve()
        if not resolved.is_relative_to(root):
            raise ExportAdmissionError("Источник экспорта вне разрешённого хранилища", code=codes.BULK_EXPORT_SOURCE_CONFLICT)
        stat = source.stat()
        documents.append(
            ExportDocument(
                doc_id=doc_id,
                filename=record["filename"],
                path=source,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
            )
        )
    return documents


def _assert_capacity(session, settings, user, estimated_bytes: int) -> None:
    now = datetime.now(timezone.utc)
    pending_count = session.scalar(
        select(func.count()).select_from(Job).where(
            Job.job_type == BULK_EXPORT,
            Job.status.in_(PENDING_STATUSES),
        )
    ) or 0
    if pending_count >= settings.bulk_export_max_pending:
        raise ExportAdmissionError("Очередь экспорта заполнена", code=codes.BULK_EXPORT_QUEUE_FULL)

    active_for_user = session.scalar(
        select(func.count()).select_from(Job).where(
            Job.job_type == BULK_EXPORT,
            Job.status.in_(PENDING_STATUSES),
            Job.created_by_id == getattr(user, "user_id", None),
        )
    ) or 0
    if active_for_user >= settings.bulk_export_max_active_per_user:
        raise ExportAdmissionError(
            "У пользователя уже есть активный экспорт", code=codes.BULK_EXPORT_USER_ACTIVE
        )

    recent_count = session.scalar(
        select(func.count()).select_from(Job).where(
            Job.job_type == BULK_EXPORT,
            Job.created_by_id == getattr(user, "user_id", None),
            Job.created_at >= now - timedelta(hours=1),
        )
    ) or 0
    if recent_count >= settings.bulk_export_max_ops_per_hour:
        raise ExportAdmissionError(
            "Превышен часовой лимит экспорта",
            code=codes.BULK_EXPORT_RATE_LIMITED,
            retry_after_seconds=3600,
        )

    # Keep the JSON accounting portable across SQLite tests and PostgreSQL.
    # The ready set is bounded by the retention quota, so summing its small
    # result payloads in Python is preferable to database-specific JSON SQL.
    ready_estimate = sum(
        int((result or {}).get("total_bytes", 0))
        for result in session.execute(
            select(Job.result).where(
                Job.job_type == BULK_EXPORT,
                Job.status == STATUS_READY,
            )
        ).scalars()
    )
    pending_reserved_bytes = sum(
        int((params or {}).get("estimated_output_bytes", 0))
        for params in session.execute(
            select(Job.params).where(
                Job.job_type == BULK_EXPORT,
                Job.status.in_(PENDING_STATUSES),
            )
        ).scalars()
    )
    if (
        ready_estimate + pending_reserved_bytes + estimated_bytes
        > settings.bulk_export_max_retained_mb * 1024 * 1024
    ):
        raise ExportAdmissionError(
            "Превышена квота готовых экспортов", code=codes.BULK_EXPORT_STORAGE_QUOTA
        )

    exports_dir = settings.exports_dir
    exports_dir.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(exports_dir).free
    if (
        free_bytes - pending_reserved_bytes - estimated_bytes
        < settings.bulk_export_min_free_mb * 1024 * 1024
    ):
        raise ExportAdmissionError(
            "Недостаточен резерв свободного места", code=codes.BULK_EXPORT_STORAGE_RESERVE
        )


def _job_to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "created_by_id": job.created_by_id,
        "created_by": job.created_by,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "params": job.params,
        "result": job.result,
        "error": job.error,
    }
