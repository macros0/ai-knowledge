"""Fence SQL transactions used by a running durable handler."""
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import select, update

from app.db.models import WorkItem
from .models import LostOwnership

_claim = ContextVar("document_work_claim", default=None)


def current_claim():
    return _claim.get()


@contextmanager
def work_execution(work):
    token = _claim.set(work)
    try:
        yield
    finally:
        _claim.reset(token)


@contextmanager
def document_execution(work):
    spec = work.spec
    if (spec.kind != "document" or spec.payload_version != 1 or spec.doc_id != spec.payload.get("doc_id")
            or spec.doc_id is None or spec.payload.get("action") not in {"process", "resume", "regenerate"}):
        raise ValueError("Invalid document work payload")
    with work_execution(work):
        yield


@contextmanager
def coordinator_session(work):
    """Acquire global admission before the parent Work row, then admit children."""
    from app.db.session import current_session, session_scope
    from .admission import lock_admission
    if current_claim() is not None or current_session() is not None:
        raise ValueError("Coordinator requires a fresh admission transaction")
    with session_scope() as session:
        lock_admission(session)
        with work_execution(work):
            bind_session(session, work)
            yield session


def enforce_session(session):
    active = current_claim()
    bound = session.info.get("document_work_claim")
    if active is not None and bound is not None and (active.work_id, active.owner_id, active.fence) != (bound.work_id, bound.owner_id, bound.fence):
        raise LostOwnership("Nested transaction belongs to a different work attempt")
    work = active or bound
    if work is None or session.info.get("work_guard_busy"):
        return
    session.info["document_work_claim"] = work
    session.info["work_guard_busy"] = True
    try:
        from .maintenance_control import assert_writes_allowed, MaintenanceRefused
        try:
            assert_writes_allowed(session, work.maintenance_epoch)
        except MaintenanceRefused:
            raise LostOwnership("Work maintenance epoch is retired") from None
        from .store import _now, _utc
        with session.no_autoflush:
            if session.get_bind().dialect.name == "sqlite":
                session.execute(update(WorkItem.__table__).where(WorkItem.id == work.work_id).values(fence=WorkItem.fence))
            # Core rows avoid replacing pending ORM completion values during before_flush.
            row = session.execute(select(WorkItem.__table__).where(WorkItem.id == work.work_id).with_for_update()).mappings().one_or_none()
            acknowledged = session.info.get("work_acknowledged")
            if (acknowledged is not None and acknowledged[:3] == (work.work_id, work.fence, row["status"] if row else None)
                    and acknowledged[3] is session.get_transaction()):
                if row and row["fence"] == work.fence and row["status"] in {"succeeded", "failed", "cancelled"}:
                    return
            if (row is None or row["owner_id"] != work.owner_id or row["fence"] != work.fence
                    or row["status"] != "running" or row["cancel_requested"] or row["lease_until"] is None
                    or _utc(row["lease_until"]) <= _now(session) or row["maintenance_epoch"] != work.maintenance_epoch):
                raise LostOwnership("Document execution no longer owns its work")
    finally:
        session.info.pop("work_guard_busy", None)


def note_completion(session, work, outcome):
    active = current_claim() or session.info.get("document_work_claim")
    if active is not None:
        if (active.work_id, active.owner_id, active.fence) != (work.work_id, work.owner_id, work.fence):
            raise LostOwnership("Foreign work completion inside document execution")
        session.info["work_acknowledged"] = (work.work_id, work.fence, outcome, session.get_transaction())


def bind_session(session, work):
    bound = session.info.get("document_work_claim")
    if bound is not None and (bound.work_id, bound.owner_id, bound.fence) != (work.work_id, work.owner_id, work.fence):
        raise LostOwnership("Transaction already belongs to a different work attempt")
    session.info["document_work_claim"] = work
    enforce_session(session)
