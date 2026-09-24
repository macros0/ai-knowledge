"""Shared eligibility rules for the generation preview and queue worker."""
from app import error_codes as codes
from sqlalchemy import select
from app.db.models import Job


def reserved_generation_doc_ids(session) -> set[str]:
    params = session.scalars(select(Job.params).where(
        Job.job_type.in_({"bulk_resume", "bulk_regenerate"}),
        Job.status.in_({"queued", "running", "awaiting_approval"}),
    ))
    return {doc_id for item in params for doc_id in (item or {}).get("doc_ids", [])}


def generation_skip_code(doc: dict | None, *, resume: bool) -> str | None:
    if doc is None:
        return codes.DOCUMENT_NOT_FOUND
    if doc.get("deleted_at"):
        return codes.ALREADY_IN_TRASH
    if doc.get("status") in {"uploaded", "queued", "processing", "splitting", "indexing"}:
        return codes.ALREADY_PROCESSING
    if resume and doc.get("status") not in {"paused", "failed"}:
        return codes.NOT_RESUMABLE
    return None
