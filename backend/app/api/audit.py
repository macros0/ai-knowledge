"""Роут журнала ИБ (audit_log): read-only доступ для роли Security."""
from fastapi import APIRouter, Depends, Response

from app.auth.models import User
from app.auth.service import require_role
from app.models.schemas import AuditQueryParams
from app.services.audit import ACTION_TYPES, get_audit

router = APIRouter(prefix="/audit", tags=["audit"])

_audit = get_audit()


@router.get("")
def list_audit(
    params: AuditQueryParams = Depends(),
    user: User = Depends(require_role("security")),
):
    """Read-only журнал ИБ с фильтрами по пользователю/типу/периоду."""
    entries = _audit.query(
        action_type=params.action_type,
        user_id=params.user_id,
        target_id=params.target_id,
        since=params.since,
        until=params.until,
        limit=params.limit,
        offset=params.offset,
    )
    return {"entries": entries}


@router.get("/action-types")
def list_action_types(user: User = Depends(require_role("security"))):
    """Полный канонический перечень типов действий (для фильтра журнала)."""
    return {"action_types": sorted(ACTION_TYPES)}


@router.get("/users")
def list_audit_users(user: User = Depends(require_role("security"))):
    """Справочник пользователей, встречающихся в журнале (для фильтра)."""
    return {"users": _audit.distinct_users()}


@router.get("/config")
def audit_config(response: Response, user: User = Depends(require_role("security"))):
    from app.config import get_settings
    from app.services.audit_modes import get_audit_mode
    settings = get_settings()
    mode = get_audit_mode(settings)
    response.headers["Cache-Control"] = "no-store"
    return {"effective_mode": mode.effective_mode, "db_enabled": mode.db_enabled,
            "stdout_mode": settings.audit_stdout_mode, "stdout_format": settings.audit_stdout_format}
