"""Trusted repair admission; payloads select an explicit operation, never Python."""
from copy import copy
from uuid import uuid4

from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import Document, DocumentGeneration, DocumentGenerationState, DocumentProcessingReview, WorkItem
from app.db.session import session_scope
from app.services.artifacts.repository import resolve_blob
from app.services.generation_artifacts import GenerationIntegrityError
from app.services.generation_files import _validate_ids
from app.services.generation_store import lock_document_write, lock_writable_generation, abandon_generation
from app.services.processing_scope_cache import embedding_fingerprint
from .admission import DOCUMENT_MUTATORS, PENDING, lock_document_intents
from .context import work_execution, coordinator_session
from .handlers import TransientWorkError, WorkDeferred
from .models import WorkSpec, LostOwnership
from .store import enqueue, _locked

REPAIRS = frozenset({"attachment_paths", "comment_threads"})
INPUT_FIELDS = frozenset({"doc_id", "repair", "base_generation_id", "canonical_digest", "source_file_hash", "embedding_fingerprint"})


def validate_input(session, work, settings, *, publication=None):
    from app.services.canonical_repair import canonical_rows_digest
    payload = work.spec.payload
    if (work.spec.kind != "canonical_repair" or work.spec.payload_version != 1 or set(payload) != INPUT_FIELDS
            or payload["doc_id"] != work.spec.doc_id or payload["repair"] not in REPAIRS):
        raise ValueError("Invalid canonical repair work")
    _validate_ids(work.spec.doc_id, payload["base_generation_id"] or "0" * 32)
    if not lock_document_write(session, work.spec.doc_id, allow_deleted=False):
        raise LostOwnership("Repair document no longer exists")
    document = session.get(Document, work.spec.doc_id)
    state = session.get(DocumentGenerationState, work.spec.doc_id)
    base = publication.base_generation_id if publication else state.active_generation_id if state else None
    ref = resolve_blob(session, owner_type="original", owner_id=work.spec.doc_id, generation_id=None, logical_name="original")
    review = session.get(DocumentProcessingReview, work.spec.doc_id)
    if (document.status != "done" or base != payload["base_generation_id"] or ref is None or ref.sha256 != payload["source_file_hash"]
            or embedding_fingerprint(settings) != payload["embedding_fingerprint"]
            or canonical_rows_digest(session, work.spec.doc_id) != payload["canonical_digest"]
            or review is not None and review.state not in {"published", "canceled"}):
        raise LostOwnership("Repair base, source or configuration changed")
    active = session.get(DocumentGeneration, base) if base else None
    if active is not None and active.processing_scope_json is not None:
        raise GenerationIntegrityError()


def admit_canonical_repair(settings, doc_id, repair):
    from app.services.canonical_repair import canonical_rows_digest
    from app.services.errors import ConflictError, DomainError
    if repair not in REPAIRS:
        raise ValueError("Unknown trusted repair")
    _validate_ids(doc_id, "0" * 32)
    with session_scope() as session:
        pending = lock_document_intents(session, doc_id)
        if pending or not lock_document_write(session, doc_id, allow_deleted=False):
            raise ConflictError("Документ уже обрабатывается")
        document = session.get(Document, doc_id)
        state = session.get(DocumentGenerationState, doc_id)
        if document.status != "done" or state is not None and state.candidate_generation_id:
            raise ConflictError("Для ремонта требуется опубликованный документ")
        review = session.get(DocumentProcessingReview, doc_id)
        active = session.get(DocumentGeneration, state.active_generation_id) if state and state.active_generation_id else None
        if review and review.state not in {"published", "canceled"} or active and active.processing_scope_json is not None:
            return {"doc_id": doc_id, "status": "skipped", "reason": "processing_scope_not_supported"}
        ref = resolve_blob(session, owner_type="original", owner_id=doc_id, generation_id=None, logical_name="original")
        if ref is None:
            raise GenerationIntegrityError()
        count = session.scalar(select(func.count()).select_from(WorkItem).where(WorkItem.kind.in_(DOCUMENT_MUTATORS), WorkItem.status.in_(PENDING)))
        if count >= min(settings.work_max_pending_documents, settings.pipeline_max_workers + settings.pipeline_admin_max_pending):
            raise DomainError("Очередь обработки заполнена", code="queue_overloaded")
        payload = {"doc_id": doc_id, "repair": repair, "base_generation_id": active.id if active else None,
            "canonical_digest": canonical_rows_digest(session, doc_id), "source_file_hash": ref.sha256,
            "embedding_fingerprint": embedding_fingerprint(settings)}
        work_id = enqueue(session, WorkSpec("canonical_repair", 1, payload, f"canonical-repair/{doc_id}/{uuid4().hex}", doc_id))
        return {"work_id": work_id, "status": "queued", "doc_id": doc_id}


def _transform(work, pipeline):
    if work.spec.payload["repair"] == "attachment_paths":
        from scripts.fix_attachment_paths import _repair_snapshot
        return lambda snapshot: _repair_snapshot(snapshot, [], [])
    from scripts.backfill_comment_concepts import _read_comment_concepts, _replace_comment_snapshot
    from app.services.artifacts.original import original_path
    from app.services.generation_artifacts import file_digest
    with session_scope() as session:
        filename = session.get(Document, work.spec.doc_id).filename
    if not filename.lower().endswith(".docx"):
        raise ValueError("Comment repair requires DOCX")
    path = original_path(pipeline.settings, work.spec.doc_id, filename)
    if file_digest(path) != work.spec.payload["source_file_hash"]:
        raise GenerationIntegrityError()
    comments, digest = _read_comment_concepts(path, filename, pipeline.settings)
    return lambda snapshot: _replace_comment_snapshot(snapshot, comments, digest, pipeline.settings,
        {"threads": len(comments), "removed": 0, "skipped": [], "error": None})


def guarded_store(store, settings, work, generation_id):
    guarded = copy(store)
    def upsert(*args, **kwargs):
        with session_scope() as session:
            lock_writable_generation(session, work.spec.doc_id, generation_id)
            return store._upsert_batches(*args, **kwargs)
    guarded._upsert_batches = upsert
    return guarded


def run_canonical_repair(work):
    from app.services.canonical_repair import repair_published_document
    from app.services.generation_publication import publish_prepared_document
    from app.services.pipeline import get_pipeline
    from app.services.errors import public_error_code
    from app.services.artifacts.contract import BlobUnavailable
    from app import error_codes as codes
    pipeline = get_pipeline()
    with work_execution(work):
        try:
            with session_scope() as session:
                validate_input(session, work, pipeline.settings)
                prior = session.scalar(select(DocumentGeneration).where(DocumentGeneration.work_id == work.work_id)
                    .order_by(DocumentGeneration.work_fence.desc()).limit(1))
                recovery = prior.id if prior else None
                if prior and prior.work_fence == work.fence:
                    if prior.phase == "ready":
                        ready = prior.id
                    else:
                        raise LostOwnership("This repair attempt already ended")
                else:
                    ready = None
                    state = session.get(DocumentGenerationState, work.spec.doc_id)
                    if state and state.candidate_generation_id:
                        old = session.get(DocumentGeneration, state.candidate_generation_id)
                        if old is None or old.work_id != work.work_id:
                            raise LostOwnership("Another work owns the repair candidate")
                        abandon_generation(session, work.spec.doc_id, old.id)
            if ready:
                publish_prepared_document(pipeline.settings, pipeline.vector_store, work.spec.doc_id, ready, claim=work)
                return
            repair_published_document(work.spec.doc_id, pipeline.settings, pipeline.okf_generator, pipeline.embedder,
                pipeline.vector_store, _transform(work, pipeline), recovery_source=recovery)
        except LostOwnership:
            raise
        except Exception as exc:
            transient = isinstance(exc, (TransientWorkError, BlobUnavailable)) or public_error_code(exc) in {
                codes.DEPENDENCY_UNAVAILABLE, codes.TIMEOUT, codes.RATE_LIMITED}
            with session_scope() as session:
                item, _ = _locked(session, work)
                if not transient or item.attempts >= get_settings().work_max_attempts:
                    state = session.get(DocumentGenerationState, work.spec.doc_id)
                    candidate = session.get(DocumentGeneration, state.candidate_generation_id) if state and state.candidate_generation_id else None
                    if candidate and (candidate.work_id, candidate.work_fence) == (work.work_id, work.fence):
                        abandon_generation(session, work.spec.doc_id, candidate.id)
            if transient and not isinstance(exc, TransientWorkError):
                raise TransientWorkError("storage_unavailable") from exc
            raise


def reconcile_repairs(work):
    from .progress import load_progress, save_progress
    if work.spec.payload:
        raise ValueError("Invalid repair reconciliation input")
    with coordinator_session(work) as session:
        cursor = load_progress(session, work, version=1).get("last_id", "")
        if not isinstance(cursor, str) or len(cursor) > 64:
            raise ValueError("Invalid repair reconciliation cursor")
        ids = list(session.scalars(select(DocumentGenerationState.doc_id)
            .join(DocumentGeneration, DocumentGeneration.id == DocumentGenerationState.candidate_generation_id)
            .join(WorkItem, WorkItem.id == DocumentGeneration.work_id)
            .where(DocumentGenerationState.doc_id > cursor, WorkItem.kind == "canonical_repair", WorkItem.status.in_(["failed", "cancelled"]))
            .order_by(DocumentGenerationState.doc_id).limit(100).with_for_update(of=WorkItem)))
        for doc_id in ids:
            lock_document_intents(session, doc_id)
            if lock_document_write(session, doc_id):
                state = session.get(DocumentGenerationState, doc_id, populate_existing=True)
                generation = session.get(DocumentGeneration, state.candidate_generation_id) if state and state.candidate_generation_id else None
                parent = session.get(WorkItem, generation.work_id) if generation and generation.work_id else None
                if parent and parent.kind == "canonical_repair" and parent.status in {"failed", "cancelled"}:
                    abandon_generation(session, doc_id, generation.id, allow_deleted=True)
        if ids:
            save_progress(session, work, version=1, state={"last_id": ids[-1]})
    return WorkDeferred(1) if len(ids) == 100 else None
