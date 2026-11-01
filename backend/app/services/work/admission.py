"""Global document intent admission; lock admission, then work, then document."""
import hashlib
import re

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app import error_codes as codes
from app.db.models import WorkAdmissionState, WorkItem
from app.services.errors import ConflictError, DomainError
from app.services.generation_files import _validate_ids
from .models import WorkSpec
from .store import enqueue

PENDING = ("queued", "running")
DOCUMENT_MUTATORS = ("document", "scope_change", "canonical_repair")
DERIVED_SCOPE_OUTPUT_FIELDS = frozenset({"signature_cache", "chunk_counts", "archived_chunk_sources"})


def durable_documents(settings):
    return getattr(settings, "okf_deployment_schema", None) == "replicas-v1" and getattr(settings, "app_role", "combined") in {"api", "worker", "combined"}


def initialize_admission(engine):
    insert = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    with engine.begin() as connection:
        connection.execute(insert(WorkAdmissionState).values(id=1).on_conflict_do_nothing())


def lock_admission(session):
    found = session.scalar(update(WorkAdmissionState).where(WorkAdmissionState.id == 1).values(id=WorkAdmissionState.id)
        .returning(WorkAdmissionState.id).execution_options(synchronize_session=False))
    if found is None:
        raise ValueError("Apply document work admission schema first")


def lock_document_intents(session, doc_id):
    """Serialize new admissions before acquiring pending work in stable order."""
    lock_admission(session)
    rows = list(session.scalars(select(WorkItem).where(WorkItem.doc_id == doc_id, WorkItem.kind.in_(DOCUMENT_MUTATORS),
        WorkItem.status.in_(PENDING)).order_by(WorkItem.id).with_for_update()))
    session.info.setdefault("locked_document_intents", set()).add(doc_id)
    return rows


def invalidate_document_intents(session, doc_id):
    from .store import cancel
    invalidated = False
    for work in lock_document_intents(session, doc_id):
        invalidated = cancel(session, work.id) or invalidated
    return invalidated


def retain_deleted_generations(session, doc_id):
    from app.db.models import DeletedWorkGeneration, DocumentGeneration
    for generation in session.scalars(select(DocumentGeneration).where(DocumentGeneration.doc_id == doc_id,
            DocumentGeneration.work_id.is_not(None)).order_by(DocumentGeneration.id)):
        if session.get(DeletedWorkGeneration, generation.id) is None:
            session.add(DeletedWorkGeneration(generation_id=generation.id, doc_id=doc_id,
                work_id=generation.work_id, work_fence=generation.work_fence))
    session.flush()


def document_request_key(doc_id, action, request_id):
    return "document/" + doc_id + "/" + action + "/" + hashlib.sha256(request_id.encode()).hexdigest()


def enqueue_document(session, settings, *, doc_id, action, source_file_hash, request_id, is_admin=False):
    _validate_ids(doc_id, "0" * 32)
    if (action not in {"process", "resume", "regenerate"} or not isinstance(source_file_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", source_file_hash) or not isinstance(request_id, str) or not 1 <= len(request_id) <= 128):
        raise ValueError("Invalid document admission identity")
    key = document_request_key(doc_id, action, request_id)
    spec = WorkSpec("document", 1, {"doc_id": doc_id, "action": action, "source_file_hash": source_file_hash}, key, doc_id)
    lock_admission(session)
    existing = session.scalar(select(WorkItem.id).where(WorkItem.dedupe_key == key))
    if existing is not None:
        return enqueue(session, spec)
    pending = list(session.scalars(select(WorkItem).where(WorkItem.doc_id == doc_id, WorkItem.kind.in_(DOCUMENT_MUTATORS),
        WorkItem.status.in_(PENDING)).order_by(WorkItem.id).with_for_update()))
    if any(not row.cancel_requested for row in pending):
        raise ConflictError("Документ уже обрабатывается", code=codes.ALREADY_PROCESSING)
    total = session.scalar(select(func.count()).select_from(WorkItem).where(WorkItem.kind.in_(DOCUMENT_MUTATORS), WorkItem.status.in_(PENDING)))
    caller_limit = settings.pipeline_max_workers + (settings.pipeline_admin_max_pending if is_admin else settings.pipeline_max_pending)
    if total >= min(settings.work_max_pending_documents, caller_limit):
        raise DomainError("Очередь обработки заполнена", code=codes.QUEUE_OVERLOADED)
    session.info["document_work_admission"] = True
    return enqueue(session, spec)


def freeze_document_input(session, work_id, doc_id, scope, request_id=None):
    from copy import deepcopy
    from app.db.models import DocumentWorkInput
    from app.services.processing_scope import ProcessingScopeConflict
    previous = session.get(DocumentWorkInput, work_id)
    if previous is not None:
        if previous.doc_id != doc_id or previous.processing_scope_json != scope or previous.scope_request_id != request_id:
            raise ProcessingScopeConflict()
        return
    session.add(DocumentWorkInput(work_id=work_id, doc_id=doc_id, processing_scope_json=deepcopy(scope), scope_request_id=request_id))
    session.flush()


def scope_policy(scope):
    """Selection stays immutable while output counters/cache/proof are recomputed."""
    return {key: value for key, value in scope.items() if key not in {*DERIVED_SCOPE_OUTPUT_FIELDS, "vector_space"}} if scope is not None else None


def _active_published_scope_matches(session, doc_id, scope):
    from app.config import get_settings
    from app.db.models import DocumentGeneration, DocumentGenerationState
    from app.services.artifacts.publication import hydrate_publication
    from app.services.generation_artifacts import load_verified_publication
    state = session.get(DocumentGenerationState, doc_id)
    generation = session.get(DocumentGeneration, state.active_generation_id) if state and state.active_generation_id else None
    if (generation is None or generation.doc_id != doc_id or generation.phase != "active"
            or scope_policy(generation.processing_scope_json) != scope_policy(scope)):
        return False
    settings = get_settings()
    hydrate_publication(settings, doc_id, generation.id)
    prepared = load_verified_publication(settings, doc_id, generation.id, generation.publication_hash, session=session)
    return scope_policy(prepared.get("processing_scope")) == scope_policy(scope)


def validate_document_scope(session, doc_id, scope, source_hash, request_id=None):
    from app.db.models import DocumentProcessingReview
    from app.services.processing_scope import ProcessingScopeConflict, snapshot_from_review, selection_hash
    if scope is None:
        if request_id is not None:
            raise ProcessingScopeConflict()
        return
    from docparser.attachment_policy import ParserProcessingPolicy
    ParserProcessingPolicy(scope)
    if scope.get("source_file_hash") != source_hash or scope.get("selection_hash") != selection_hash(
            source_hash, scope.get("parser_version"), scope.get("traversal_limits"), scope.get("decisions", [])):
        raise ProcessingScopeConflict()
    if scope.get("review_id"):
        review = session.get(DocumentProcessingReview, doc_id)
        expected = {key: value for key, value in scope_policy(scope).items() if key != "confirmed_at"}
        if (review is not None and review.state in {"submitted", "published"} and snapshot_from_review(review) == expected
                and (request_id is None or review.request_id == request_id)):
            return
        if request_id is None and _active_published_scope_matches(session, doc_id, scope):
            return  # Reprocessing the verified active policy does not depend on a later draft review.
        raise ProcessingScopeConflict()


def enqueue_existing_document(settings, doc_id, action, *, is_admin=False, processing_scope=None, scope_request_id=None, session=None, request_id=None):
    """Admit an existing original without touching a worker's local filesystem."""
    from uuid import uuid4
    from contextlib import nullcontext
    from copy import deepcopy
    from app.db.models import Document, DocumentStaging, DocumentGeneration, DocumentGenerationState, DocumentProcessingReview
    from app.db.session import session_scope
    from app.services.artifacts.repository import resolve_blob
    from app.services.errors import NotFoundError
    from app.services.generation_store import lock_document_write
    with nullcontext(session) if session is not None else session_scope() as session:
        lock_document_intents(session, doc_id)
        if not lock_document_write(session, doc_id, allow_deleted=False):
            raise NotFoundError("Документ не найден", code=codes.DOCUMENT_NOT_FOUND)
        doc = session.get(Document, doc_id)
        ref = resolve_blob(session, owner_type="original", owner_id=doc_id, generation_id=None, logical_name="original")
        if ref is None:
            raise NotFoundError("Исходный файл документа не найден", code=codes.FILE_NOT_FOUND)
        if processing_scope is None:
            state = session.get(DocumentGenerationState, doc_id)
            checkpoint = session.get(DocumentStaging, doc_id) if action == "resume" else None
            source_id = checkpoint.generation_id if checkpoint else state.active_generation_id if state else None
            generation = session.get(DocumentGeneration, source_id) if source_id else None
            processing_scope = deepcopy(generation.processing_scope_json) if generation else None
        review = session.get(DocumentProcessingReview, doc_id)
        if review and (review.state in {"draft", "awaiting_similar", "validating"} or review.state == "submitted" and not review.generation_id):
            if not scope_request_id or not processing_scope or processing_scope.get("review_id") != review.id:
                raise ConflictError("Выберите состав вложений документа", code="processing_scope_review_required")
        validate_document_scope(session, doc_id, processing_scope, ref.sha256, scope_request_id)
        if action == "resume":
            staging = session.get(DocumentStaging, doc_id)
            from app.services.gen_quality import partial_chunk_indices
            partial = staging is not None and partial_chunk_indices(staging.chunks_data or {})
            if doc.status == "done" and not partial:
                raise DomainError("Нет контрольных данных для догенерации", code=codes.PARTIAL_REGENERATION_UNAVAILABLE)
            if doc.status not in {"paused", "failed", "error", "done"}:
                raise ConflictError("Документ уже обрабатывается", code=codes.ALREADY_PROCESSING)
        elif action == "regenerate" and doc.status in {"uploaded", "queued", "processing", "splitting", "indexing"}:
            raise ConflictError("Документ уже обрабатывается", code=codes.ALREADY_PROCESSING)
        work_id = enqueue_document(session, settings, doc_id=doc_id, action=action, source_file_hash=ref.sha256,
            request_id=scope_request_id or request_id or ("upload" if action == "process" else uuid4().hex), is_admin=is_admin)
        if session.get(WorkItem, work_id).status not in PENDING:
            return work_id  # A receipt replay cannot restart completed/failed work.
        freeze_document_input(session, work_id, doc_id, processing_scope, scope_request_id)
        from app.services.document_update import capture_published_update
        capture_published_update(session, doc_id, settings, fresh=action == "regenerate")
        doc.status, doc.error, doc.error_code = "queued", None, None
        return work_id
