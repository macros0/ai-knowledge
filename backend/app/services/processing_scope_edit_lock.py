"""Document-level edit leases, distinct from durable operation ownership.

Every mutation checks its lease while holding the document write lock. No SQL
transaction spans parsing/provider calls or the lifetime of an open dialog.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from sqlalchemy import select

from app.db.models import (Document, DocumentGenerationState, DocumentProcessingReview,
                           DocumentScopeChange, DocumentScopeEditLock)
from app.services.generation_store import lock_document_write
from app.services.processing_scope import ProcessingScopeConflict

LEASE_SECONDS = 180
IDLE_SECONDS = 600
OPERATION_STATES = {"admitted", "queued", "processing", "copying", "ready"}


def _now():
    return datetime.now(timezone.utc)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest() if token else ""


def _live(row):
    return bool(row and _utc(row.expires_at) > _now() and _utc(row.idle_expires_at) > _now())


def _operation_active(session, doc_id):
    state = session.get(DocumentGenerationState, doc_id)
    review = session.get(DocumentProcessingReview, doc_id)
    document = session.get(Document, doc_id)
    return bool((state and state.candidate_generation_id)
        or (review and review.state in {"validating", "submitted"})
        or (document and document.status in {"uploaded", "queued", "processing", "splitting", "indexing"})
        or session.scalar(select(DocumentScopeChange.id).where(
            DocumentScopeChange.doc_id == doc_id, DocumentScopeChange.state.in_(OPERATION_STATES)).limit(1)))


def edit_lock_status(session, doc_id):
    if _operation_active(session, doc_id):
        return {"state": "applying", "owner": None, "expires_at": None}
    row = session.get(DocumentScopeEditLock, doc_id)
    if not _live(row):
        return {"state": "available", "owner": None, "expires_at": None}
    return {"state": "editing", "owner": row.owner_name, "expires_at": _utc(row.expires_at).isoformat(),
            "idle_expires_at": _utc(row.idle_expires_at).isoformat(),
            "idle_remaining_seconds": max(0, int((_utc(row.idle_expires_at) - _now()).total_seconds()))}


def _lock(session, doc_id):
    if not lock_document_write(session, doc_id, allow_deleted=False):
        raise ProcessingScopeConflict("Документ недоступен.")


def _matches(row, owner_id, token):
    return bool(row and token and row.owner_id == owner_id
                and secrets.compare_digest(row.token_hash, _hash(token)))


def _check_operation(session, doc_id):
    if _operation_active(session, doc_id):
        raise ProcessingScopeConflict("Изменение состава уже выполняется.", code="scope_change_in_progress")


def acquire_edit_lock(session, doc_id, owner_id, owner_name, token):
    _lock(session, doc_id)
    from app.config import get_settings
    from app.services.processing_scope import recover_review_admission
    recover_review_admission(session, doc_id, get_settings())
    _check_operation(session, doc_id)
    if not token or len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        raise ProcessingScopeConflict("Откройте состав повторно.", code="scope_edit_lock_lost")
    row = session.get(DocumentScopeEditLock, doc_id, populate_existing=True)
    if _live(row):
        if not _matches(row, owner_id, token):
            raise ProcessingScopeConflict("Состав уже редактирует другой пользователь или другая вкладка.", code="scope_edit_locked")
        return edit_lock_status(session, doc_id)
    if not row:
        row = DocumentScopeEditLock(doc_id=doc_id)
        session.add(row)
    row.owner_id, row.owner_name, row.token_hash = owner_id, owner_name, _hash(token)
    row.expires_at = _now() + timedelta(seconds=LEASE_SECONDS)
    row.idle_expires_at = _now() + timedelta(seconds=IDLE_SECONDS)
    row.activity_sequence = 0
    session.flush()
    return edit_lock_status(session, doc_id)


def require_edit_lock(session, doc_id, owner_id, token, *, record_activity=True):
    _lock(session, doc_id)
    _check_operation(session, doc_id)
    row = session.get(DocumentScopeEditLock, doc_id, populate_existing=True)
    if not _matches(row, owner_id, token) or not _live(row):
        raise ProcessingScopeConflict("Блокировка редактора истекла. Обновите состав.", code="scope_edit_lock_lost")
    # A bounded mutation also renews the lease; it cannot resurrect an expired tab.
    if record_activity:
        row.idle_expires_at = _now() + timedelta(seconds=IDLE_SECONDS)
    row.expires_at = min(_now() + timedelta(seconds=LEASE_SECONDS), _utc(row.idle_expires_at))
    return row


def renew_edit_lock(session, doc_id, owner_id, token, *, activity_sequence=0):
    row = require_edit_lock(session, doc_id, owner_id, token, record_activity=False)
    if activity_sequence > row.activity_sequence:
        row.activity_sequence = activity_sequence
        row.idle_expires_at = _now() + timedelta(seconds=IDLE_SECONDS)
        row.expires_at = _now() + timedelta(seconds=LEASE_SECONDS)
    return edit_lock_status(session, doc_id)


def release_edit_lock(session, doc_id, owner_id, token):
    _lock(session, doc_id)
    row = session.get(DocumentScopeEditLock, doc_id, populate_existing=True)
    if _matches(row, owner_id, token):
        session.delete(row)
