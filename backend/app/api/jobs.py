"""Роуты системных (массовых) операций администратора: список, статус, одобрение, отмена."""
from app.api import errors
from app.services.errors import DomainError
from app.api.errors import ApiError
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.auth.models import User
from app.auth.service import require_role
from app.config import get_settings
from app.services.export_queue import ExportAdmissionError, ExportAuditUnavailableError, get_export_queue
from app.services.job_queue import (
    JobNotFoundError,
    SelfApprovalError,
    get_job_queue,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])

EXPORT_ERROR_STATUS = {
    errors.BULK_EXPORT_DISABLED: 404,
    errors.EMPTY_DOCUMENT_LIST: 400,
    errors.INVALID_REQUEST: 400,
    errors.BULK_EXPORT_SOURCE_CONFLICT: 409,
    errors.BULK_EXPORT_USER_ACTIVE: 409,
    errors.BULK_EXPORT_SIZE_LIMIT: 413,
    errors.BULK_EXPORT_RATE_LIMITED: 429,
    errors.BULK_EXPORT_QUEUE_FULL: 503,
    errors.BULK_EXPORT_AUDIT_UNAVAILABLE: 503,
    errors.BULK_EXPORT_STORAGE_QUOTA: 507,
    errors.BULK_EXPORT_STORAGE_RESERVE: 507,
    errors.BULK_EXPORT_NOT_READY: 409,
    errors.BULK_EXPORT_GONE: 410,
    errors.BULK_EXPORT_PART_NOT_FOUND: 404,
}


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _raise_export_error(exc: ExportAdmissionError) -> None:
    headers = None
    if exc.code == errors.BULK_EXPORT_RATE_LIMITED:
        headers = {"Retry-After": str(max(1, exc.retry_after_seconds or 1))}
    raise ApiError(
        status_code=EXPORT_ERROR_STATUS.get(exc.code, 400),
        code=exc.code,
        detail=str(exc),
        headers=headers,
    ) from exc


@router.get("")
def list_jobs(
    limit: int = 100,
    offset: int = 0,
    user: User = Depends(require_role("admin")),
):
    return {"jobs": get_job_queue().list(limit=limit, offset=offset)}


@router.get("/{job_id}")
def get_job(job_id: int, user: User = Depends(require_role("admin"))):
    job = get_job_queue().get(job_id)
    if job is None:
        raise ApiError(
            status_code=404,
            code=errors.JOB_NOT_FOUND,
            detail="Задача не найдена",
        )
    return job


@router.get("/{job_id}/export/{part_number}")
def download_export_part(
    job_id: int,
    part_number: int,
    request: Request,
    user: User = Depends(require_role("admin")),
):
    if not get_settings().bulk_export_download_enabled:
        raise ApiError(
            status_code=404,
            code=errors.BULK_EXPORT_DISABLED,
            detail="Выдача массовых экспортов отключена",
        )
    try:
        lease = get_export_queue().acquire_download(job_id, part_number, user, _client_ip(request))
    except ExportAuditUnavailableError as exc:
        raise ApiError(status_code=503, code=exc.code, detail=str(exc)) from exc
    except ExportAdmissionError as exc:
        _raise_export_error(exc)
    try:
        return FileResponse(
            lease.path,
            media_type="application/zip",
            filename=lease.filename,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            background=BackgroundTask(lease.release),
        )
    except Exception:
        lease.release()
        raise


@router.delete("/{job_id}/export")
def delete_export(
    job_id: int,
    request: Request,
    user: User = Depends(require_role("admin")),
):
    try:
        return get_export_queue().delete_artifacts(job_id, user, _client_ip(request))
    except ExportAuditUnavailableError as exc:
        raise ApiError(status_code=503, code=exc.code, detail=str(exc)) from exc
    except ExportAdmissionError as exc:
        _raise_export_error(exc)


@router.post("/{job_id}/approve")
def approve_job(job_id: int, request: Request, user: User = Depends(require_role("admin"))):
    """Одобрение задачи (four-eyes) вторым администратором."""
    try:
        return get_job_queue().approve(job_id, user, ip_address=_client_ip(request))
    except JobNotFoundError as exc:
        raise ApiError(
            status_code=404,
            code=errors.JOB_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except SelfApprovalError as exc:
        raise ApiError(
            status_code=403,
            code=errors.SELF_APPROVAL,
            detail=str(exc),
        ) from exc
    except DomainError as exc:
        raise errors.domain_error(exc, 400) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=400,
            code=errors.INVALID_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/{job_id}/cancel")
def cancel_job(job_id: int, request: Request, user: User = Depends(require_role("admin"))):
    try:
        return get_job_queue().cancel(job_id, user, ip_address=_client_ip(request))
    except JobNotFoundError as exc:
        raise ApiError(
            status_code=404,
            code=errors.JOB_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except DomainError as exc:
        raise errors.domain_error(exc, 400) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=400,
            code=errors.INVALID_REQUEST,
            detail=str(exc),
        ) from exc
