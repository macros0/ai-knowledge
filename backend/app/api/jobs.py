"""Роуты системных (массовых) операций администратора: список, статус, одобрение, отмена."""
from app.api import errors
from app.services.errors import DomainError
from app.api.errors import ApiError
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.models import User
from app.auth.service import require_role
from app.services.job_queue import (
    JobNotFoundError,
    SelfApprovalError,
    get_job_queue,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


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
