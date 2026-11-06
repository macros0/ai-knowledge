"""Durable scope admission; HTTP never allocates the worker's generation."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select

from app.db.models import Document, DocumentGeneration, DocumentGenerationState, DocumentProcessingReview, DocumentScopeChange, WorkItem
from app.db.session import session_scope
from app.services.generation_store import lock_document_write

from .admission import DOCUMENT_MUTATORS, PENDING, lock_document_intents
from .models import WorkSpec
from .store import enqueue


def scope_operation(session, work):
    from app.services.artifacts.publication import canonical_hash
    from app.services.generation_files import _validate_ids
    from .context import bind_session
    from .models import LostOwnership
    spec, payload = work.spec, work.spec.payload
    if (spec.kind != "scope_change" or spec.payload_version != 1 or spec.doc_id != payload.get("doc_id")
            or set(payload) != {"doc_id", "plan_id", "request_id", "source_file_hash", "input_hash"}):
        raise ValueError("Invalid scope work payload")
    _validate_ids(spec.doc_id, payload["plan_id"])
    bind_session(session, work)
    row = session.get(DocumentScopeChange, payload["plan_id"], populate_existing=True)
    if (row is None or row.doc_id != spec.doc_id or row.request_id != payload["request_id"]
            or canonical_hash(row.plan_json) != payload["input_hash"]
            or row.plan_json["snapshot"].get("source_file_hash") != payload["source_file_hash"]):
        raise LostOwnership("Accepted scope work input changed")
    return row


def cancel_scope_receipts(session, doc_id):
    """Explicit update cancellation discards accepted scope work in the same transaction."""
    from .store import _now
    for row in session.scalars(select(DocumentScopeChange).where(DocumentScopeChange.doc_id == doc_id,
            DocumentScopeChange.state.in_({"queued", "admitted", "processing", "copying", "ready"}))):
        row.state, row.error_code = "canceled", "update_canceled"
        row.owner_token, row.lease_expires_at, row.finished_at = None, None, _now(session)


def retained_scope_receipt(session, doc_id, *, request_id=None):
    """The caller holds the document lock; trash retains this accepted draft."""
    from app.db.models import DocumentUpdateAttempt
    from app.services.artifacts.publication import canonical_hash
    from app.services.processing_scope import ProcessingScopeConflict
    document = session.get(Document, doc_id)
    attempt = session.get(DocumentUpdateAttempt, doc_id)
    if document is None or document.status != "paused" or attempt is None or attempt.cancel_requested:
        return None
    query = select(DocumentScopeChange).where(DocumentScopeChange.doc_id == doc_id,
        DocumentScopeChange.state.in_({"queued", "admitted", "processing", "copying", "ready"}))
    if request_id is not None:
        query = query.where(DocumentScopeChange.request_id == request_id)
    operation = session.scalar(query.order_by(DocumentScopeChange.started_at.desc(), DocumentScopeChange.id).limit(1))
    if operation is None:
        return None
    original = session.scalar(select(WorkItem).where(WorkItem.dedupe_key == f"scope-change/{operation.id}/{operation.request_id}"))
    payload = (original.payload_json or {}) if original else {}
    if (original is None or original.kind != "scope_change" or original.doc_id != doc_id
            or payload.get("plan_id") != operation.id or payload.get("request_id") != operation.request_id
            or payload.get("input_hash") != canonical_hash(operation.plan_json)):
        raise ProcessingScopeConflict("Сохранённый состав изменился. Повторите анализ.")
    return operation, original


def resume_scope_change(settings, doc_id, *, is_admin=False):
    from uuid import uuid4
    from app import error_codes as codes
    from app.services.artifacts.repository import resolve_blob
    from app.services.errors import ConflictError, DomainError
    from app.services.processing_scope import ProcessingScopeConflict
    with session_scope() as session:
        pending = lock_document_intents(session, doc_id)
        if not lock_document_write(session, doc_id, allow_deleted=False):
            return None
        retained = retained_scope_receipt(session, doc_id)
        if retained is None:
            return None
        operation, original = retained
        for item in pending:
            if not item.cancel_requested:
                if item.kind == "scope_change" and item.payload_json == original.payload_json:
                    return item.id
                raise ConflictError("Документ уже обрабатывается", code=codes.ALREADY_PROCESSING)
        state = session.get(DocumentGenerationState, doc_id)
        review = session.get(DocumentProcessingReview, doc_id)
        value = operation.plan_json
        if (state is None or state.active_generation_id != value["preview"]["base_generation_id"]
                or review is None or review.state != "submitted" or review.id != value["review_id"]
                or review.revision != value["revision"] or review.selection_hash != value["snapshot"]["selection_hash"]):
            raise ProcessingScopeConflict()
        ref = resolve_blob(session, owner_type="original", owner_id=doc_id, generation_id=None, logical_name="original")
        if ref is None or ref.sha256 != original.payload_json.get("source_file_hash"):
            raise ProcessingScopeConflict("Исходный файл изменился. Повторите анализ.", code="source_changed")
        total = session.scalar(select(func.count()).select_from(WorkItem).where(WorkItem.kind.in_(DOCUMENT_MUTATORS), WorkItem.status.in_(PENDING)))
        capacity = settings.pipeline_max_workers + (settings.pipeline_admin_max_pending if is_admin else settings.pipeline_max_pending)
        if total >= min(settings.work_max_pending_documents, capacity):
            raise DomainError("Очередь обработки заполнена", code=codes.QUEUE_OVERLOADED)
        work_id = enqueue(session, WorkSpec("scope_change", 1, deepcopy(original.payload_json), f"scope-resume/{operation.id}/{uuid4().hex}", doc_id))
        operation.state, operation.owner_token, operation.lease_expires_at, operation.error_code = "queued", None, None, None
        document = session.get(Document, doc_id)
        document.status, document.error, document.error_code = "queued", None, None
        return work_id


def prepare_scope_attempt(session, work, settings):
    from app.services.artifacts.repository import resolve_blob
    from app.services.generation_store import abandon_generation, begin_generation
    from .context import work_execution
    from .document_handler import _copy_source_results
    from .models import LostOwnership
    with work_execution(work):
        row = scope_operation(session, work)
        doc_id = work.spec.doc_id
        if not lock_document_write(session, doc_id, allow_deleted=False):
            raise LostOwnership("Scope document is no longer writable")
        state = session.get(DocumentGenerationState, doc_id)
        review = session.get(DocumentProcessingReview, doc_id)
        value = row.plan_json
        if (row.state not in {"queued", "admitted", "processing", "copying", "ready"}
                or state is None or state.active_generation_id != value["preview"]["base_generation_id"]
                or review is None or review.id != value["review_id"] or review.revision != value["revision"]
                or review.state != "submitted" or review.selection_hash != value["snapshot"]["selection_hash"]):
            raise LostOwnership("Scope base or review changed")
        ref = resolve_blob(session, owner_type="original", owner_id=doc_id, generation_id=None, logical_name="original")
        if ref is None or ref.sha256 != work.spec.payload["source_file_hash"]:
            raise LostOwnership("Scope original changed")
        previous = session.scalar(select(DocumentGeneration).where(DocumentGeneration.work_id == work.work_id,
            DocumentGeneration.work_fence == work.fence))
        if previous is not None:
            if previous.phase not in {"preparing", "ready"} or state.candidate_generation_id != previous.id:
                raise LostOwnership("Scope attempt is no longer writable")
            return previous
        old_id = state.candidate_generation_id
        if old_id:
            old = session.get(DocumentGeneration, old_id)
            old_owner = session.get(WorkItem, old.work_id) if old and old.work_id else None
            same_input = old_owner and old_owner.kind == "scope_change" and old_owner.payload_json == work.spec.payload
            replaceable = same_input and (old_owner.cancel_requested or old_owner.status not in PENDING)
            if old is None or old.work_id != work.work_id and not replaceable:
                raise LostOwnership("A different work owns this candidate")
            abandon_generation(session, doc_id, old_id)
        base = session.get(DocumentGeneration, state.active_generation_id)
        generation = begin_generation(session, doc_id, chunk_projection_version=base.chunk_projection_version)
        generation.work_id, generation.work_fence = work.work_id, work.fence
        generation.processing_scope_json = deepcopy(value["snapshot"])
        # The logical receipt stays on ScopeChange/Work; each fence owns a new generation.
        if old_id:
            _copy_source_results(session, doc_id, old_id, generation.id)
        row.candidate_generation_id, row.owner_token = generation.id, uuid4().hex
        row.state, row.lease_expires_at = "processing", work.lease_until
        review.generation_id = generation.id
        session.get(Document, doc_id).status = "processing"
        session.flush()
        return generation


def run_scope_job(work):
    from app.services.artifacts.contract import BlobUnavailable
    from app.services.artifacts.original import original_path
    from app.services.generation_artifacts import file_digest, GenerationIntegrityError
    from app.services.generation_publication import publish_prepared_document
    from app.services.pipeline import get_pipeline
    from app.services.processing_scope_worker import execute_scope_change
    from app.services.processing_scope_jobs import fail_scope_change
    from app.services.errors import public_error_code
    from app import error_codes as codes
    from .context import work_execution
    from .handlers import TransientWorkError
    from .models import LostOwnership
    pipeline = get_pipeline()
    with work_execution(work):
        try:
            with session_scope() as session:
                operation = scope_operation(session, work)
                document = session.get(Document, work.spec.doc_id)
                filename = document.filename if document else None
            if filename is None or file_digest(original_path(pipeline.settings, work.spec.doc_id, filename)) != work.spec.payload["source_file_hash"]:
                raise GenerationIntegrityError()
            with session_scope() as session:
                generation = prepare_scope_attempt(session, work, pipeline.settings)
                operation = session.get(DocumentScopeChange, work.spec.payload["plan_id"])
                owner = operation.owner_token
            if generation.phase == "ready":
                publish_prepared_document(pipeline.settings, pipeline.vector_store, work.spec.doc_id, generation.id, claim=work)
            else:
                execute_scope_change(work.spec.doc_id, work.spec.payload["plan_id"], owner)
        except LostOwnership:
            raise
        except Exception as exc:
            transient = isinstance(exc, (BlobUnavailable, TransientWorkError)) or public_error_code(exc) in {
                codes.DEPENDENCY_UNAVAILABLE, codes.TIMEOUT, codes.RATE_LIMITED}
            with session_scope() as session:
                operation = scope_operation(session, work)
                if not lock_document_write(session, work.spec.doc_id, allow_deleted=False):
                    raise LostOwnership("Scope document is no longer writable") from exc
                if transient:
                    document = session.get(Document, work.spec.doc_id)
                    document.status, document.error, document.error_code = "paused", "Обработка прервана", codes.PROCESSING_UNAVAILABLE
                else:
                    fail_scope_change(work.spec.doc_id, work.spec.payload["plan_id"], operation.owner_token)
            if transient:
                if isinstance(exc, TransientWorkError):
                    raise
                raise TransientWorkError("storage_unavailable") from exc
            raise
    with session_scope() as session:
        completed = session.get(WorkItem, work.work_id)
        if completed is None or completed.fence != work.fence or completed.status != "succeeded":
            raise LostOwnership("Scope stopped before atomic publication")


def admit_scope_change(doc_id, plan_id, request_id, expected_active_generation_id, actor_id, allowed_modes, *,
                       is_admin=False, edit_owner_id=None, edit_token=None):
    from app.config import get_settings
    from app.services import processing_scope_jobs as jobs
    from app.services.artifacts.publication import canonical_hash
    from app.services.artifacts.repository import resolve_blob
    from app.services.document_update import capture_published_update
    from app.services.processing_scope import ProcessingScopeConflict, append_scope_audit, locked_review
    from app.services.processing_scope_cache import generation_vector_space
    from app.services.processing_scope_change import load_scope_base, scope_base_digest
    settings = get_settings()
    with session_scope() as session:
        pending = lock_document_intents(session, doc_id)
        if not lock_document_write(session, doc_id, allow_deleted=False):
            raise ProcessingScopeConflict()
        replay = jobs._replay_receipt(session, doc_id, plan_id, request_id, expected_active_generation_id, allowed_modes)
        if replay is not None:
            return replay
        if pending:
            raise jobs.ScopeWorkerBusy("Документ уже ожидает обработки")
        if edit_owner_id is not None:
            from app.services.processing_scope_edit_lock import require_edit_lock
            require_edit_lock(session, doc_id, edit_owner_id, edit_token)
        base = load_scope_base(doc_id, session=session)
        row = session.get(DocumentScopeChange, plan_id, populate_existing=True)
        if row is None or row.doc_id != doc_id or row.state != "preview":
            raise ProcessingScopeConflict()
        value, plan = deepcopy(row.plan_json), row.plan_json["preview"]
        if plan["mode"] not in allowed_modes or plan["mode"] == "blocked":
            raise ProcessingScopeConflict("Для этого изменения требуется другое действие.")
        if jobs._utc(row.expires_at) <= datetime.now(timezone.utc):
            raise ProcessingScopeConflict("Анализ устарел. Проверьте изменения повторно.")
        jobs._check_base(session, base, expected_active_generation_id, snapshot_locked=True)
        review = locked_review(session, doc_id, value["review_id"], value["revision"])
        if (review.state not in {"draft", "awaiting_similar"} or review.selection_hash != value["snapshot"]["selection_hash"]
                or scope_base_digest(base) != value["input_digest"]):
            raise ProcessingScopeConflict()
        ref = resolve_blob(session, owner_type="original", owner_id=doc_id, generation_id=None, logical_name="original")
        if ref is None or ref.sha256 != base.file_hash or ref.sha256 != value["snapshot"]["source_file_hash"]:
            raise ProcessingScopeConflict("Исходный файл изменился. Повторите анализ.", code="source_changed")
        if plan["mode"] != "noop":
            total = session.scalar(select(func.count()).select_from(WorkItem).where(WorkItem.kind.in_(DOCUMENT_MUTATORS),
                WorkItem.status.in_(PENDING)))
            capacity = settings.pipeline_max_workers + (settings.pipeline_admin_max_pending if is_admin else settings.pipeline_max_pending)
            if total >= min(settings.work_max_pending_documents, capacity):
                raise jobs.ScopeWorkerBusy("Очередь обработки занята. Повторите применение позже.")
        now = datetime.now(timezone.utc)
        row.request_id = request_id
        review.confirmed_by, review.request_id = actor_id, request_id
        value["snapshot"].update(confirmed_by=actor_id, confirmed_at=now.isoformat())
        value["snapshot"]["vector_space"] = base.vector_space or generation_vector_space(base.scope, base.generation_id)
        row.plan_json = value
        append_scope_audit(session, doc_id, actor_id, "document_processing_scope_confirmed", review)
        if plan["mode"] == "noop":
            row.state, row.finished_at = "published", now
            row.result_json = {"mode": "noop", "generation_id": base.generation_id}
            review.state = "published"
            return jobs._public(row)
        capture_published_update(session, doc_id, settings)
        enqueue(session, WorkSpec("scope_change", 1, {"doc_id": doc_id, "plan_id": plan_id, "request_id": request_id,
            "source_file_hash": ref.sha256, "input_hash": canonical_hash(value)}, f"scope-change/{plan_id}/{request_id}", doc_id))
        row.candidate_generation_id, row.owner_token, row.lease_expires_at = None, None, None
        row.state, row.started_at = "queued", now
        review.state, review.generation_id = "submitted", None
        session.get(Document, doc_id).status = "queued"
        return jobs._public(row)
