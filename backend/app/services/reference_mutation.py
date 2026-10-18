"""Checks shared by foreground writers and trusted in-process reference writers."""
from dataclasses import dataclass, field
from app.config import get_settings
from app.services.reference_concurrency import ReferenceError, lock_reference_resources, require_reference_revision
from app.services.reference_edit_lock import live, require_edit_lock
from app.services.reference_resources import LEASE_TYPES, require_access


@dataclass(frozen=True)
class ReferenceWriteContext:
    actor: object
    expected_revisions: dict
    leases: dict = field(default_factory=dict)


def guard_reference_writes(session, keys, context=None):
    rows = lock_reference_resources(session, keys)
    for key in sorted(rows, key=lambda item: item.canonical):
        guard_reference_write(session, key,
            expected_revision=context.expected_revisions.get(key) if context else None,
            actor=context.actor if context else None,
            lease=context.leases.get(key) if context else None)
    return rows


def guard_reference_write(session, key, *, expected_revision=None, actor=None, lease=None):
    if actor is not None:
        require_access(key, actor)
        require_reference_revision(lock_reference_resources(session, [key])[key], expected_revision)
    state = lock_reference_resources(session, [key])[key]
    if expected_revision is not None:
        require_reference_revision(state, expected_revision)
    if actor is not None and get_settings().reference_edit_locks_enabled and key.resource_type in LEASE_TYPES:
        if not lease:
            raise ReferenceError("reference_precondition_required", 428)
        return require_edit_lock(session, key, actor, lease["edit_token"], lease["lease_epoch"], record_activity=True)
    if live(state):
        if not actor or not lease:
            raise ReferenceError("reference_edit_locked")
        return require_edit_lock(session, key, actor, lease["edit_token"], lease["lease_epoch"], record_activity=True)
    return state
