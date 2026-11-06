"""Fixed-cardinality gauges from SQL and process counters without work payloads."""
from collections import Counter
import threading

from sqlalchemy import case, func, select

from app.db.models import WorkItem, ReplicaCapacitySlot
from app.db.session import session_scope
from .store import _now, _utc


class ProcessMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counts = Counter(claims=0, retries=0, lease_losses=0, completed=0)

    def increment(self, name):
        with self._lock:
            if name not in self._counts:
                raise ValueError("Unknown replica counter")
            self._counts[name] += 1

    def snapshot(self):
        with self._lock:
            return dict(self._counts)


def shared_metrics(session, settings):
    now = _now(session)
    groups = session.execute(select(WorkItem.status, func.count(), func.min(WorkItem.created_at))
        .group_by(WorkItem.status)).all()
    counts = {status: count for status, count, _created in groups}
    oldest = next((created for status, _count, created in groups if status == "queued"), None)
    reserved = ReplicaCapacitySlot.owner_id.is_not(None) & (
        ReplicaCapacitySlot.release_required | (ReplicaCapacitySlot.expires_at > now)
        | (ReplicaCapacitySlot.protected_until > now))
    model_reserved = session.scalar(select(func.count()).select_from(ReplicaCapacitySlot)
        .where(ReplicaCapacitySlot.resource == "llm", reserved))
    quarantined = session.scalar(select(func.count()).select_from(ReplicaCapacitySlot).where(
        ReplicaCapacitySlot.resource == "llm", ReplicaCapacitySlot.owner_id.is_not(None),
        ReplicaCapacitySlot.release_required, ReplicaCapacitySlot.expires_at <= now))
    return {
        "queue_depth": counts.get("queued", 0),
        "queue_oldest_seconds": max(0, (now - _utc(oldest)).total_seconds()) if oldest else 0,
        "running": counts.get("running", 0),
        "expired_claims": session.scalar(select(func.count()).select_from(WorkItem)
            .where(WorkItem.status == "running", WorkItem.lease_until <= now)),
        "retry_attempts": session.scalar(select(func.coalesce(func.sum(case(
            (WorkItem.attempts > 1, WorkItem.attempts - 1), else_=0)), 0))),
        "model_reserved": model_reserved, "model_quarantined": quarantined,
        "model_limit": settings.llm_global_max_concurrency,
        "model_saturated": int(model_reserved >= settings.llm_global_max_concurrency),
    }


def render_metrics(settings, handle):
    with session_scope() as session:
        metrics = shared_metrics(session, settings)
    if settings.app_role == "api":
        from app.services.health import get_readiness
        metrics["role_ready"] = int(get_readiness()["status"] == "ready")
    else:
        metrics["role_ready"] = int(handle.health.ready())
    # Shared gauges are repeated by each Pod: aggregate with max, never sum.
    output = [f"okf_replica_{name} {value}" for name, value in sorted(metrics.items())]
    totals = Counter(claims=0, retries=0, lease_losses=0, completed=0)
    for worker in handle._workers:
        totals.update(worker.metrics.snapshot())
    output.extend(f"okf_replica_process_{name}_total {value}" for name, value in sorted(totals.items()))
    return "\n".join(output) + "\n"
