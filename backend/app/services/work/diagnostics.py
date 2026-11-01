"""Shared diagnostic retention/reconciliation; no instance spool is scanned."""
from datetime import timedelta

from sqlalchemy import select

from app.db.models import DiagnosticBundle, WorkItem
from app.db.session import session_scope
from app.services import audit
from app.services.artifacts.job_files import lock_job, retire_files
from .context import work_execution
from .handlers import WorkDeferred
from .progress import load_progress, save_progress
from .store import _now, _utc


def maintain_diagnostics(work):
    if work.spec.payload:
        raise ValueError("Invalid diagnostic maintenance input")
    with work_execution(work):
        with session_scope() as session:
            cursor = load_progress(session, work, version=1).get("last_id", "")
            if not isinstance(cursor, str) or len(cursor) > 36:
                raise ValueError("Invalid diagnostic maintenance cursor")
            ids = list(session.scalars(select(DiagnosticBundle.id).where(DiagnosticBundle.id > cursor)
                .order_by(DiagnosticBundle.id).limit(100)))
        for bundle_id in ids:
            with session_scope() as session:
                row = lock_job(session, DiagnosticBundle, bundle_id)
                now = _now(session)
                if row and row.artifact_version == 2:
                    if row.status == "ready" and row.expires_at is not None and _utc(row.expires_at) <= now:
                        row.status = "expired"
                        retire_files(session, kind="diagnostic_bundle", job_id=bundle_id)
                        audit.record_in_session(session, action_type="diagnostic_bundle_expired", user_id="system",
                            username="system", target_type="diagnostic_bundle", target_id=bundle_id, new_value={"expired": True})
                    elif row.status in {"queued", "building"}:
                        parent = session.scalar(select(WorkItem).where(WorkItem.kind == "diagnostic_bundle",
                            WorkItem.payload_json["bundle_id"].as_string() == bundle_id).limit(1))
                        if parent is not None and parent.status in {"failed", "cancelled"}:
                            row.status, row.error_code, row.finished_at = "failed", "diagnostic_bundle_failed", now
                            audit.record_in_session(session, action_type="diagnostic_bundle_failed", user_id="system",
                                username="system", target_type="diagnostic_bundle", target_id=bundle_id,
                                new_value={"error_code": row.error_code})
                    if (row.status in {"failed", "expired", "deleted"} and row.finished_at is not None
                            and _utc(row.finished_at) < now - timedelta(days=7) and not row.audit_receipts):
                        retire_files(session, kind="diagnostic_bundle", job_id=bundle_id)
                        session.delete(row)
                save_progress(session, work, version=1, state={"last_id": bundle_id})
    return WorkDeferred(1) if len(ids) == 100 else None
