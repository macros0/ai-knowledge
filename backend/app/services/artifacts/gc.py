"""Opt-in bounded GC: provider discovery, SQL quarantine, fenced deletion."""
from dataclasses import dataclass
from datetime import timedelta
import logging
import threading
import time
from uuid import uuid4

from sqlalchemy import delete, or_, select, update

from app.db.models import ArtifactBinding, ArtifactGcState, ArtifactObject, ArtifactOrphan, ArtifactReadLease, CheckpointBinding, SourceCacheHead
from app.db.session import session_scope
from .contract import BlobConflict, BlobIntegrityError, BlobLeaseLost, BlobNotFound
from .inventory import Candidate, location_hash
from .lifecycle import _now, _utc, collect_blob
from .repository import _ref, lock_object

log = logging.getLogger(__name__)
TTL = 120


@dataclass
class GcResult:
    scanned: int = 0
    observed: int = 0
    registered: int = 0
    deleted: int = 0
    failures: int = 0
    budget_skipped: int = 0
    busy: bool = False


def _insert_once(session, model, values):
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise ValueError("Artifact GC requires PostgreSQL or SQLite")
    primary_key = next(iter(model.__table__.primary_key.columns))
    return session.execute(insert(model).values(**values).on_conflict_do_nothing()
                           .returning(primary_key)).scalar_one_or_none() is not None


def _state(session, store_id):
    session.execute(update(ArtifactGcState).where(ArtifactGcState.store_id == store_id)
                    .values(store_id=ArtifactGcState.store_id).execution_options(synchronize_session=False))
    return session.get(ArtifactGcState, store_id, populate_existing=True)


def initialize_gc(settings, *, store=None, adopt_existing=False, explicit=False):
    """Persist the DB token first; a lost DB can never auto-adopt its old store."""
    from . import get_blob_store
    store = store or get_blob_store(settings)
    location = location_hash(store)
    with session_scope() as session:
        created = _insert_once(session, ArtifactGcState, dict(store_id=store.store_id,
            scope_token=uuid4().hex, location_hash=location))
        state = _state(session, store.store_id)
        if state.location_hash != location:
            raise BlobConflict("GC store location changed")
        token = state.scope_token
    store.gc_scope(token, initialize=created or explicit or adopt_existing, adopt_existing=adopt_existing)
    return store


class _Guard:
    def __init__(self, store_id, token, stop):
        self.store_id, self.token, self.stop = store_id, token, stop
        self.renewed = time.monotonic()
        self.deadline = self.renewed + TTL * .8

    def row(self, session):
        state = _state(session, self.store_id)
        now = _now(session)
        if (state is None or state.owner_token != self.token or state.lease_expires_at is None
                or _utc(state.lease_expires_at) <= now):
            raise BlobLeaseLost("GC ownership lost")
        return state, now

    def __call__(self):
        now = time.monotonic()
        if self.stop.is_set() or now >= self.deadline:
            raise BlobLeaseLost("GC stopped or ownership lost")
        if now - self.renewed >= 10:
            with session_scope() as session:
                state, db_now = self.row(session)
                state.lease_expires_at = db_now + timedelta(seconds=TTL)
            self.renewed, self.deadline = now, now + TTL * .8

    def checkpoint(self, field, cursor):
        self()
        with session_scope() as session:
            state, _ = self.row(session)
            setattr(state, field, cursor)


def _observe(store, page, guard, report):
    for item in page.items:
        guard()
        with session_scope() as session:
            guard.row(session)
            row = session.get(ArtifactObject, item.object_id)
            if row is not None and (row.store_id != store.store_id or row.state != "deleted"):
                session.execute(delete(ArtifactOrphan).where(ArtifactOrphan.object_id == item.object_id,
                                                           ArtifactOrphan.store_id == store.store_id))
                continue
            inserted = _insert_once(session, ArtifactOrphan, dict(object_id=item.object_id, store_id=store.store_id,
                size=item.size, fingerprint=item.fingerprint, first_seen_at=_now(session)))
            orphan = session.get(ArtifactOrphan, item.object_id, populate_existing=True)
            if orphan.store_id != store.store_id:
                continue
            if (orphan.fingerprint, orphan.size) != (item.fingerprint, item.size):
                orphan.fingerprint, orphan.size, orphan.first_seen_at = item.fingerprint, item.size, _now(session)
            report.observed += int(inserted)


def _forget(session, store_id, object_id):
    session.execute(delete(ArtifactOrphan).where(ArtifactOrphan.object_id == object_id, ArtifactOrphan.store_id == store_id))


def _promote(store, settings, guard, cursor, remaining, report):
    with session_scope() as session:
        rows = session.execute(select(ArtifactOrphan).where(ArtifactOrphan.store_id == store.store_id,
            ArtifactOrphan.object_id > cursor,
            ArtifactOrphan.first_seen_at <= _now(session) - timedelta(seconds=settings.artifact_gc_grace_seconds))
            .order_by(ArtifactOrphan.object_id).limit(settings.artifact_gc_batch_size)).scalars().all()
        values = [(Candidate(row.object_id, row.store_id, row.size, row.fingerprint), row.first_seen_at) for row in rows]
    for candidate, first_seen in values:
        guard()
        if candidate.size > remaining or candidate.size > settings.artifact_max_bytes:
            report.budget_skipped += 1
            continue
        remaining -= candidate.size
        try:
            with session_scope() as session:
                guard.row(session)
                orphan = session.get(ArtifactOrphan, candidate.object_id)
                if orphan is None or orphan.fingerprint != candidate.fingerprint or orphan.first_seen_at != first_seen:
                    continue
                known = session.get(ArtifactObject, candidate.object_id)
                if known is not None and (known.store_id != store.store_id or known.state != "deleted"):
                    _forget(session, store.store_id, candidate.object_id)
                    continue
                tombstone = _ref(known) if known else None
            if tombstone is not None:
                # Its trusted digest already exists. The deletion adapter verifies
                # all bytes against it; a second inspection pass could starve an
                # object whose size equals the entire per-cycle byte budget.
                if tombstone.size != candidate.size:
                    raise BlobIntegrityError("Deleted artifact reappeared with different bytes")
                if collect_blob(store, tombstone.object_id, grace_seconds=settings.artifact_gc_grace_seconds,
                                guard=guard, reappeared=True):
                    report.deleted += 1
                    with session_scope() as session:
                        _forget(session, store.store_id, tombstone.object_id)
                continue
            ref = store.inspect_candidate(candidate, max_bytes=settings.artifact_max_bytes, guard=guard)
            with session_scope() as session:
                guard.row(session)
                orphan = session.get(ArtifactOrphan, candidate.object_id)
                if orphan is None or orphan.fingerprint != candidate.fingerprint or orphan.first_seen_at != first_seen:
                    continue
                inserted = _insert_once(session, ArtifactObject, {**vars(ref), "created_at": first_seen})
                row = lock_object(session, candidate.object_id)
                if row.store_id != store.store_id:
                    continue
                report.registered += int(inserted)
                reappeared = row.state == "deleted"
                if reappeared and (row.sha256 != ref.sha256 or row.size != ref.size):
                    raise BlobIntegrityError("Deleted artifact reappeared with different bytes")
                # Keep an observation for resurrected provider bytes until their
                # tombstone has been collected again; never make them readable.
                if not reappeared:
                    _forget(session, store.store_id, candidate.object_id)
            # A concurrent lifecycle may have installed a tombstone during
            # inspection. Keep its observation for a one-pass retry next cycle.
        except BlobNotFound:
            with session_scope() as session:
                _forget(session, store.store_id, candidate.object_id)
        except BlobLeaseLost:
            raise
        except Exception as exc:
            report.failures += 1
            log.warning("Artifact GC discovery failed (%s)", type(exc).__name__)
    guard.checkpoint("orphan_cursor", values[-1][0].object_id if len(values) == settings.artifact_gc_batch_size else "")
    return remaining


def _sweep(store, settings, guard, cursor, remaining, report):
    with session_scope() as session:
        now = _now(session)
        protected = [select(model.object_id).where(model.object_id == ArtifactObject.object_id).exists()
                     for model in (ArtifactBinding, CheckpointBinding, SourceCacheHead)]
        protected.append(select(ArtifactReadLease.object_id).where(ArtifactReadLease.object_id == ArtifactObject.object_id,
                                                                  ArtifactReadLease.expires_at > now).exists())
        rows = session.scalars(select(ArtifactObject).where(ArtifactObject.store_id == store.store_id,
            ArtifactObject.object_id > cursor, ArtifactObject.state.in_(["ready", "deleting"]),
            ArtifactObject.created_at <= now - timedelta(seconds=settings.artifact_gc_grace_seconds),
            or_(ArtifactObject.state == "ready", ArtifactObject.delete_expires_at.is_(None), ArtifactObject.delete_expires_at <= now),
            *(~condition for condition in protected)).order_by(ArtifactObject.object_id).limit(settings.artifact_gc_batch_size)).all()
        refs = [_ref(row) for row in rows]
    for ref in refs:
        guard()
        if ref.size > remaining or ref.size > settings.artifact_max_bytes:
            report.budget_skipped += 1
            continue
        remaining -= ref.size
        try:
            if collect_blob(store, ref.object_id, grace_seconds=settings.artifact_gc_grace_seconds, guard=guard):
                report.deleted += 1
        except BlobLeaseLost:
            raise
        except Exception as exc:
            report.failures += 1
            log.warning("Artifact GC deletion will retry (%s)", type(exc).__name__)
    guard.checkpoint("sweep_cursor", refs[-1].object_id if len(refs) == settings.artifact_gc_batch_size else "")


def run_gc_cycle(settings, *, store=None, stop_event=None):
    result = GcResult()
    if not settings.artifact_gc_enabled:
        return result
    stop = stop_event or threading.Event()
    if stop.is_set():
        return result
    store = initialize_gc(settings, store=store)
    token = uuid4().hex
    with session_scope() as session:
        state = _state(session, store.store_id)
        now = _now(session)
        if state.owner_token and state.lease_expires_at and _utc(state.lease_expires_at) > now:
            result.busy = True
            return result
        state.owner_token, state.lease_expires_at = token, now + timedelta(seconds=TTL)
        inventory_cursor, orphan_cursor, sweep_cursor = state.inventory_cursor, state.orphan_cursor, state.sweep_cursor
    guard = _Guard(store.store_id, token, stop)
    try:
        guard()
        page = store.inventory_page(cursor=inventory_cursor, limit=settings.artifact_gc_batch_size)
        result.scanned = len(page.items)
        _observe(store, page, guard, result)
        guard.checkpoint("inventory_cursor", page.cursor)
        remaining = _promote(store, settings, guard, orphan_cursor, settings.artifact_gc_verify_bytes, result)
        _sweep(store, settings, guard, sweep_cursor, remaining, result)
        return result
    finally:
        with session_scope() as session:
            session.execute(update(ArtifactGcState).where(ArtifactGcState.store_id == store.store_id,
                ArtifactGcState.owner_token == token).values(owner_token=None, lease_expires_at=None))


def run_gc_loop(stop_event, settings):
    while not stop_event.is_set():
        try:
            result = run_gc_cycle(settings, stop_event=stop_event)
            if result.scanned or result.deleted or result.failures or result.budget_skipped:
                log.info("Artifact GC: scanned=%d observed=%d registered=%d deleted=%d failed=%d budget_skipped=%d",
                    result.scanned, result.observed, result.registered, result.deleted, result.failures, result.budget_skipped)
        except BlobLeaseLost:
            if stop_event.is_set():
                return
            log.warning("Artifact GC ownership lost; retry deferred")
        except Exception as exc:
            log.warning("Artifact GC cycle deferred (%s)", type(exc).__name__)
        stop_event.wait(settings.artifact_gc_interval_seconds)


class GcWorker:
    def __init__(self, settings):
        self.event = threading.Event()
        self.thread = threading.Thread(target=run_gc_loop, args=(self.event, settings), name="artifact-gc", daemon=True)
        self.thread.start()

    def stop(self):
        self.event.set()
        self.thread.join(timeout=2)


def start_gc_if_enabled(settings):
    return GcWorker(settings) if settings.artifact_gc_enabled else None
