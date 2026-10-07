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
from copy import deepcopy
from dataclasses import dataclass
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from app.config import get_settings
from app.db.models import ChatMessage, ChatSession
from app.db.session import session_scope
from app.services import audit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AttemptRef:
    session_id: str
    message_id: int
    attempt_id: str
    status: str = "incomplete"
    existing: bool = False


def _attempt_message(s, ref: AttemptRef, user):
    sess = s.get(ChatSession, ref.session_id)
    if sess is None or sess.user_id != user.user_id:
        raise ChatOwnershipError("Сессия принадлежит другому пользователю")
    if sess.deleted_at is not None:
        raise ChatSessionDeletedError("Сессия удалена")
    message = s.get(ChatMessage, ref.message_id, with_for_update=True)
    if (message is None or message.session_id != sess.id or
            (message.retrieval_metadata or {}).get("answer_attempt", {}).get("id") != ref.attempt_id):
        raise ChatOwnershipError("Попытка ответа не найдена")
    return message


def begin_attempt(session_id: str | None, user, query: str, attempt_id: str, response_mode: str) -> AttemptRef:
    """Persist one question/placeholder pair before search; no evidence checkpoint."""
    session_id = session_id or str(uuid.uuid4())
    with session_scope() as s:
        sess = s.get(ChatSession, session_id, with_for_update=True)
        if sess is not None:
            if sess.user_id != user.user_id:
                raise ChatOwnershipError("Сессия принадлежит другому пользователю")
            if sess.deleted_at is not None:
                raise ChatSessionDeletedError("Сессия удалена")
            messages = s.execute(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)).scalars().all()
            for message in messages:
                attempt = (message.retrieval_metadata or {}).get("answer_attempt", {})
                if attempt.get("id") == attempt_id:
                    if attempt.get("query") != query or attempt.get("mode") != response_mode:
                        raise ValueError("Attempt ID reused with different request")
                    return AttemptRef(session_id, message.id, attempt_id, attempt.get("status", "incomplete"), True)
                if message.role == "assistant" and attempt.get("status") == "incomplete":
                    message.retrieval_metadata = {**(message.retrieval_metadata or {}),
                        "answer_attempt": {**attempt, "status": "stopped"}}
        else:
            sess = ChatSession(
                id=session_id, user_id=user.user_id,
                username=getattr(user, "username", None), title=query[:1024],
            )
            s.add(sess)
        sess.updated_at = _now()
        s.add(ChatMessage(session_id=session_id, role="user", content=query))
        message = ChatMessage(
            session_id=session_id, role="assistant", content="",
            sources=[], retrieval_metadata={"answer_attempt": {
                "id": attempt_id, "query": query, "mode": response_mode,
                "status": "incomplete",
            }},
        )
        s.add(message)
        s.flush()
        return AttemptRef(session_id, message.id, attempt_id)


def save_attempt_sources(ref: AttemptRef, user, sources: list[dict], *, retrieval_metadata: dict | None = None) -> None:
    with session_scope() as s:
        message = _attempt_message(s, ref, user)
        if message.retrieval_metadata["answer_attempt"]["status"] == "incomplete":
            message.sources = deepcopy(sources)
            if retrieval_metadata is not None:
                message.retrieval_metadata = {
                    **message.retrieval_metadata,
                    **{k: deepcopy(v) for k, v in retrieval_metadata.items() if k != "answer_attempt"},
                }


def finish_attempt(ref: AttemptRef, user, *, status: str, answer: str) -> bool:
    if status not in {"completed", "stopped", "failed"}:
        raise ValueError("Invalid terminal status")
    with session_scope() as s:
        message = _attempt_message(s, ref, user)
        metadata = message.retrieval_metadata or {}
        if metadata.get("answer_attempt", {}).get("status") != "incomplete":
            return False
        message.retrieval_metadata = {**metadata, "answer_attempt": {
            **metadata["answer_attempt"], "status": status,
        }}
        message.content = answer if status == "completed" else ""
        return True


def attempt_status(ref: AttemptRef, user) -> str:
    with session_scope() as s:
        message = _attempt_message(s, ref, user)
        return (message.retrieval_metadata or {})["answer_attempt"]["status"]


def find_attempt(session_id: str, user, attempt_id: str) -> AttemptRef | None:
    with session_scope() as s:
        sess = s.get(ChatSession, session_id)
        if sess is None:
            return None
        if sess.user_id != user.user_id:
            raise ChatOwnershipError("Сессия принадлежит другому пользователю")
        if sess.deleted_at is not None:
            raise ChatSessionDeletedError("Сессия удалена")
        messages = s.execute(select(ChatMessage).where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")).scalars()
        for message in messages:
            metadata = (message.retrieval_metadata or {}).get("answer_attempt", {})
            if metadata.get("id") == attempt_id:
                return AttemptRef(session_id, message.id, attempt_id, metadata.get("status", "incomplete"))
        return None


def read_attempt_sources(session_id: str, user, attempt_id: str):
    ref = find_attempt(session_id, user, attempt_id)
    if ref is None:
        return None
    with session_scope() as s:
        message = _attempt_message(s, ref, user)
        return deepcopy(message.sources or []), deepcopy(message.retrieval_metadata or {})


class ChatOwnershipError(Exception):
    """Попытка обратиться к чужому треду (session_id принадлежит другому user_id)."""


class ChatSessionDeletedError(Exception):
    """Передан session_id уже удалённого (в корзине) треда — повторное использование запрещено."""


def is_valid_session_id(value: str) -> bool:
    """Проверяет, что строка — канонический UUID (session_id, порождаемый бэкендом)."""
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


def check_session_state(session_id: str, user_id: str | None) -> None:
    """Ранняя проверка состояния треда перед тяжёлой работой (Этап 6, 1.1).

    Бросает ChatOwnershipError (чужая сессия) / ChatSessionDeletedError (в корзине),
    чтобы не тратить дорогой LLM-вызов на запрос, чей результат всё равно не
    сохранится. Несуществующий session_id — не ошибка (store_turn создаст сессию).
    """
    with session_scope() as s:
        sess = s.get(ChatSession, session_id)
        if sess is None:
            return
        owner = sess.user_id
        deleted_at = sess.deleted_at
    if user_id is not None and owner != user_id:
        raise ChatOwnershipError("Сессия принадлежит другому пользователю")
    if deleted_at is not None:
        raise ChatSessionDeletedError("Сессия удалена")


def store_turn(
    session_id: str | None,
    user,
    query: str,
    answer: str,
    sources: list[dict] | None = None,
    *,
    retrieval_metadata: dict | None = None,
) -> str:
    """Сохраняет пару сообщений (user/assistant) в тред. Возвращает session_id.

    `session_id` None/пустой → создаётся новая сессия (uuid4) с user_id=user.user_id.
    Иначе — сессия либо принадлежит текущему пользователю (reuse), либо бросает
    ChatOwnershipError. Заголовок треда — первое сообщение пользователя.
    """
    user_id = getattr(user, "user_id", None) or "anonymous"
    username = getattr(user, "username", None) or "anonymous"
    sources = deepcopy(sources or [])
    retrieval_metadata = (
        deepcopy(retrieval_metadata) if retrieval_metadata is not None else None
    )

    with session_scope() as s:
        if session_id:
            sess = s.get(ChatSession, session_id)
            if sess is not None:
                if sess.user_id != user_id:
                    raise ChatOwnershipError("Сессия принадлежит другому пользователю")
                if sess.deleted_at is not None:
                    raise ChatSessionDeletedError("Сессия удалена")
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
                retrieval_metadata=retrieval_metadata,
            )
        )
        return sess.id


def list_sessions(user_id: str, limit: int | None = None, offset: int = 0) -> tuple[list[dict], int]:
    """Активные (не удалённые) треды пользователя, по updated_at desc."""
    with session_scope() as s:
        total = s.execute(
            select(func.count())
            .select_from(ChatSession)
            .where(
                ChatSession.user_id == user_id,
                ChatSession.deleted_at.is_(None),
            )
        ).scalar_one()

        rows = (
            s.execute(
                select(ChatSession)
                .where(
                    ChatSession.user_id == user_id,
                    ChatSession.deleted_at.is_(None),
                )
                .order_by(ChatSession.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        counts = _message_counts(s, [r.id for r in rows])
        return [_session_to_dict(r, counts.get(r.id, 0)) for r in rows], total


def list_recent_turns(
    user_id: str,
    *,
    limit: int = 10,
    before_id: int | None = None,
    exclude_session_id: str | None = None,
) -> dict:
    """Keyset page of own questions, paired up to the next user in the same session.

    Read at most limit+1 question headers, then batch only their message ranges.
    Global message IDs keep pages stable when timestamps tie or new turns arrive.
    """
    with session_scope() as s:
        query = (
            select(ChatMessage.id, ChatMessage.session_id, ChatMessage.created_at)
            .join(ChatSession, ChatMessage.session_id == ChatSession.id)
            .where(
                ChatSession.user_id == user_id,
                ChatSession.deleted_at.is_(None),
                ChatMessage.role == "user",
            )
        )
        if before_id is not None:
            query = query.where(ChatMessage.id < before_id)
        if exclude_session_id is not None:
            query = query.where(ChatSession.id != exclude_session_id)
        questions = s.execute(query.order_by(ChatMessage.id.desc()).limit(limit + 1)).all()
        has_more = len(questions) > limit
        questions = questions[:limit]
        if not questions:
            return {"turns": [], "next_before_id": None}

        # A different session's question cannot terminate this turn. The boundary
        # must also include newer questions outside the requested cursor page.
        next_question = aliased(ChatMessage)
        next_id = (
            select(func.min(next_question.id))
            .where(
                next_question.session_id == ChatMessage.session_id,
                next_question.role == "user",
                next_question.id > ChatMessage.id,
            )
            .correlate(ChatMessage)
            .scalar_subquery()
        )
        ranges = (
            select(ChatMessage.id, ChatMessage.session_id, next_id.label("next_id"))
            .where(ChatMessage.id.in_([question.id for question in questions]))
            .subquery()
        )
        messages = s.execute(
            select(ranges.c.id, ChatMessage)
            .join(ranges, and_(
                ChatMessage.session_id == ranges.c.session_id,
                ChatMessage.id >= ranges.c.id,
                or_(ranges.c.next_id.is_(None), ChatMessage.id < ranges.c.next_id),
            ))
            .join(ChatSession, ChatMessage.session_id == ChatSession.id)
            .where(ChatSession.user_id == user_id, ChatSession.deleted_at.is_(None))
            .order_by(ranges.c.id.asc(), ChatMessage.id.asc())
        ).all()
        turns = {
            question.id: {
                "id": question.id,
                "session_id": question.session_id,
                "created_at": question.created_at,
                "messages": [],
            }
            for question in reversed(questions)
        }
        for question_id, message in messages:
            turns[question_id]["messages"].append({
                "role": message.role,
                "content": message.content,
                "sources": message.sources or [],
                "retrieval_metadata": message.retrieval_metadata,
                "created_at": message.created_at,
            })
        return {
            "turns": [turn for turn in turns.values() if turn["messages"]],
            "next_before_id": questions[-1].id if has_more else None,
        }


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
                    "retrieval_metadata": m.retrieval_metadata,
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

    Порядок (1.1/2.2): сначала физическое удаление + commit, затем — аудит. Это
    гарантирует, что audit-запись не может «врать» об удалении, которого не было.
    Аудит пишется отдельным циклом по каждой сессии с try/except, чтобы сбой одной
    записи не блокировал аудит для остальных уже удалённых сессий батча.
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
        metas = [
            {
                "id": sess.id,
                "user_id": sess.user_id,
                "title": sess.title,
                "deleted_at": sess.deleted_at,
            }
            for sess in rows
        ]
        for sess in rows:
            s.delete(sess)
    # commit выполнен (выход из session_scope). Только теперь — аудит.
    for m in metas:
        try:
            audit.record(
                audit.SystemUser(),
                audit.CHAT_HISTORY_AUTO_DELETE,
                audit.TARGET_CHAT,
                target_id=m["id"],
                old_value={
                    "user_id": m["user_id"],
                    "title": m["title"],
                    "deleted_at": audit.iso_or_str(m["deleted_at"]),
                },
            )
        except Exception:
            logger.warning(
                "Аудит автоочистки истории не записан для %s", m["id"], exc_info=True
            )
    return len(metas)


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
            try:
                n = purge_expired_sessions()
                if n:
                    logger.info("Автоочистка истории чата: удалено %d тред(ов)", n)
            except Exception:
                logger.exception("Автоочистка истории чата не удалась")
            time.sleep(interval)

    thread = threading.Thread(target=_loop, name="chat-history-purge", daemon=True)
    thread.start()
    return thread
