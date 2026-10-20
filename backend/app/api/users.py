"""Роуты блокировки пользователей (единственное активное действие роли Security)."""
from app.api import errors
from app.api.errors import ApiError
from fastapi import APIRouter, Depends, Request

from app.auth.models import User
from app.auth.service import require_role
from app.models.schemas import BlockUserRequest
from app.services import audit
from app.services.audit_security import enabled
from app.services.audit_event import AuditContext
from app.db.session import session_scope
from sqlalchemy.exc import SQLAlchemyError
from app.services.blocklist import get_blocklist

router = APIRouter(prefix="/users", tags=["users"])

_blocklist = get_blocklist()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/blocks")
def list_blocks(user: User = Depends(require_role("security"))):
    """Список активных блокировок (read-only для роли Security)."""
    return {"blocks": _blocklist.list_active()}


@router.post("/{external_id}/block")
def block_user(
    external_id: str,
    body: BlockUserRequest,
    request: Request,
    user: User = Depends(require_role("security")),
):
    """Блокировка пользователя/сессий (требует роли Security)."""
    if enabled():
        try:
            with session_scope() as session:
                block = _blocklist.block_in_session(session, external_id,
                    blocked_by=user.username or user.user_id, reason=body.reason, expires_at=body.expires_at)
                audit.record_in_session(session, action_type=audit.USER_BLOCK, user_id=user.user_id,
                    username=user.username, target_type=audit.TARGET_USER, target_id=external_id,
                    new_value={"reason": body.reason}, ip_address=_client_ip(request),
                    event_context=AuditContext(actor_type="user", request_id=getattr(request.state,"request_id",None)))
            return block
        except (SQLAlchemyError, ValueError, TypeError):
            raise ApiError(status_code=503, code=errors.DEPENDENCY_UNAVAILABLE, detail="Журнал безопасности временно недоступен") from None
    block = _blocklist.block(
        external_id,
        blocked_by=user.username or user.user_id,
        reason=body.reason,
        expires_at=body.expires_at,
    )
    audit.record(
        user,
        audit.USER_BLOCK,
        audit.TARGET_USER,
        target_id=external_id,
        new_value={"reason": body.reason},
        ip_address=_client_ip(request),
    )
    return block


@router.post("/{external_id}/unblock")
def unblock_user(
    external_id: str,
    request: Request,
    user: User = Depends(require_role("security")),
):
    if enabled():
        try:
            with session_scope() as session:
                n = _blocklist.unblock_in_session(session, external_id)
                if n == 0:
                    raise ApiError(status_code=404, code=errors.VALUE_NOT_FOUND, detail="Активных блокировок не найдено")
                audit.record_in_session(session, action_type=audit.USER_UNBLOCK, user_id=user.user_id,
                    username=user.username, target_type=audit.TARGET_USER, target_id=external_id,
                    new_value={"count": n}, ip_address=_client_ip(request),
                    event_context=AuditContext(actor_type="user", request_id=getattr(request.state,"request_id",None)))
            return {"status": "unblocked", "count": n}
        except (SQLAlchemyError, ValueError, TypeError):
            raise ApiError(status_code=503, code=errors.DEPENDENCY_UNAVAILABLE, detail="Журнал безопасности временно недоступен") from None
    n = _blocklist.unblock(external_id)
    if n == 0:
        raise ApiError(
            status_code=404,
            code=errors.VALUE_NOT_FOUND,
            detail="Активных блокировок не найдено",
        )
    # Audit — только фактическая разблокировка: запись ПОСЛЕ проверки n > 0,
    # иначе журнал ИБ фиксирует фантомное действие по несуществующей блокировке.
    audit.record(
        user,
        audit.USER_UNBLOCK,
        audit.TARGET_USER,
        target_id=external_id,
        ip_address=_client_ip(request),
    )
    return {"status": "unblocked", "count": n}
