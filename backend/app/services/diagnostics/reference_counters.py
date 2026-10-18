"""Bounded operational counters. Never accept resource IDs, owners or payloads as labels."""
from collections import Counter
from datetime import datetime, timezone
from threading import Lock

from sqlalchemy import case, func, select

from app.db.models import ReferenceResourceState
from app.db.session import session_scope
from app.services.reference_resources import RESOURCE_TYPES

ERRORS = frozenset({"version_conflict", "reference_edit_locked", "reference_edit_lock_lost",
    "reference_write_busy", "reference_precondition_required", "reference_preview_stale"})
SKIPS = frozenset({"skipped_locked", "skipped_changed", "skipped_reviewed", "skipped_deleted"})
_started = datetime.now(timezone.utc).isoformat()
_lock = Lock()
_attempts = Counter()
_backfill = Counter()


def record_error(code):
    if code in ERRORS:
        with _lock:
            _attempts[code] += 1


def record_backfill(resource_type, outcomes):
    if resource_type not in RESOURCE_TYPES:
        return
    with _lock:
        for outcome in SKIPS:
            count = outcomes.get(outcome, 0)
            if type(count) is int and count > 0:
                _backfill[(resource_type, outcome)] += count


def snapshot():
    # One aggregate query, no materialization of IDs, token hashes or owner names.
    types = sorted(RESOURCE_TYPES)
    model = ReferenceResourceState
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        values = session.execute(select(*[
            func.coalesce(func.sum(case((model.resource_key.like('["' + kind + '",%'), 1), else_=0)), 0)
            for kind in types]).where(model.owner_id.is_not(None), model.expires_at > now,
                                     model.idle_expires_at > now)).one()
    with _lock:
        attempts = dict(_attempts)
        backfill = [{"resource_type": kind, "outcome": outcome, "count": count}
                    for (kind, outcome), count in sorted(_backfill.items())]
    return {"attempts_scope": "backend_process_since_start", "process_started_at": _started,
        "attempts": attempts, "backfill_skips": backfill,
        "active_scope": "database_now", "sampled_at": now.isoformat(),
        "active_leases": dict(zip(types, map(int, values)))}
