"""Rechecked SQL trash retirement and separately recoverable byte cleanup."""
from datetime import datetime, timedelta

from app.db.models import Document
from app.services.generation_files import _validate_ids
from app.services.generation_store import lock_document_write

from .context import coordinator_session
from .models import WorkSpec
from .store import _now, _utc, enqueue


def purge_document(work):
    payload = work.spec.payload
    if set(payload) != {"doc_id", "deleted_at"} or not isinstance(payload["deleted_at"], str):
        raise ValueError("Invalid trash purge payload")
    doc_id = payload["doc_id"]
    _validate_ids(doc_id, "0" * 32)
    expected = datetime.fromisoformat(payload["deleted_at"])
    if expected.tzinfo is None:
        raise ValueError("Trash retirement requires an aware timestamp")
    from app.services import audit
    from app.services.pipeline import get_pipeline
    pipeline = get_pipeline()
    if not pipeline.settings.trash_purge_enabled:
        return
    with coordinator_session(work) as session:
        if not lock_document_write(session, doc_id):
            return
        document = session.get(Document, doc_id)
        cutoff = _now(session) - timedelta(days=pipeline.settings.trash_retention_days)
        if document.deleted_at is None or _utc(document.deleted_at) != _utc(expected) or _utc(document.deleted_at) > cutoff:
            return
        old_value = {"filename": document.filename, "deleted_at": audit.iso_or_str(document.deleted_at)}
        if not pipeline.registry.delete_if_deleted(doc_id):
            return
        audit.record_in_session(session, action_type=audit.DOCUMENT_AUTO_DELETE, user_id="system", username="system",
            target_type=audit.TARGET_DOCUMENT, target_id=doc_id, old_value=old_value)
        enqueue(session, WorkSpec("trash_cleanup", 1, {"doc_id": doc_id}, f"trash-cleanup/{work.work_id}"))


def cleanup_document_bytes(work):
    if set(work.spec.payload) != {"doc_id"}:
        raise ValueError("Invalid trash cleanup payload")
    doc_id = work.spec.payload["doc_id"]
    _validate_ids(doc_id, "0" * 32)
    from app import error_codes as codes
    from app.services.errors import VectorStoreError, public_error_code
    from app.services.pipeline import get_pipeline
    from .handlers import TransientWorkError
    with coordinator_session(work) as session:
        if session.get(Document, doc_id) is not None:
            return
        pipeline = get_pipeline()
        try:
            pipeline.vector_store.delete_document(doc_id)
        except VectorStoreError as exc:
            if public_error_code(exc) in {codes.DEPENDENCY_UNAVAILABLE, codes.TIMEOUT, codes.RATE_LIMITED}:
                raise TransientWorkError("storage_unavailable") from exc
            raise
        pipeline._physical_cleanup(doc_id, delete_vectors=False)


def sweep_trash(work):
    from sqlalchemy import select
    from app.config import get_settings
    from app.db.models import WorkItem
    from .handlers import WorkDeferred
    from .progress import load_progress, save_progress
    if work.spec.payload:
        raise ValueError("Invalid trash sweep payload")
    settings = get_settings()
    if not settings.trash_purge_enabled:
        return
    with coordinator_session(work) as session:
        cursor = load_progress(session, work, version=1).get("last_id", "")
        if not isinstance(cursor, str) or len(cursor) > 64:
            raise ValueError("Invalid trash sweep cursor")
        cutoff = _now(session) - timedelta(days=settings.trash_retention_days)
        documents = list(session.scalars(select(Document).where(Document.id > cursor,
            Document.deleted_at.is_not(None), Document.deleted_at <= cutoff).order_by(Document.id).limit(100)))
        for document in documents:
            pending = session.scalar(select(WorkItem.id).where(WorkItem.kind == "trash_purge", WorkItem.doc_id == document.id,
                WorkItem.status.in_({"queued", "running"})).order_by(WorkItem.id).limit(1).with_for_update())
            if pending is None:
                enqueue(session, WorkSpec("trash_purge", 1,
                    {"doc_id": document.id, "deleted_at": _utc(document.deleted_at).isoformat()},
                    f"trash-purge/{document.id}/{work.work_id}", document.id))
        if documents:
            save_progress(session, work, version=1, state={"last_id": documents[-1].id})
    return WorkDeferred(1) if len(documents) == 100 else None
