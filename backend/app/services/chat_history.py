"""История чата (Этап 6): персистентные треды + мягкое удаление + автоочистка.

Модель данных: ChatSession (тред) + ChatMessage (сообщение, снапшот sources).

Правила владения (защита от подмены session_id клиентом):
  - `store_turn` пишет строго в сессию текущего аутентифицированного user_id;
    если переданный session_id существует, но принадлежит другому — отказ
    (ChatOwnershipError), а не молчаливое присвоение чужого треда.
  - `get_thread` с check_owner=True возвращает тред только его владельцу.

Жизненный цикл (аналог корзины документов, 4a.2):
  - активная история хранится бессрочно;
  - ручное удаление → soft delete (deleted_at/deleted_by);
  - фоновая задача по истечении chat_history_retention_days физически удаляет
    тред (каскад на сообщения) и пишет CHAT_HISTORY_AUTO_DELETE (user_id=system).

Просмотр чужой истории (Security/Admin) фиксируется в audit_log как
CHAT_HISTORY_VIEW — но это делает роут (api/chat_history.py), не сервис.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import ChatMessage, ChatSession
from app.db.session import session_scope
from app.services import audit

logger = logging.getLogger(__name__)


class ChatOwnershipError(Exception):
    """Попытка обратиться к чужому треду (session_id принадлежит другому user_id)."""


class _SystemUser:
    """Прокси-«пользователь» для аудита автоочистки истории (системное действие)."""

    user_id = "system"
    username = "system"


def is_valid_session_id(value: str) -> bool:
    """Проверяет, что строка — канонический UUID (клиентский session_id)."""
    if not value:
        return False
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session_to_dict(s: ChatSession, message_count: int | None = None) -> dict:
    if message_count is None:
        message_count = len(s.messages_rel)
    return {
        "session_id": s.id,
        "title": s.title,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "message_count": message_count,
        "deleted_at": s.deleted_at,
    }


def store_turn(
    session_id: str | None,
    user,
    query: str,
    answer: str,
    sources: list[dict] | None = None,
) -> str:
    """Сохраняет пару сообщений (user/assistant) в тред. Возвращает session_id.

    `session_id` None/пустой → создаётся новая сессия (uuid4) с user_id=user.user_id.
    Иначе — сессия либо принадлежит текущему пользователю (reuse), либо бросает
    ChatOwnershipError. Заголовок треда — первое сообщение пользователя.
    """
    user_id = getattr(user, "user_id", None) or "anonymous"
    username = getattr(user, "username", None) or "anonymous"
    sources = sources or []

    with session_scope() as s:
        if session_id:
            sess = s.get(ChatSession, session_id)
            if sess is not None:
                if sess.user_id != user_id:
                    raise ChatOwnershipError("Сессия принадлежит другому пользователю")
                if not sess.title:
                    sess.title = query[:1024]
                sess.updated_at = _now()
            else:
                sess = ChatSession(
                    id=session_id,
                    user_id=user_id,
                    username=username,
                    title=query[:1024],
                )
                s.add(sess)
        else:
            session_id = str(uuid.uuid4())
            sess = ChatSession(
                id=session_id,
                user_id=user_id,
                username=username,
                title=query[:1024],
            )
            s.add(sess)

        s.add(ChatMessage(session_id=sess.id, role="user", content=query))
        s.add(
            ChatMessage(
                session_id=sess.id,
                role="assistant",
                content=answer,
                sources=sources,
            )
        )
        return sess.id


def list_sessions(user_id: str, limit: int | None = None, offset: int = 0) -> tuple[list[dict], int]:
    """Активные (не удалённые) треды пользователя, по updated_at desc."""
    with session_scope() as s:
        base = select(ChatSession).where(
            ChatSession.user_id == user_id,
            ChatSession.deleted_at.is_(None),
        )
        total = s.execute(
            select(func.count()).select_from(base.subquery())
        ).scalar_one()

        rows = (
            s.execute(
                base.order_by(ChatSession.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        counts = _message_counts(s, [r.id for r in rows])
        return [_session_to_dict(r, counts.get(r.id, 0)) for r in rows], total


def get_thread(session_id: str, user_id: str | None = None, *, check_owner: bool = True) -> dict | None:
    """Тред целиком (сессия + сообщения). None — не найден.

    check_owner=True: если сессия принадлежит другому user_id → ChatOwnershipError.
    """
    with session_scope() as s:
        sess = s.get(ChatSession, session_id)
        if sess is None:
            return None
        if check_owner and user_id is not None and sess.user_id != user_id:
            raise ChatOwnershipError("Сессия принадлежит другому пользователю")
        messages = (
            s.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
            .scalars()
            .all()
        )
        return {
            "session_id": sess.id,
            "title": sess.title,
            "created_at": sess.created_at,
            "updated_at": sess.updated_at,
            "deleted_at": sess.deleted_at,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "sources": m.sources or [],
                    "created_at": m.created_at,
                }
                for m in messages
            ],
        }


def soft_delete_session(session_id: str, user) -> bool:
    """Помечает тред удалённым (окно хранения). Возвращает False, если тред не найден/чужой."""
    user_id = getattr(user, "user_id", None) or "anonymous"
    with session_scope() as s:
        sess = s.get(ChatSession, session_id)
        if sess is None:
            return False
        if sess.user_id != user_id:
            raise ChatOwnershipError("Сессия принадлежит другому пользователю")
        if sess.deleted_at is None:
            sess.deleted_at = _now()
            sess.deleted_by = getattr(user, "username", None) or "anonymous"
        return True


def list_distinct_users() -> list[dict]:
    """Distinct владельцев тредов (для admin-экрана поиска пользователя)."""
    with session_scope() as s:
        rows = s.execute(
            select(
                ChatSession.user_id,
                ChatSession.username,
                func.count(ChatSession.id).label("session_count"),
            )
            .group_by(ChatSession.user_id, ChatSession.username)
            .order_by(func.count(ChatSession.id).desc())
        ).all()
        return [
            {"user_id": r.user_id, "username": r.username, "session_count": r.session_count}
            for r in rows
        ]


def _message_counts(s, session_ids: list[str]) -> dict[str, int]:
    if not session_ids:
        return {}
    rows = s.execute(
        select(ChatMessage.session_id, func.count(ChatMessage.id))
        .where(ChatMessage.session_id.in_(session_ids))
        .group_by(ChatMessage.session_id)
    ).all()
    return {sid: n for sid, n in rows}


def purge_expired_sessions() -> int:
    """Физически удаляет треды с истёкшим окном корзины. Возвращает число удалённых.

    Каскад сносит сообщения. Пишет CHAT_HISTORY_AUTO_DELETE в audit_log (system).
    """
    settings = get_settings()
    cutoff = _now() - timedelta(days=settings.chat_history_retention_days)
    with session_scope() as s:
        rows = (
            s.execute(
                select(ChatSession).where(
                    ChatSession.deleted_at.is_not(None),
                    ChatSession.deleted_at < cutoff,
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return 0
        removed = 0
        for sess in rows:
            audit.record(
                _SystemUser(),
                audit.CHAT_HISTORY_AUTO_DELETE,
                audit.TARGET_CHAT,
                target_id=sess.id,
                old_value={
                    "user_id": sess.user_id,
                    "title": sess.title,
                    "deleted_at": _iso(sess.deleted_at),
                },
            )
            s.delete(sess)
            removed += 1
        return removed


def start_chat_purge_loop() -> threading.Thread | None:
    """Запускает фоновый демон-поток автоочистки истории (однократно).

    Возвращает None, если очистка отключена настройкой chat_history_purge_enabled.
    """
    settings = get_settings()
    if not settings.chat_history_purge_enabled:
        return None

    def _loop() -> None:
        interval = max(60.0, settings.chat_history_purge_interval_seconds)
        while True:
            time.sleep(interval)
            try:
                n = purge_expired_sessions()
                if n:
                    logger.info("Автоочистка истории чата: удалено %d тред(ов)", n)
            except Exception:
                logger.exception("Автоочистка истории чата не удалась")

    thread = threading.Thread(target=_loop, name="chat-history-purge", daemon=True)
    thread.start()
    return thread


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
