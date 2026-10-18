"""One durable ownership boundary for fast scope workers and cancellation."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Event, Thread

from sqlalchemy import select
from app.db.models import DocumentScopeChange, DocumentUpdateAttempt
from app.db.session import session_scope
from app.services.document_update import DocumentUpdateCancelled
from app.services.generation_store import lock_document_write

LIVE = {"admitted", "processing", "copying", "ready"}


@contextmanager
def scope_write_guard(doc_id, plan_id, owner_token):
    with session_scope() as session:
        if not lock_document_write(session, doc_id, allow_deleted=False):
            raise DocumentUpdateCancelled("Document no longer writable")
        row = session.get(DocumentScopeChange, plan_id, populate_existing=True)
        attempt = session.get(DocumentUpdateAttempt, doc_id, populate_existing=True)
        if not row or row.doc_id != doc_id or row.owner_token != owner_token or row.state not in LIVE or (
            attempt is not None and attempt.cancel_requested
        ):
            raise DocumentUpdateCancelled("Scope worker ownership or cancellation changed")
        row.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)
        yield session


def scope_update_in_progress(doc_id):
    with session_scope() as session:
        return session.scalar(select(DocumentScopeChange.id).where(
            DocumentScopeChange.doc_id == doc_id, DocumentScopeChange.state.in_(LIVE | {"queued"}))) is not None


@contextmanager
def register_update_worker(doc_id, plan_id, owner_token):
    stopped = Event()
    def heartbeat():
        while not stopped.wait(20):
            try:
                with scope_write_guard(doc_id, plan_id, owner_token):
                    pass
            except Exception:
                return
    thread = Thread(target=heartbeat, daemon=True, name="scope-heartbeat")
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1)


def request_update_cancel(doc_id):
    """The existing durable cancel flag is authoritative across processes."""
    with session_scope() as session:
        if not lock_document_write(session, doc_id, allow_deleted=False):
            return False
        attempt = session.get(DocumentUpdateAttempt, doc_id)
        if attempt:
            attempt.cancel_requested = True
            return True
        return False


def finish_update_worker(doc_id, plan_id, owner_token):
    with session_scope() as session:
        lock_document_write(session, doc_id)
        row = session.get(DocumentScopeChange, plan_id)
        if row and row.owner_token == owner_token:
            row.owner_token = row.lease_expires_at = None
