"""Caller-owned transactions; PostgreSQL locks and DB time decide ownership."""
from datetime import datetime, timedelta, timezone
import json
import re
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models import WorkItem
from .models import LostOwnership, WorkClaim, WorkSpec


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _now(session):
    expression = func.clock_timestamp() if session.get_bind().dialect.name == "postgresql" else func.strftime("%Y-%m-%d %H:%M:%f", "now")
    value = session.scalar(select(expression))
    return _utc(datetime.fromisoformat(value) if isinstance(value, str) else value)


def _duration(value):
    if type(value) is not int or not 1 <= value <= 3600:
        raise ValueError("Invalid work lease duration")
    return value


def _spec(row):
    return WorkSpec(row.kind, row.payload_version, row.payload_json, row.dedupe_key, row.doc_id)


def _claim(row):
    return WorkClaim(row.id, row.owner_id, row.fence, _utc(row.lease_until), _spec(row))


def enqueue(session, spec: WorkSpec) -> str:
    # Revalidate because a caller may have modified a nested DTO payload.
    spec = WorkSpec(spec.kind, spec.payload_version, spec.payload, spec.dedupe_key, spec.doc_id)
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    now = _now(session)
    session.execute(insert(WorkItem).values(id=uuid4().hex, kind=spec.kind, payload_version=spec.payload_version,
        payload_json=spec.payload, dedupe_key=spec.dedupe_key, doc_id=spec.doc_id,
        status="queued", owner_id=None, fence=0, attempts=0, cancel_requested=False, available_at=now, created_at=now)
        .on_conflict_do_nothing(index_elements=[WorkItem.dedupe_key]))
    row = session.scalar(select(WorkItem).where(WorkItem.dedupe_key == spec.dedupe_key).execution_options(populate_existing=True))
    if (row is None or (row.kind, row.payload_version, row.doc_id) != (spec.kind, spec.payload_version, spec.doc_id)
            or json.dumps(row.payload_json, sort_keys=True, separators=(",", ":")) != json.dumps(spec.payload, sort_keys=True, separators=(",", ":"))):
        raise ValueError("Work dedupe key conflicts with a different request")
    return row.id


def claim(session, *, owner_id: str, supported: dict[str, set[int]], lease_seconds: int) -> WorkClaim | None:
    seconds = _duration(lease_seconds)
    if not isinstance(owner_id, str) or not 1 <= len(owner_id) <= 128:
        raise ValueError("Invalid worker identity")
    versions = [and_(WorkItem.kind == kind, WorkItem.payload_version.in_(values)) for kind, values in supported.items() if values]
    now = _now(session)
    # Bounded recovery of cancelled attempts whose owner can no longer ACK.
    cancelled = list(session.scalars(select(WorkItem.id).where(WorkItem.status == "running", WorkItem.cancel_requested,
        WorkItem.lease_until <= now).order_by(WorkItem.lease_until, WorkItem.id).limit(100).with_for_update(skip_locked=True)))
    if cancelled:
        session.execute(update(WorkItem).where(WorkItem.id.in_(cancelled), WorkItem.status == "running", WorkItem.cancel_requested,
            WorkItem.lease_until <= now).values(status="cancelled", finished_at=now, owner_id=None, lease_until=None)
            .execution_options(synchronize_session=False))
    compatible = or_(*versions) if versions else WorkItem.id.is_(None)
    unsupported = list(session.scalars(select(WorkItem.id).where(WorkItem.status == "queued", ~compatible,
        or_(WorkItem.error_code.is_(None), WorkItem.error_code != "unsupported_payload"))
        .order_by(WorkItem.created_at, WorkItem.id).limit(100).with_for_update(skip_locked=True)))
    if unsupported:
        session.execute(update(WorkItem).where(WorkItem.id.in_(unsupported), WorkItem.status == "queued")
            .values(error_code="unsupported_payload").execution_options(synchronize_session=False))
    if not versions:
        return None
    eligible = and_(~WorkItem.cancel_requested, WorkItem.available_at <= now, or_(
        WorkItem.status == "queued", and_(WorkItem.status == "running", WorkItem.lease_until <= now)))
    row = session.scalar(select(WorkItem).where(eligible, compatible)
        .order_by(WorkItem.available_at, WorkItem.created_at, WorkItem.id).limit(1)
        .with_for_update(skip_locked=True).execution_options(populate_existing=True))
    if row is None:
        return None
    # Conditional update is also the serialized single-writer SQLite fallback.
    token = session.scalar(update(WorkItem).where(WorkItem.id == row.id, WorkItem.fence == row.fence, eligible)
        .values(status="running", owner_id=owner_id, fence=WorkItem.fence + 1, attempts=WorkItem.attempts + 1,
                lease_until=now + timedelta(seconds=seconds), error_code=None)
        .returning(WorkItem.id).execution_options(synchronize_session=False))
    if token is None:
        return None
    session.refresh(row)
    return _claim(row)


def _locked(session, work, *, allow_cancel=False):
    if session.get_bind().dialect.name == "sqlite":
        session.execute(update(WorkItem).where(WorkItem.id == work.work_id).values(fence=WorkItem.fence)
                        .execution_options(synchronize_session=False))
    row = session.scalar(select(WorkItem).where(WorkItem.id == work.work_id)
                         .with_for_update().execution_options(populate_existing=True))
    now = _now(session)
    if (row is None or row.status != "running" or row.owner_id != work.owner_id or row.fence != work.fence
            or row.lease_until is None or _utc(row.lease_until) <= now or work.maintenance_epoch != 0
            or row.cancel_requested and not allow_cancel):
        raise LostOwnership("Work ownership expired, changed or cancelled")
    return row, now


def assert_owner(session, work: WorkClaim) -> None:
    _locked(session, work)


def renew(session, work: WorkClaim, *, lease_seconds: int) -> WorkClaim:
    seconds = _duration(lease_seconds)
    row, now = _locked(session, work)
    row.lease_until = now + timedelta(seconds=seconds)
    session.flush()
    return _claim(row)


def finish(session, work: WorkClaim, *, outcome: str, error_code: str | None = None) -> None:
    if outcome not in {"succeeded", "failed", "cancelled"} or error_code is not None and not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code):
        raise ValueError("Invalid work outcome/error code")
    row, now = _locked(session, work, allow_cancel=outcome == "cancelled")
    row.status, row.finished_at, row.error_code = outcome, now, error_code
    row.owner_id, row.lease_until = None, None
    session.flush()


def cancel(session, work_id: str) -> bool:
    if session.get_bind().dialect.name == "sqlite":
        session.execute(update(WorkItem).where(WorkItem.id == work_id).values(fence=WorkItem.fence)
                        .execution_options(synchronize_session=False))
    row = session.scalar(select(WorkItem).where(WorkItem.id == work_id).with_for_update().execution_options(populate_existing=True))
    if row is None or row.status not in {"queued", "running"}:
        return False
    row.cancel_requested = True
    if row.status == "queued" or row.lease_until is None or _utc(row.lease_until) <= _now(session):
        row.status, row.finished_at, row.owner_id, row.lease_until = "cancelled", _now(session), None, None
    session.flush()
    return True


def retry(session, work: WorkClaim, *, error_code: str, delay_seconds: int, max_attempts: int) -> None:
    if (not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code) or type(delay_seconds) is not int
            or not 1 <= delay_seconds <= 3600 or type(max_attempts) is not int or not 1 <= max_attempts <= 10):
        raise ValueError("Invalid bounded work retry")
    row, now = _locked(session, work)
    if row.attempts >= max_attempts:
        row.status, row.finished_at = "failed", now
    else:
        row.status, row.available_at = "queued", now + timedelta(seconds=delay_seconds)
    row.error_code, row.owner_id, row.lease_until = error_code, None, None
    session.flush()
