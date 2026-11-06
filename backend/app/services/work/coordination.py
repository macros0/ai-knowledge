"""Caller-owned transactions for shared limits and configuration revisions.

PostgreSQL row locks serialize a resource/key, and all expiry uses DB time.
SQLite is the existing single-writer unit/dev fallback only.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import re
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models import (ConfigRevision, ReplicaCapacityResource, ReplicaCapacitySlot,
    ReplicaRateBucket, ReplicaRateEvent)
from .store import _now, _utc


class CoordinationOwnershipLost(Exception):
    pass


@dataclass(frozen=True)
class CapacityLease:
    id: str
    resource: str
    owner_id: str
    fence: int
    expires_at: datetime


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,63}", value):
        raise ValueError("Invalid coordination resource/domain")
    return value


def _integer(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("Invalid coordination bound")
    return value


def _insert(session, model, **values):
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    session.execute(insert(model).values(**values).on_conflict_do_nothing())


def _resource(session, resource):
    # UPDATE also obtains SQLite's single-writer reservation before reading.
    found = session.scalar(update(ReplicaCapacityResource).where(ReplicaCapacityResource.resource == resource)
        .values(resource=ReplicaCapacityResource.resource).returning(ReplicaCapacityResource.resource)
        .execution_options(synchronize_session=False))
    if found is None:
        raise CoordinationOwnershipLost("Capacity resource missing")
    return session.get(ReplicaCapacityResource, resource, populate_existing=True)


def _lease(row):
    return CapacityLease(row.id, row.resource, row.owner_id, row.fence, _utc(row.expires_at))


def lock_capacity_resource(session, *, resource, limit):
    _name(resource)
    _integer(limit, 10000)
    _insert(session, ReplicaCapacityResource, resource=resource, capacity_limit=limit)
    return _resource(session, resource)


def reserve_capacity(session, *, resource, owner_id, limit, ttl_seconds, hold_seconds=None, release_required=False):
    _name(resource)
    _integer(limit, 10000)
    _integer(ttl_seconds, 86400)
    hold_seconds = ttl_seconds if hold_seconds is None else _integer(hold_seconds, 86400)
    if (hold_seconds < ttl_seconds or not isinstance(owner_id, str) or not 1 <= len(owner_id) <= 128
            or type(release_required) is not bool):
        raise ValueError("Invalid capacity ownership/protection")
    policy = lock_capacity_resource(session, resource=resource, limit=limit)
    now = _now(session)
    rows = list(session.scalars(select(ReplicaCapacitySlot).where(ReplicaCapacitySlot.resource == resource)
        .order_by(ReplicaCapacitySlot.slot).execution_options(populate_existing=True)))
    busy = [row for row in rows if row.owner_id is not None and (
        row.release_required or _utc(row.expires_at) > now or _utc(row.protected_until) > now)]
    if policy.capacity_limit != limit:
        if busy:
            raise ValueError("Capacity policy differs between replicas")
        policy.capacity_limit = limit
    if len(busy) >= limit:
        return None
    busy_ids = {row.id for row in busy}
    row = next((row for row in rows if row.id not in busy_ids and row.slot < limit), None)
    if row is None:
        occupied = {row.slot for row in rows}
        slot = next(value for value in range(limit) if value not in occupied)
        row = ReplicaCapacitySlot(id=uuid4().hex, resource=resource, slot=slot, fence=1)
        session.add(row)
    else:
        row.fence += 1
    row.owner_id, row.expires_at = owner_id, now + timedelta(seconds=ttl_seconds)
    row.protected_until = now + timedelta(seconds=hold_seconds)
    row.release_required = release_required
    session.flush()
    return _lease(row)


def _owned(session, lease, *, completed=False, current_identity=False):
    if not isinstance(lease, CapacityLease):
        raise ValueError("Capacity token required")
    _resource(session, lease.resource)
    row = session.get(ReplicaCapacitySlot, lease.id, populate_existing=True)
    now = _now(session)
    if (row is None or row.resource != lease.resource or row.owner_id != lease.owner_id
            or row.fence != lease.fence or row.expires_at is None
            or (not completed and ((not current_identity and _utc(row.expires_at) != lease.expires_at)
                or _utc(row.expires_at) <= now))
            or (completed and not row.release_required)):
        raise CoordinationOwnershipLost("Capacity ownership expired or changed")
    return row, now


def assert_capacity_live(session, lease):
    """Validate current DB expiry; renewal preserves this owner/fence identity."""
    _owned(session, lease, current_identity=True)


def renew_capacity(session, lease, *, ttl_seconds):
    _integer(ttl_seconds, 86400)
    row, now = _owned(session, lease)
    row.expires_at = now + timedelta(seconds=ttl_seconds)
    row.protected_until = max(_utc(row.protected_until), row.expires_at)
    session.flush()
    return _lease(row)


def release_capacity(session, lease):
    row, _ = _owned(session, lease)
    if row.release_required:
        raise ValueError("Provider completion required to release this capacity")
    row.owner_id, row.expires_at, row.protected_until = None, None, None
    session.flush()


def complete_capacity(session, lease):
    """Trusted acknowledgement of completion, never an expiry-based recovery.

    The original id/resource/owner/fence must still match. Expiry/renewal does not
    change that provider call's identity; an expired generic token is rejected.
    """
    row, _ = _owned(session, lease, completed=True)
    row.owner_id, row.expires_at, row.protected_until, row.release_required = None, None, None, False
    session.flush()


def consume_rate(session, *, key, limit, window_seconds, weight=1):
    _integer(limit, 10000)
    _integer(window_seconds, 86400)
    _integer(weight, 10000)
    if not isinstance(key, str) or not 1 <= len(key) <= 512:
        raise ValueError("Invalid rate key")
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    _insert(session, ReplicaRateBucket, key_hash=key_hash, request_limit=limit, window_seconds=window_seconds)
    session.execute(update(ReplicaRateBucket).where(ReplicaRateBucket.key_hash == key_hash)
        .values(key_hash=ReplicaRateBucket.key_hash).execution_options(synchronize_session=False))
    policy = session.get(ReplicaRateBucket, key_hash, populate_existing=True)
    now = _now(session)
    cutoff = now - timedelta(seconds=policy.window_seconds)
    session.execute(delete(ReplicaRateEvent).where(ReplicaRateEvent.key_hash == key_hash,
        ReplicaRateEvent.accepted_at <= cutoff))
    count = session.scalar(select(func.coalesce(func.sum(ReplicaRateEvent.cost), 0))
        .where(ReplicaRateEvent.key_hash == key_hash))
    if (policy.request_limit, policy.window_seconds) != (limit, window_seconds):
        if count:
            raise ValueError("Rate policy differs between replicas")
        policy.request_limit, policy.window_seconds = limit, window_seconds
    if count + weight > limit:
        return False
    session.add(ReplicaRateEvent(id=uuid4().hex, key_hash=key_hash, accepted_at=now, cost=weight))
    session.flush()
    return True


def config_revision(session, domain):
    _name(domain)
    return session.scalar(select(ConfigRevision.revision).where(ConfigRevision.domain == domain)) or 0


def bump_config_revision(session, domain):
    _name(domain)
    _insert(session, ConfigRevision, domain=domain, revision=0)
    return session.scalar(update(ConfigRevision).where(ConfigRevision.domain == domain)
        .values(revision=ConfigRevision.revision + 1).returning(ConfigRevision.revision)
        .execution_options(synchronize_session=False))
