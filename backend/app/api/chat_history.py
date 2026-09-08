"""Роуты истории чата (Этап 6).

Собственная история (владелец):
  - GET    /chat/history                      — список своих тредов (без audit);
  - GET    /chat/history/{session_id}         — свой тред (без audit);
  - DELETE /chat/history/{session_id}         — soft delete треда.

Чужая история (Security/Admin, read-only):
  - GET /chat/admin/history/users             — distinct пользователи с историей;
  - GET /chat/admin/history/{user_id}         — треды пользователя (без audit);
  - GET /chat/admin/history/{user_id}/{sid}   — тред пользователя (+ audit
    chat_history_view — только здесь, при открытии содержимого конкретного треда).
"""
from app.api import errors
from app.api.errors import ApiError
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth.models import User
from app.auth.service import require_role, require_user
from app.models.schemas import (
    ChatAdminUserListOut,
    ChatHistoryListOut,
    ChatHistoryThreadOut,
)
from app.services import audit, chat_history

router = APIRouter(prefix="/chat", tags=["chat_history"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/history", response_model=ChatHistoryListOut)
def list_own_history(
    user: User = Depends(require_user),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
):
    """Список собственных тредов (активные, не удалённые). Audit-записи нет."""
    sessions, total = chat_history.list_sessions(user.user_id, limit=limit, offset=offset)
    return {"sessions": sessions, "total": total, "limit": limit, "offset": offset}


@router.get("/history/{session_id}", response_model=ChatHistoryThreadOut)
def get_own_thread(session_id: str, user: User = Depends(require_user)):
    """Содержимое собственного треда. Audit-записи нет (свои данные)."""
    try:
        thread = chat_history.get_thread(session_id, user.user_id, check_owner=True)
    except chat_history.ChatOwnershipError as exc:
        raise ApiError(
            status_code=403,
            code=errors.FORBIDDEN,
            detail=str(exc),
        ) from exc
    if thread is None:
        raise ApiError(
            status_code=404,
            code=errors.SESSION_NOT_FOUND,
            detail="Сессия не найдена",
        )
    return thread


@router.delete("/history/{session_id}")
def delete_own_session(session_id: str, user: User = Depends(require_user)):
    """Soft delete собственного треда (окно хранения, автоочистка фоном)."""
    try:
        deleted = chat_history.soft_delete_session(session_id, user)
    except chat_history.ChatOwnershipError as exc:
        raise ApiError(
            status_code=403,
            code=errors.FORBIDDEN,
            detail=str(exc),
        ) from exc
    if not deleted:
        raise ApiError(
            status_code=404,
            code=errors.SESSION_NOT_FOUND,
            detail="Сессия не найдена",
        )
    return {"status": "deleted", "session_id": session_id}


@router.get("/admin/history/users", response_model=ChatAdminUserListOut)
def list_history_users(user: User = Depends(require_role("security", "admin"))):
    """Distinct пользователи, у которых есть история чата (для экрана поиска)."""
    return {"users": chat_history.list_distinct_users()}


@router.get("/admin/history/{user_id}", response_model=ChatHistoryListOut)
def list_user_history(
    user_id: str,
    user: User = Depends(require_role("security", "admin")),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
):
    """Список тредов другого пользователя. Audit-записи нет (только список)."""
    sessions, total = chat_history.list_sessions(user_id, limit=limit, offset=offset)
    return {"sessions": sessions, "total": total, "limit": limit, "offset": offset}


@router.get("/admin/history/{user_id}/{session_id}", response_model=ChatHistoryThreadOut)
def get_user_thread(
    user_id: str,
    session_id: str,
    request: Request,
    user: User = Depends(require_role("security", "admin")),
):
    """Содержимое чужого треда (read-only). Audit: chat_history_view."""
    try:
        thread = chat_history.get_thread(session_id, user_id, check_owner=True)
    except chat_history.ChatOwnershipError:
        # Сессия принадлежит не user_id — для запрошенного пользователя не найдена.
        thread = None
    if thread is None:
        raise ApiError(
            status_code=404,
            code=errors.SESSION_NOT_FOUND,
            detail="Сессия не найдена",
        )
    audit.record(
        user,
        audit.CHAT_HISTORY_VIEW,
        audit.TARGET_CHAT,
        target_id=session_id,
        meta={"owner_user_id": user_id},
        ip_address=_client_ip(request),
    )
    return thread
