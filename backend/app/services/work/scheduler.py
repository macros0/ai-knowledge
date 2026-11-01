"""Caller-owned periodic intent admission, shared across scheduler processes."""
from dataclasses import dataclass
from datetime import datetime
import re

from sqlalchemy import select

from app.db.models import WorkItem
from .admission import lock_admission
from .models import WorkSpec
from .store import _now, enqueue


@dataclass(frozen=True)
class PeriodicSpec:
    kind: str
    interval_seconds: int
    scope: str = "global"

    def __post_init__(self):
        if (not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.kind)
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.scope)
                or type(self.interval_seconds) is not int or not 1 <= self.interval_seconds <= 31 * 86400):
            raise ValueError("Invalid bounded periodic work")


def enqueue_due(session, spec: PeriodicSpec, *, at_utc: datetime | None = None):
    now = at_utc if at_utc is not None else _now(session)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Periodic work requires an aware database timestamp")
    prefix = f"schedule/{spec.kind}/{spec.scope}/"
    slot = int(now.timestamp()) // spec.interval_seconds
    lock_admission(session)
    pending = session.scalar(select(WorkItem.id).where(WorkItem.kind == spec.kind,
        WorkItem.status.in_(["queued", "running"]), WorkItem.dedupe_key.startswith(prefix))
        .order_by(WorkItem.created_at, WorkItem.id).limit(1).with_for_update())
    if pending is not None:
        return pending  # A slow task coalesces missed slots instead of growing a backlog.
    return enqueue(session, WorkSpec(spec.kind, 1, {}, prefix + str(slot)))


def periodic_specs(settings):
    specs = [PeriodicSpec("auth_purge", 3600), PeriodicSpec("bulk_reconcile", 30),
        PeriodicSpec("generation_sweep", 60), PeriodicSpec("export_purge", 300), PeriodicSpec("index_backfill_sweep", 3600),
        PeriodicSpec("translation_reconcile", 30), PeriodicSpec("diagnostic_maintenance", 60), PeriodicSpec("repair_reconcile", 60)]
    if settings.chat_history_purge_enabled:
        specs.append(PeriodicSpec("chat_purge", max(60, min(31 * 86400, int(settings.chat_history_purge_interval_seconds)))))
    if settings.artifact_gc_enabled:
        specs.append(PeriodicSpec("artifact_gc", settings.artifact_gc_interval_seconds))
    if settings.trash_purge_enabled:
        specs.append(PeriodicSpec("trash_sweep", max(60, min(31 * 86400, int(settings.trash_purge_interval_seconds)))))
    return specs
