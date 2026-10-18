"""User/tab reference leases, independent from background job ownership."""
from datetime import datetime, timezone
import hashlib
import re
import secrets

from app.db.models import ReferenceResourceState
from app.services import audit
from app.services.edit_lease_policy import (LEASE_SECONDS, IDLE_SECONDS, HEARTBEAT_SECONDS, WARNING_SECONDS,
                                           compute_deadlines, utc)
from app.services.reference_concurrency import ReferenceError, lock_reference_resources
from app.services.reference_resources import LEASE_TYPES, require_access, require_resource


def utc_now():
    return datetime.now(timezone.utc)


def live(row, now=None):
    now = now or utc_now()
    return bool(row and row.owner_id and row.expires_at and row.idle_expires_at and
                utc(row.expires_at) > now and utc(row.idle_expires_at) > now)


def _token_hash(token):
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ReferenceError("reference_precondition_required", 428)
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _matches(row, actor, token, epoch=None):
    return bool(row and row.owner_id == actor.user_id and row.token_hash and
        secrets.compare_digest(row.token_hash, _token_hash(token)) and
        (epoch is None or row.lease_epoch == epoch))


def _event(session, key, actor, action, row, **meta):
    audit.record_in_session(session, action_type=action, user_id=actor.user_id, username=actor.username,
        target_type="reference", target_id=key.canonical,
        meta={"lease_epoch": row.lease_epoch, **meta})


def _clear(row):
    row.owner_id = row.owner_name = row.token_hash = None
    row.acquired_at = row.expires_at = row.idle_expires_at = None
    row.activity_sequence = 0


def invalidate_reference_lease(row):
    row.lease_epoch += 1
    _clear(row)


def edit_lock_status(session, key):
    from app.config import get_settings
    row = session.get(ReferenceResourceState, key.canonical)
    now = utc_now()
    active = live(row, now)
    return {"state": "editing" if active else "available", "owner_name": row.owner_name if active else None,
        "server_now": now.isoformat(), "expires_at": utc(row.expires_at).isoformat() if active else None,
        "idle_expires_at": utc(row.idle_expires_at).isoformat() if active else None,
        "lease_seconds": LEASE_SECONDS, "idle_seconds": IDLE_SECONDS,
        "heartbeat_seconds": HEARTBEAT_SECONDS, "warning_seconds": WARNING_SECONDS,
        "reference_revision": row.revision if row else 1, "lease_epoch": row.lease_epoch if row else 0,
        "lease_required": key.resource_type in LEASE_TYPES and get_settings().reference_edit_locks_enabled}


def acquire_edit_lock(session, key, actor, token):
    require_access(key, actor)
    require_resource(session, key)
    if key.resource_type not in LEASE_TYPES:
        raise ReferenceError("reference_precondition_required", 428)
    digest = _token_hash(token)
    row = lock_reference_resources(session, [key])[key]
    require_resource(session, key)
    now = utc_now()
    if live(row, now):
        if not _matches(row, actor, token):
            raise ReferenceError("reference_edit_locked", **edit_lock_status(session, key))
        return edit_lock_status(session, key)
    row.lease_epoch += 1
    row.owner_id, row.owner_name, row.token_hash = actor.user_id, actor.username, digest
    row.acquired_at, row.activity_sequence = now, 0
    row.expires_at, row.idle_expires_at = compute_deadlines(now, now, record_activity=True)
    _event(session, key, actor, audit.REFERENCE_EDIT_ACQUIRED, row)
    return edit_lock_status(session, key)


def require_edit_lock(session, key, actor, token, epoch, *, record_activity=False):
    require_access(key, actor)
    row = lock_reference_resources(session, [key])[key]
    require_resource(session, key)
    now = utc_now()
    if type(epoch) is not int or not live(row, now) or not _matches(row, actor, token, epoch):
        raise ReferenceError("reference_edit_lock_lost")
    if record_activity:
        row.expires_at, row.idle_expires_at = compute_deadlines(now, row.idle_expires_at, record_activity=True)
    return row


def renew_edit_lock(session, key, actor, token, epoch, activity_sequence):
    if type(activity_sequence) is not int or not 0 <= activity_sequence <= 2**63 - 1:
        raise ReferenceError("reference_precondition_required", 428)
    row = require_edit_lock(session, key, actor, token, epoch)
    changed = activity_sequence > row.activity_sequence
    if changed:
        row.activity_sequence = activity_sequence
    row.expires_at, row.idle_expires_at = compute_deadlines(utc_now(), row.idle_expires_at, record_activity=changed)
    return edit_lock_status(session, key)


def release_edit_lock(session, key, actor, token, epoch):
    require_access(key, actor)
    _token_hash(token)
    row = lock_reference_resources(session, [key])[key]
    if type(epoch) is int and _matches(row, actor, token, epoch):
        _clear(row)
        _event(session, key, actor, audit.REFERENCE_EDIT_RELEASED, row)
    return {"released": True}


def revoke_edit_lock(session, key, actor, expected_epoch, reason):
    require_access(key, actor, admin=True)
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
        raise ReferenceError("reference_precondition_required", 428)
    row = lock_reference_resources(session, [key])[key]
    require_resource(session, key)
    if type(expected_epoch) is not int or row.lease_epoch != expected_epoch:
        raise ReferenceError("reference_edit_lock_lost")
    old_owner = row.owner_id
    row.lease_epoch += 1
    _clear(row)
    _event(session, key, actor, audit.REFERENCE_EDIT_REVOKED, row, reason=reason.strip(), previous_owner_id=old_owner)
    return edit_lock_status(session, key)


def acquire_edit_locks(session, keys, actor, tokens, expected_epochs=None):
    if not 1 <= len(keys) <= 2 or len(set(keys)) != len(keys):
        raise ReferenceError("reference_precondition_required", 428)
    for key in keys:
        require_access(key, actor)
        require_resource(session, key)
        _token_hash(tokens[key])
    # A savepoint also protects callers that catch a conflict inside their transaction.
    with session.begin_nested():
        lock_reference_resources(session, keys)
        for key, epoch in (expected_epochs or {}).items():
            require_edit_lock(session, key, actor, tokens[key], epoch)
        return {key: acquire_edit_lock(session, key, actor, tokens[key]) for key in sorted(keys, key=lambda k: k.canonical)}
