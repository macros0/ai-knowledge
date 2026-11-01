"""Explicit bounded maintenance handlers; payloads name SQL identities only."""
from app.db.models import DocumentGeneration, DocumentStaging
from app.db.session import session_scope
from app.services.generation_files import _validate_ids

from .context import work_execution, coordinator_session


def retire_checkpoint(work):
    payload = work.spec.payload
    if set(payload) != {"doc_id", "generation_id"}:
        raise ValueError("Invalid checkpoint retirement payload")
    doc_id, generation_id = payload["doc_id"], payload["generation_id"]
    _validate_ids(doc_id, generation_id)
    with work_execution(work):
        with session_scope() as session:
            generation = session.get(DocumentGeneration, generation_id)
            stage = session.get(DocumentStaging, doc_id)
            if (generation is None or generation.doc_id != doc_id or generation.phase not in {"active", "retired"}
                    or stage is None or stage.generation_id != generation_id):
                return
            from app.services.gen_quality import partial_chunk_indices
            if partial_chunk_indices(stage.chunks_data or {}):
                return
        from app.services.staging import StagingStore
        StagingStore(doc_id).remove(expected_generation_id=generation_id)


def refresh_duplicates(work):
    payload = work.spec.payload
    if set(payload) != {"doc_id", "previous_ids"}:
        raise ValueError("Invalid duplicate refresh payload")
    previous = payload["previous_ids"]
    if not isinstance(previous, list) or len(previous) > 256:
        raise ValueError("Invalid bounded duplicate refresh")
    for doc_id in [payload["doc_id"], *previous]:
        _validate_ids(doc_id, "0" * 32)
    from app.services.deduplication import refresh_duplicate_flags
    with work_execution(work):
        refresh_duplicate_flags(payload["doc_id"], set(previous))


def purge_auth(work):
    if work.spec.payload:
        raise ValueError("Invalid auth retention payload")
    from sqlalchemy import select
    from app.db.models import AuthSession
    from .handlers import WorkDeferred
    from .progress import load_progress, save_progress
    from .store import _now
    with work_execution(work), session_scope() as session:
        cursor = load_progress(session, work, version=1).get("last_id", "")
        if not isinstance(cursor, str) or len(cursor) > 64:
            raise ValueError("Invalid auth retention cursor")
        rows = list(session.scalars(select(AuthSession).where(AuthSession.id > cursor,
            AuthSession.expires_at <= _now(session)).order_by(AuthSession.id).limit(100).with_for_update()))
        for row in rows:
            session.delete(row)
        if rows:
            save_progress(session, work, version=1, state={"last_id": rows[-1].id})
    return WorkDeferred(1) if len(rows) == 100 else None


def collect_artifacts(work):
    if work.spec.payload:
        raise ValueError("Invalid artifact GC payload")
    from app.config import get_settings
    from app.services.artifacts.gc import run_gc_cycle
    with work_execution(work):
        run_gc_cycle(get_settings())


def cleanup_generations(work):
    if set(work.spec.payload) != {"doc_id"}:
        raise ValueError("Invalid generation cleanup payload")
    doc_id = work.spec.payload["doc_id"]
    _validate_ids(doc_id, "0" * 32)
    from app.services.generation_cleanup import cleanup_document_generations
    from app.services.pipeline import get_pipeline
    pipeline = get_pipeline()
    with work_execution(work):
        cleanup_document_generations(pipeline.settings, pipeline.vector_store, doc_id)


def sweep_generations(work):
    from sqlalchemy import or_, select
    from app.db.models import WorkItem
    from .models import WorkSpec
    from .store import enqueue
    if work.spec.payload:
        raise ValueError("Invalid generation sweep payload")
    with coordinator_session(work) as session:
        eligible = or_(DocumentGeneration.legacy_cleanup_pending.is_(True),
            DocumentGeneration.phase.in_({"retired", "abandoned"})
            & or_(DocumentGeneration.work_id.is_(None), DocumentGeneration.work_cleanup_done.is_(False))
            & ~select(DocumentStaging.doc_id).where(DocumentStaging.generation_id == DocumentGeneration.id).exists())
        doc_ids = list(session.scalars(select(DocumentGeneration.doc_id).where(eligible)
            .distinct().order_by(DocumentGeneration.doc_id).limit(100)))
        for doc_id in doc_ids:
            pending = session.scalar(select(WorkItem.id).where(WorkItem.kind == "generation_cleanup", WorkItem.doc_id == doc_id,
                WorkItem.status.in_({"queued", "running"})).order_by(WorkItem.id).limit(1).with_for_update())
            if pending is None:
                enqueue(session, WorkSpec("generation_cleanup", 1, {"doc_id": doc_id},
                    f"generation-cleanup/{doc_id}/{work.work_id}", doc_id))


def purge_exports(work):
    from sqlalchemy import select
    from app.db.models import Job, WorkItem
    from app.services import audit
    from app.services.export_queue import BULK_EXPORT, get_export_queue, _parse_utc
    from .handlers import WorkDeferred
    from .progress import load_progress, save_progress
    from .store import _now
    if work.spec.payload:
        raise ValueError("Invalid export retention payload")
    queue = get_export_queue()
    with work_execution(work):
        with session_scope() as session:
            cursor = load_progress(session, work, version=1).get("last_id", 0)
            if type(cursor) is not int or cursor < 0:
                raise ValueError("Invalid export retention cursor")
            job_ids = list(session.scalars(select(Job.id).where(Job.job_type == BULK_EXPORT,
                Job.id > cursor).order_by(Job.id).limit(100)))
        for job_id in job_ids:
            with session_scope() as session:
                job = session.get(Job, job_id)
                result = (job.result or {}) if job else {}
                expires = _parse_utc(result.get("expires_at"))
                if job and job.status in {"queued", "running"}:
                    parent = session.scalar(select(WorkItem).where(WorkItem.kind == "export",
                        WorkItem.payload_json["job_id"].as_integer() == job_id).limit(1))
                    if parent is not None and parent.status in {"failed", "cancelled"}:
                        from app import error_codes as codes
                        queue._finish_failed(job_id, codes.JOB_INTERRUPTED,
                            total=len((job.params or {}).get("documents") or []), strict=True)
                if (job and job.status == "completed" and result.get("artifact_version") == 2
                        and result.get("artifact_status") == "available" and expires is not None and expires <= _now(session)):
                    queue.delete_artifacts(job_id, audit.SystemUser(), None, reason="expired")
                save_progress(session, work, version=1, state={"last_id": job_id})
    return WorkDeferred(1) if len(job_ids) == 100 else None


def purge_chat(work):
    from datetime import timedelta
    from sqlalchemy import select
    from app.config import get_settings
    from app.db.models import ChatSession
    from app.services import audit
    from .handlers import WorkDeferred
    from .store import _now
    if work.spec.payload:
        raise ValueError("Invalid chat retention payload")
    settings = get_settings()
    if not settings.chat_history_purge_enabled:
        return
    with work_execution(work), session_scope() as session:
        from .chat import lock_chat_history_purge
        lock_chat_history_purge(session)
        cutoff = _now(session) - timedelta(days=settings.chat_history_retention_days)
        rows = list(session.scalars(select(ChatSession).where(ChatSession.deleted_at.is_not(None),
            ChatSession.deleted_at < cutoff).order_by(ChatSession.id).limit(100).with_for_update()))
        for row in rows:
            audit.record_in_session(session, action_type=audit.CHAT_HISTORY_AUTO_DELETE,
                user_id="system", username="system", target_type=audit.TARGET_CHAT, target_id=row.id,
                old_value={"user_id": row.user_id, "title": row.title, "deleted_at": audit.iso_or_str(row.deleted_at)})
            session.delete(row)
    return WorkDeferred(1) if len(rows) == 100 else None


def sync_document_visibility(work):
    from app.db.models import Document
    from app.services.generation_store import lock_document_write
    from app.services.pipeline import get_pipeline
    payload = work.spec.payload
    if set(payload) != {"doc_id"}:
        raise ValueError("Invalid visibility work payload")
    doc_id = payload["doc_id"]
    _validate_ids(doc_id, "0" * 32)
    with work_execution(work), session_scope() as session:
        if not lock_document_write(session, doc_id):
            return
        document = session.get(Document, doc_id)
        from app import error_codes as codes
        from app.services.errors import VectorStoreError, public_error_code
        from .handlers import TransientWorkError
        try:
            get_pipeline().vector_store.set_document_deleted(doc_id, document.deleted_at is not None)
        except VectorStoreError as exc:
            if public_error_code(exc) in {codes.DEPENDENCY_UNAVAILABLE, codes.TIMEOUT, codes.RATE_LIMITED}:
                raise TransientWorkError("storage_unavailable") from exc
            raise
