"""Short SQL transactions serialize reference/lease creation with orphan GC."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from uuid import uuid4

from sqlalchemy import delete, func, select

from app.db.models import ArtifactBinding, ArtifactReadLease, CheckpointBinding
from app.db.session import session_scope
from .contract import BlobLeaseLost, BlobRef
from .repository import _ref, lock_object


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _now(session):
    expression = (func.clock_timestamp() if session.get_bind().dialect.name == "postgresql"
                  else func.strftime("%Y-%m-%d %H:%M:%f", "now"))
    value = session.scalar(select(expression))
    return _utc(datetime.fromisoformat(value) if isinstance(value, str) else value)


def _seconds(value, *, minimum=1, maximum=300):
    if type(value) not in (float, int) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("Invalid artifact lease/grace duration")
    return value


def acquire_read_lease(session, ref, *, ttl_seconds=60):
    ttl_seconds = _seconds(ttl_seconds)
    row = lock_object(session, ref.object_id)
    if row is None or row.state != "ready" or _ref(row) != ref:
        raise BlobLeaseLost("Artifact cannot be leased")
    token = uuid4().hex
    session.add(ArtifactReadLease(object_id=ref.object_id, owner_token=token,
                                 expires_at=_now(session) + timedelta(seconds=ttl_seconds)))
    session.flush()
    return token


def renew_read_lease(session, object_id, token, *, ttl_seconds=60):
    ttl_seconds = _seconds(ttl_seconds)
    row = lock_object(session, object_id)
    lease = session.get(ArtifactReadLease, (object_id, token), populate_existing=True)
    now = _now(session)
    if row is None or row.state != "ready" or lease is None or _utc(lease.expires_at) <= now:
        raise BlobLeaseLost("Artifact read lease lost")
    lease.expires_at = now + timedelta(seconds=ttl_seconds)
    session.flush()


def release_read_lease(session, object_id, token):
    session.execute(delete(ArtifactReadLease).where(ArtifactReadLease.object_id == object_id,
                                                  ArtifactReadLease.owner_token == token))


@dataclass(frozen=True)
class DeleteClaim:
    ref: BlobRef
    token: str


def claim_delete(session, object_id, *, grace_seconds=86400, ttl_seconds=60, store_id=None):
    grace_seconds = _seconds(grace_seconds, minimum=0, maximum=365 * 86400)
    ttl_seconds = _seconds(ttl_seconds)
    row = lock_object(session, object_id)
    if row is None or row.state == "deleted":
        return None
    if store_id is not None and row.store_id != store_id:
        raise ValueError("Foreign artifact GC store")
    now = _now(session)
    if row.state == "deleting" and row.delete_expires_at is not None and _utc(row.delete_expires_at) > now:
        return None
    # Never detach bindings or decide owner retention here. Those operations
    # require the document/job lifecycle lock in their own owning transaction.
    if session.scalar(select(ArtifactBinding.object_id).where(ArtifactBinding.object_id == object_id).limit(1)):
        return None
    if session.scalar(select(CheckpointBinding.object_id).where(CheckpointBinding.object_id == object_id).limit(1)):
        return None
    if session.scalar(select(ArtifactReadLease.object_id).where(ArtifactReadLease.object_id == object_id,
                                                               ArtifactReadLease.expires_at > now).limit(1)):
        return None
    if _utc(row.created_at) + timedelta(seconds=grace_seconds) > now:
        return None
    session.execute(delete(ArtifactReadLease).where(ArtifactReadLease.object_id == object_id,
                                                  ArtifactReadLease.expires_at <= now))
    row.state = "deleting"
    row.delete_token = uuid4().hex
    row.delete_expires_at = now + timedelta(seconds=ttl_seconds)
    session.flush()
    return DeleteClaim(_ref(row), row.delete_token)


def finish_delete(session, claim):
    row = lock_object(session, claim.ref.object_id)
    if row is None or row.state != "deleting" or row.delete_token != claim.token:
        return False
    row.state = "deleted"
    row.delete_token = None
    row.delete_expires_at = None
    session.flush()
    return True


def collect_blob(store, object_id, *, grace_seconds=86400):
    with session_scope() as session:
        claim = claim_delete(session, object_id, grace_seconds=grace_seconds, store_id=store.store_id)
    if claim is None:
        return False
    store.delete(claim.ref)
    with session_scope() as session:
        return finish_delete(session, claim)
