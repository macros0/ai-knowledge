"""Роуты блокировки пользователей (единственное активное действие роли Security)."""
from fastapi import APIRouter, Depends, HTTPException

from app.auth.models import User
from app.auth.service import require_role
from app.models.schemas import BlockUserRequest
from app.services import audit
from app.services.blocklist import get_blocklist

router = APIRouter(prefix="/users", tags=["users"])

_blocklist = get_blocklist()


@router.get("/blocks")
def list_blocks(user: User = Depends(require_role("security"))):
    """Список активных блокировок (read-only для роли Security)."""
    return {"blocks": _blocklist.list_active()}


@router.post("/{external_id}/block")
def block_user(
    external_id: str,
    body: BlockUserRequest,
    user: User = Depends(require_role("security")),
):
    """Блокировка пользователя/сессий (требует роли Security)."""
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
    )
    return block


@router.post("/{external_id}/unblock")
def unblock_user(
    external_id: str,
    user: User = Depends(require_role("security")),
):
    n = _blocklist.unblock(external_id)
    audit.record(
        user,
        audit.USER_UNBLOCK,
        audit.TARGET_USER,
        target_id=external_id,
    )
    if n == 0:
        raise HTTPException(status_code=404, detail="Активных блокировок не найдено")
    return {"status": "unblocked", "count": n}
