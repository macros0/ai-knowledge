"""Durable request ownership without automatically replaying interrupted chats."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import select

from app.db.models import ChatRun, ChatMessage, ChatSession, ReplicaCapacityResource, ReplicaCapacitySlot
from app.services import chat_history
from .coordination import (lock_capacity_resource, reserve_capacity, release_capacity,
    renew_capacity, CapacityLease, CoordinationOwnershipLost)
from .store import _now, _utc


class ChatRunConflict(Exception):
    pass


class ChatRunOwnershipLost(Exception):
    pass


class ChatCapacityExceeded(Exception):
    pass


@dataclass(frozen=True)
class ChatClaim:
    run_id: str
    owner_id: str
    fence: int
    lease_until: datetime
    ref: chat_history.AttemptRef


def _claim(row):
    ref = chat_history.AttemptRef(row.session_id, row.message_id, row.attempt_id,
        run_id=row.id, run_owner=row.owner_id, run_fence=row.fence)
    return ChatClaim(row.id, row.owner_id, row.fence, _utc(row.lease_until), ref)


def _resource_lock(session):
    policy = session.get(ReplicaCapacityResource, "chat")
    if policy is None:
        raise ChatRunOwnershipLost("Chat capacity missing")
    lock_capacity_resource(session, resource="chat", limit=policy.capacity_limit)


def _slot_token(session, row):
    slot = session.get(ReplicaCapacitySlot, row.capacity_id, populate_existing=True)
    if (slot is None or slot.resource != "chat" or slot.owner_id != row.id
            or slot.fence != row.capacity_fence or slot.expires_at is None):
        raise ChatRunOwnershipLost("Chat capacity ownership changed")
    return CapacityLease(slot.id, slot.resource, slot.owner_id, slot.fence, _utc(slot.expires_at))


def _release(session, row):
    try:
        token = _slot_token(session, row)
        if token.expires_at > _now(session):
            release_capacity(session, token)
    except (ChatRunOwnershipLost, CoordinationOwnershipLost):
        pass  # An expired/reallocated generic slot must never be cleared.


def _message(session, row, user):
    if row.actor_id != user.user_id:
        raise chat_history.ChatOwnershipError("Сессия принадлежит другому пользователю")
    return chat_history._attempt_message(session, _claim(row).ref, user, _lock_session=True)


def _finish_message(message, status, answer):
    metadata = message.retrieval_metadata or {}
    attempt = metadata.get("answer_attempt", {})
    if attempt.get("status") != "incomplete":
        raise ChatRunOwnershipLost("Chat history already terminal")
    message.content = answer if status == "completed" else ""
    message.retrieval_metadata = {**metadata, "answer_attempt": {**attempt, "status": status}}


def _locked(session, claim):
    _resource_lock(session)
    row = session.scalar(select(ChatRun).where(ChatRun.id == claim.run_id)
        .with_for_update().execution_options(populate_existing=True))
    now = _now(session)
    if (row is None or row.owner_id != claim.owner_id or row.fence != claim.fence
            or row.status != "running" or row.cancel_requested or _utc(row.lease_until) <= now):
        raise ChatRunOwnershipLost("Chat request ownership expired or changed")
    if _slot_token(session, row).expires_at <= now:
        raise ChatRunOwnershipLost("Chat capacity expired")
    thread = session.get(ChatSession, row.session_id, with_for_update=True, populate_existing=True)
    if thread is None or thread.user_id != row.actor_id or thread.deleted_at is not None:
        raise ChatRunOwnershipLost("Chat history no longer writable")
    now = _now(session)
    if _utc(row.lease_until) <= now or _slot_token(session, row).expires_at <= now:
        raise ChatRunOwnershipLost("Chat request expired during lock acquisition")
    return row, now


def admit_chat_run(session, settings, request, user, *, owner_id):
    if not isinstance(owner_id, str) or not 1 <= len(owner_id) <= 128:
        raise ValueError("Invalid chat owner")
    attempt_id = str(UUID(request.attempt_id)) if request.attempt_id else str(uuid4())
    accepted = request.model_dump(mode="json")
    accepted["attempt_id"] = attempt_id
    digest = hashlib.sha256(json.dumps(accepted, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    # Acquire the global row before the savepoint. Besides lock order this
    # starts SQLite's real write transaction: a first SAVEPOINT would otherwise
    # be committed by RELEASE independently of the caller's later rollback.
    lock_capacity_resource(session, resource="chat", limit=settings.chat_max_inflight)
    with session.begin_nested():
        prior = session.scalar(select(ChatRun).where(ChatRun.actor_id == user.user_id,
            ChatRun.attempt_id == attempt_id).with_for_update())
        if prior is not None:
            if prior.request_hash != digest:
                raise ValueError("Chat attempt reused with different input")
            raise ChatRunConflict("Chat attempt already accepted")
        if request.session_id:
            thread = session.get(ChatSession, request.session_id)
            if thread is not None:
                if thread.user_id != user.user_id:
                    raise chat_history.ChatOwnershipError("Сессия принадлежит другому пользователю")
                if thread.deleted_at is not None:
                    raise chat_history.ChatSessionDeletedError("Сессия удалена")
            previous = list(session.scalars(select(ChatRun).where(ChatRun.session_id == request.session_id,
                ChatRun.actor_id == user.user_id, ChatRun.status == "running")
                .order_by(ChatRun.id).with_for_update().execution_options(populate_existing=True)))
            for old in previous:
                _finish_message(_message(session, old, user), "stopped", "")
                old.cancel_requested, old.status, old.finished_at = True, "stopped", _now(session)
                _release(session, old)
        run_id, ttl = uuid4().hex, 30
        capacity = reserve_capacity(session, resource="chat", owner_id=run_id,
            limit=settings.chat_max_inflight, ttl_seconds=ttl)
        if capacity is None:
            raise ChatCapacityExceeded("Shared chat capacity exhausted")
        ref = chat_history.begin_attempt(request.session_id, user, request.query, attempt_id,
            request.response_mode, scenario_id=str(request.scenario_id) if request.scenario_id else None,
            _session=session)
        if ref.existing:
            raise ChatRunConflict("Legacy chat attempt already accepted")
        now = _now(session)
        row = ChatRun(id=run_id, actor_id=user.user_id, attempt_id=attempt_id, request_hash=digest,
            session_id=ref.session_id, message_id=ref.message_id, owner_id=owner_id, fence=1,
            lease_until=capacity.expires_at, cancel_requested=False, status="running",
            capacity_id=capacity.id, capacity_fence=capacity.fence, created_at=now)
        session.add(row)
        session.flush()
        return _claim(row)


def renew_chat_run(session, claim):
    row, now = _locked(session, claim)
    try:
        capacity = renew_capacity(session, _slot_token(session, row), ttl_seconds=30)
    except CoordinationOwnershipLost as exc:
        raise ChatRunOwnershipLost("Chat capacity expired") from exc
    row.lease_until = capacity.expires_at
    session.flush()
    return _claim(row)


def finish_chat_run(session, claim, user, *, status, answer):
    if status not in {"completed", "stopped", "failed"}:
        raise ValueError("Invalid chat outcome")
    row, now = _locked(session, claim)
    message = _message(session, row, user)
    row, now = _locked(session, claim)  # Recheck after the history row lock.
    _finish_message(message, status, answer)
    row.status, row.finished_at = status, now
    _release(session, row)
    session.flush()


def cancel_chat_run(session, *, session_id, attempt_id, user):
    if session.scalar(select(ChatRun.id).where(ChatRun.actor_id == user.user_id,
            ChatRun.session_id == session_id, ChatRun.attempt_id == str(UUID(attempt_id)))) is None:
        return False
    _resource_lock(session)
    row = session.scalar(select(ChatRun).where(ChatRun.actor_id == user.user_id,
        ChatRun.session_id == session_id, ChatRun.attempt_id == str(UUID(attempt_id)))
        .with_for_update().execution_options(populate_existing=True))
    if row is None:
        return False
    message = _message(session, row, user)
    if row.status == "running":
        _finish_message(message, "stopped", "")
        row.cancel_requested, row.status, row.finished_at = True, "stopped", _now(session)
        _release(session, row)
        session.flush()
    return True


def _run_for_ref(session, ref, user):
    row = session.scalar(select(ChatRun).where(ChatRun.message_id == ref.message_id))
    if row is None:
        if ref.run_id is not None:
            raise ChatRunOwnershipLost("Managed chat history no longer exists")
        return None
    if (ref.run_id != row.id or ref.run_owner != row.owner_id or ref.run_fence != row.fence
            or ref.session_id != row.session_id or ref.attempt_id != row.attempt_id or row.actor_id != user.user_id):
        raise ChatRunOwnershipLost("Managed history requires the matching request owner")
    return row


def guard_history_write(session, ref, user, *, terminal_draft=False):
    row = _run_for_ref(session, ref, user)
    if row is None:
        return
    if not terminal_draft:
        _locked(session, _claim(row))
        return
    _resource_lock(session)
    row = session.scalar(select(ChatRun).where(ChatRun.id == ref.run_id)
        .with_for_update().execution_options(populate_existing=True))
    if (row is None or row.owner_id != ref.run_owner or row.fence != ref.run_fence
            or row.status not in {"stopped", "failed"}):
        raise ChatRunOwnershipLost("Only the terminal owner may retain its partial draft")


def finish_managed_attempt(session, ref, user, *, status, answer):
    row = _run_for_ref(session, ref, user)
    if row is None:
        return None
    try:
        finish_chat_run(session, _claim(row), user, status=status, answer=answer)
    except ChatRunOwnershipLost:
        return False
    return True


def lock_chat_history_purge(session):
    """Keep Run→Session order when ORM deletion cascades back to run rows."""
    if session.get(ReplicaCapacityResource, "chat") is not None:
        _resource_lock(session)


def cancel_chat_session(session, session_id, user):
    thread = session.get(ChatSession, session_id)
    if thread is None:
        return
    actor_id = getattr(user, "user_id", None) or "anonymous"
    if thread.user_id != actor_id:
        raise chat_history.ChatOwnershipError("Сессия принадлежит другому пользователю")
    lock_chat_history_purge(session)
    rows = list(session.scalars(select(ChatRun).where(ChatRun.session_id == session_id,
        ChatRun.actor_id == actor_id, ChatRun.status == "running")
        .order_by(ChatRun.id).with_for_update().execution_options(populate_existing=True)))
    for row in rows:
        message = session.get(ChatMessage, row.message_id, with_for_update=True)
        if (message is None or message.session_id != session_id
                or (message.retrieval_metadata or {}).get("answer_attempt", {}).get("id") != row.attempt_id):
            raise ChatRunOwnershipLost("Managed history identity changed")
        _finish_message(message, "stopped", "")
        row.cancel_requested, row.status, row.finished_at = True, "stopped", _now(session)
        _release(session, row)


def reconcile_chat_runs(work):
    from app.db.session import session_scope
    from .context import work_execution
    from .handlers import WorkDeferred
    if work.spec.doc_id is not None or work.spec.payload:
        raise ValueError("Invalid chat reconciliation payload")
    with work_execution(work), session_scope() as session:
        if session.get(ReplicaCapacityResource, "chat") is None:
            return
        _resource_lock(session)
        now = _now(session)
        # The eligibility predicate is the durable cursor: each accepted page
        # makes its rows terminal in the same transaction. No LLM replay.
        rows = list(session.scalars(select(ChatRun).where(ChatRun.status == "running",
            ChatRun.lease_until <= now).order_by(ChatRun.lease_until, ChatRun.id)
            .limit(25).with_for_update().execution_options(populate_existing=True)))
        for row in rows:
            thread = session.get(ChatSession, row.session_id, with_for_update=True)
            message = session.get(ChatMessage, row.message_id, with_for_update=True)
            if (thread is None or thread.user_id != row.actor_id or message is None
                    or message.session_id != row.session_id
                    or (message.retrieval_metadata or {}).get("answer_attempt", {}).get("id") != row.attempt_id):
                raise ChatRunOwnershipLost("Managed history identity changed")
            if message.retrieval_metadata["answer_attempt"].get("status") == "incomplete":
                _finish_message(message, "stopped", "")
            row.cancel_requested, row.status, row.finished_at = True, "interrupted", now
            _release(session, row)
    return WorkDeferred(1) if len(rows) == 25 else None
