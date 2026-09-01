"""Роуты системных (массовых) операций администратора: список, статус, одобрение, отмена."""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.models import User
from app.auth.service import require_role
from app.services.job_queue import (
    JobNotFoundError,
    SelfApprovalError,
    get_job_queue,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])

_job_queue = get_job_queue()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("")
def list_jobs(
    limit: int = 100,
    offset: int = 0,
    user: User = Depends(require_role("admin")),
):
    return {"jobs": _job_queue.list(limit=limit, offset=offset)}


@router.get("/{job_id}")
def get_job(job_id: int, user: User = Depends(require_role("admin"))):
    job = _job_queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return job


@router.post("/{job_id}/approve")
def approve_job(job_id: int, request: Request, user: User = Depends(require_role("admin"))):
    """Одобрение задачи (four-eyes) вторым администратором."""
    try:
        return _job_queue.approve(job_id, user, ip_address=_client_ip(request))
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SelfApprovalError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{job_id}/cancel")
def cancel_job(job_id: int, request: Request, user: User = Depends(require_role("admin"))):
    try:
        return _job_queue.cancel(job_id, user, ip_address=_client_ip(request))
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
