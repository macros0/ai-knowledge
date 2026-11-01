"""Bulk orchestration releases worker slots while durable documents execute."""
from sqlalchemy import select

from app import error_codes as codes
from app.db.models import Job, WorkItem
from app.services.artifacts.job_files import lock_job
from app.services.generation_files import _validate_ids

from .context import coordinator_session, work_execution
from .handlers import WorkDeferred
from .models import WorkSpec
from .store import enqueue, finish


def _job(session, payload, *, item=False):
    fields = {"job_id", "doc_id", "index"} if item else {"job_id"}
    if set(payload) != fields or type(payload["job_id"]) is not int or payload["job_id"] <= 0:
        raise ValueError("Invalid bulk work identity")
    job = lock_job(session, Job, payload["job_id"])
    from app.services.job_queue import JOB_TYPES
    if job is None or job.job_type not in JOB_TYPES or job.status not in {"queued", "running"}:
        raise ValueError("Bulk job is no longer pending")
    ids = (job.params or {}).get("doc_ids")
    if not isinstance(ids, list) or not ids:
        raise ValueError("Invalid bulk document list")
    if item:
        index = payload["index"]
        if type(index) is not int or not 0 <= index < len(ids) or ids[index] != payload["doc_id"]:
            raise ValueError("Bulk document does not match the accepted job")
    return job, ids


def run_bulk(work):
    from app.services.job_queue import _utcnow
    with coordinator_session(work) as session:
        job, ids = _job(session, work.spec.payload)
        for index, doc_id in enumerate(ids):
            _validate_ids(doc_id, "0" * 32)
            enqueue(session, WorkSpec("bulk_item", 1, {"job_id": job.id, "doc_id": doc_id, "index": index},
                f"bulk-item/{job.id}/{index}"))
        job.status, job.started_at = "running", job.started_at or _utcnow()
        job.result = {"processed": 0, "total": len(ids), "results": [], "errors": [], "skipped": []}
        finish(session, work, outcome="succeeded")


def reconcile_job(session, job, ids):
    """Public progress is a projection of private terminal item receipts."""
    results, errors, skipped = [], [], []
    rows = {row.dedupe_key: row for row in session.scalars(select(WorkItem).where(WorkItem.kind == "bulk_item",
        WorkItem.dedupe_key.startswith(f"bulk-item/{job.id}/")))}
    for index, doc_id in enumerate(ids):
        row = rows.get(f"bulk-item/{job.id}/{index}")
        if row is None or row.status in {"queued", "running"}:
            continue
        if row.status == "succeeded" and row.error_code:
            skipped.append({"doc_id": doc_id, "error_code": row.error_code,
                **({"requires_action": True} if row.error_code == "processing_scope_review_required" else {})})
        elif row.status == "succeeded":
            results.append({"doc_id": doc_id, "ok": True})
        else:
            errors.append({"doc_id": doc_id, "error": "Массовая операция не завершена", "error_code": row.error_code or codes.INTERNAL_ERROR})
    job.result = {"processed": len(results), "total": len(ids), "results": results, "errors": errors, "skipped": skipped, "error": None}
    if len(results) + len(errors) + len(skipped) == len(ids):
        from app.services.job_queue import _utcnow
        job.status, job.finished_at = ("failed" if errors and not results else "completed"), _utcnow()


def run_bulk_item(work):
    from app.services import audit
    from app.services.bulk_generation import generation_skip_code
    from app.services.errors import DomainError
    from app.services.job_queue import get_settings
    from app.services.pipeline import get_pipeline
    from .admission import document_request_key, enqueue_existing_document
    settings, pipeline = get_settings(), get_pipeline()
    payload = work.spec.payload
    with coordinator_session(work) as session:
        job, ids = _job(session, payload, item=True)
        doc_id, index = payload["doc_id"], payload["index"]
        _validate_ids(doc_id, "0" * 32)
        action = "resume" if job.job_type == "bulk_resume" else "regenerate"
        request_id = f"bulk/{job.id}/{index}"
        child = session.scalar(select(WorkItem).where(WorkItem.dedupe_key == document_request_key(doc_id, action, request_id)))
        skip, failed = None, None
        if job.job_type == "bulk_delete":
            deleted = pipeline.registry.soft_delete(doc_id, deleted_by=job.created_by)
            failed = None if deleted else codes.DOCUMENT_NOT_FOUND
            event = audit.DOCUMENT_BULK_DELETE if deleted else None
        elif child is None:
            skip = generation_skip_code(pipeline.registry.get(doc_id), resume=action == "resume")
            if not skip:
                try:
                    with session.begin_nested():
                        enqueue_existing_document(settings, doc_id, action, is_admin=True, session=session, request_id=request_id)
                except DomainError as exc:
                    if exc.code == codes.QUEUE_OVERLOADED:
                        return WorkDeferred(1)
                    if exc.code == "processing_scope_review_required":
                        skip = exc.code
                    else:
                        failed = exc.code
                if not skip and not failed:
                    audit.record_in_session(session,
                        action_type=audit.DOCUMENT_BULK_RESUME if action == "resume" else audit.DOCUMENT_BULK_REGENERATE,
                        user_id=job.created_by_id, username=job.created_by, target_type=audit.TARGET_DOCUMENT, target_id=doc_id,
                        ip_address=(job.params or {}).get("ip_address"), new_value={"job_id": job.id, "phase": "requested"})
                    return WorkDeferred(1)
            event = None
        elif child.status in {"queued", "running"}:
            return WorkDeferred(1)
        else:
            failed = None if child.status == "succeeded" else child.error_code or codes.REGENERATE_FAILED
            event = None
        if event:
            audit.record_in_session(session, action_type=event, user_id=job.created_by_id,
                username=job.created_by, target_type=audit.TARGET_DOCUMENT, target_id=doc_id,
                ip_address=(job.params or {}).get("ip_address"))
        finish(session, work, outcome="failed" if failed else "succeeded", error_code=failed or skip)
        reconcile_job(session, job, ids)


def reconcile_bulk(work):
    """Repair public progress after a coordinator fails before its final write."""
    if work.spec.payload:
        raise ValueError("Invalid bulk reconciliation payload")
    from app.db.session import session_scope
    from app.services.job_queue import JOB_TYPES, _utcnow
    with work_execution(work):
        with session_scope() as session:
            job_ids = list(session.scalars(select(Job.id).where(Job.job_type.in_(JOB_TYPES),
                Job.status.in_({"queued", "running"})).order_by(Job.id).limit(100)))
        for job_id in job_ids:
            with session_scope() as session:
                job = lock_job(session, Job, job_id)
                if job is None or job.status not in {"queued", "running"}:
                    continue
                parent = session.scalar(select(WorkItem).where(WorkItem.dedupe_key == f"bulk/{job_id}"))
                if parent is None:
                    continue  # A legacy combined job has no distributed receipt.
                if parent.status in {"failed", "cancelled"}:
                    job.status, job.finished_at = "failed", _utcnow()
                    job.result = {"processed": 0, "total": len((job.params or {}).get("doc_ids", [])),
                        "results": [], "errors": [], "skipped": [], "error": "Массовая операция прервана",
                        "error_code": parent.error_code or codes.JOB_INTERRUPTED}
                elif job.status == "running":
                    reconcile_job(session, job, (job.params or {}).get("doc_ids", []))
