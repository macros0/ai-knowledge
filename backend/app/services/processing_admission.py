"""Short review leases around bounded parsing and one generation admission."""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.db.models import DocumentGeneration, DocumentGenerationState, DocumentProcessingReview
from app.db.session import session_scope
from app.services.pipeline import get_pipeline
from app.services.processing_scope import (
    ProcessingScopeConflict,
    assessment_limits,
    locked_review,
    snapshot_from_review,
    append_scope_audit,
)
from app.services.registry import get_registry
from app.services.parser_supervisor import inspect_attachments_supervised, parse_document_supervised
from docparser import ParseContext, parse_document, blocks_to_markdown
from docparser.attachment_inventory import inspect_attachment_inventory
from docparser.attachment_assessment import recommend_scope
from docparser.attachment_policy import TraversalLimits, ParserProcessingPolicy


@dataclass
class ProcessingAdmissionResult:
    status: str
    document: dict | None = None
    detail: dict | None = None


def prepare_upload_scope(path, filename, settings, *, attachments_dir=None, branch=None, traversal=None):
    limits = assessment_limits(settings)
    traversal = traversal or TraversalLimits(max_depth=settings.attachment_max_depth)
    if settings.parser_supervisor_enabled:
        inventory = inspect_attachments_supervised(
            path,
            filename,
            attachments_dir=attachments_dir,
            limits=limits,
            traversal=traversal,
            timeout_seconds=min(limits.timeout_seconds, settings.parser_timeout_seconds),
            max_memory_mb=settings.parser_max_memory_mb,
            max_concurrent=settings.parser_max_concurrent,
            mail_enabled=settings.mail_import_enabled,
            archive_helper_path=settings.archive_7zip_path or None,
            branch=branch,
        )
    else:
        inventory = inspect_attachment_inventory(
            Path(path),
            filename,
            limits=limits,
            traversal=traversal,
            attachments_dir=attachments_dir,
            mail_enabled=settings.mail_import_enabled,
            helper_path=settings.archive_7zip_path or None,
            branch=branch,
        )
    return inventory, recommend_scope(inventory, limits)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def confirm_processing_scope(
    doc_id, review_id, revision, request_id, *, actor, is_admin=False, similarity_token=None,
    edit_owner_id=None, edit_token=None
):
    settings = get_settings()
    registry = get_registry()
    doc = registry.get(doc_id)
    if not doc or doc.get("deleted_at"):
        raise ProcessingScopeConflict("Документ недоступен.")
    with session_scope() as session:
        prior = session.scalar(
            select(DocumentGeneration).where(DocumentGeneration.scope_request_id == request_id)
        )
        if prior is not None:
            snapshot = prior.processing_scope_json or {}
            if (
                prior.doc_id != doc_id
                or snapshot.get("review_id") != review_id
                or snapshot.get("review_revision") != revision
            ):
                raise ProcessingScopeConflict("Этот идентификатор подтверждения уже использован.")
            return ProcessingAdmissionResult("already_accepted", doc)
        row = locked_review(session, doc_id, review_id, revision)
        if row.state not in {"draft", "awaiting_similar", "validating", "submitted", "published"}:
            raise ProcessingScopeConflict("Выбор отменён. Создайте новый состав обработки.")
        if row.state == "submitted" and not row.generation_id:
            now = datetime.now(timezone.utc)
            lease = max(180, settings.parser_timeout_seconds + 60)
            if row.validation_started_at and now - _utc(row.validation_started_at) < timedelta(
                seconds=lease
            ):
                return ProcessingAdmissionResult("validating", doc)
            row.state = "draft"
        if row.state in {"submitted", "published"}:
            if row.request_id != request_id:
                raise ProcessingScopeConflict("Состав уже передан на обработку.")
            return ProcessingAdmissionResult("already_accepted", doc)
        now = datetime.now(timezone.utc)
        lease = max(180, settings.parser_timeout_seconds + 60)
        if (
            row.state == "validating"
            and row.validation_started_at
            and now - _utc(row.validation_started_at) < timedelta(seconds=lease)
        ):
            return ProcessingAdmissionResult("validating", doc)
        if edit_owner_id is not None:
            from app.services.processing_scope_edit_lock import require_edit_lock
            require_edit_lock(session, doc_id, edit_owner_id, edit_token)
        row.state = "validating"
        row.validation_token = token = uuid.uuid4().hex
        row.validation_started_at = now
        row.request_id = request_id
        row.confirmed_by = actor
        snapshot = snapshot_from_review(row)
        snapshot["confirmed_at"] = now.isoformat()
        consent = row.similarity_confirmation_json
    try:
        path = settings.uploads_dir / (doc_id + Path(doc["filename"]).suffix.lower())
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != snapshot["source_file_hash"]:
                raise ProcessingScopeConflict("Исходный файл изменился. Обновите состав.")
        duplicates = {"level2": [], "level3": []}
        if settings.dedup_enabled:
            with TemporaryDirectory(prefix="scope-preview-", dir=settings.uploads_dir) as folder:
                if settings.parser_supervisor_enabled:
                    parsed = parse_document_supervised(
                        path,
                        doc["filename"],
                        attachments_dir=folder,
                        timeout_seconds=settings.parser_timeout_seconds,
                        max_memory_mb=settings.parser_max_memory_mb,
                        max_concurrent=settings.parser_max_concurrent,
                        mail_enabled=settings.mail_import_enabled,
                        processing_policy=snapshot,
                        archive_helper_path=settings.archive_7zip_path or None,
                    )
                    blocks, sources = parsed
                else:
                    context = ParseContext(
                        doc["filename"],
                        mail_enabled=settings.mail_import_enabled,
                        processing_policy=ParserProcessingPolicy(snapshot),
                    )
                    context.archive_helper_path = settings.archive_7zip_path or None
                    blocks = parse_document(
                        path, doc["filename"], attachments_dir=folder, context=context
                    )
                    sources = context.sources
                from app.services.source_chunking import indexable_blocks
                from app.services.mail_identity import mail_fingerprint_from_parse
                from app.services.deduplication import find_duplicates_for_text

                markdown = blocks_to_markdown(indexable_blocks(blocks))
                fingerprint = mail_fingerprint_from_parse(sources, blocks, Path(folder))
        from app.services.deduplication import upload_admission_lock

        # Parse outside admission; recheck the current signatures immediately
        # before committing this request and admitting its generation.
        with upload_admission_lock():
            if settings.dedup_enabled:
                duplicates = find_duplicates_for_text(markdown, mail_fingerprint=fingerprint)
                duplicates = {
                    group: [item for item in values if item["doc"]["id"] != doc_id]
                    for group, values in duplicates.items()
                }
            if any(duplicates.values()):
                accepted = bool(
                    similarity_token
                    and consent
                    and consent.get("selection_hash") == snapshot["selection_hash"]
                    and consent.get("expires_at", 0) > datetime.now(timezone.utc).timestamp()
                    and secrets.compare_digest(
                        consent.get("token_hash", ""),
                        hashlib.sha256(similarity_token.encode()).hexdigest(),
                    )
                )
                if not accepted:
                    opaque = secrets.token_urlsafe(32)
                    with session_scope() as session:
                        row = locked_review(session, doc_id, review_id, revision)
                        if row.validation_token != token:
                            raise ProcessingScopeConflict()
                        row.state = "awaiting_similar"
                        row.validation_token = None
                        row.similarity_confirmation_json = {
                            "token_hash": hashlib.sha256(opaque.encode()).hexdigest(),
                            "selection_hash": snapshot["selection_hash"],
                            "expires_at": datetime.now(timezone.utc).timestamp() + 600,
                        }
                    return ProcessingAdmissionResult(
                        "similar",
                        detail={
                            "code": "similar_document",
                            "detail": "Найдены похожие документы. Подтвердите обработку.",
                            "duplicates": duplicates,
                            "review_id": review_id,
                            "revision": revision,
                            "selection_hash": snapshot["selection_hash"],
                            "similarity_token": opaque,
                        },
                    )
            with session_scope() as session:
                row = locked_review(session, doc_id, review_id, revision)
                if row.validation_token != token:
                    raise ProcessingScopeConflict()
                state = session.get(DocumentGenerationState, doc_id)
                if (state.active_generation_id if state else None) != row.base_generation_id:
                    raise ProcessingScopeConflict()
                row.state = "submitted"
                row.validation_token = None
                if settings.dedup_enabled:
                    from app.services.deduplication import prepare_document_signature

                    row.similarity_confirmation_json = {
                        **(row.similarity_confirmation_json or {}),
                        "reservation": {
                            **prepare_document_signature(markdown, fingerprint),
                            "selection_hash": snapshot["selection_hash"],
                        },
                    }
                append_scope_audit(
                    session, doc_id, actor, "document_processing_scope_confirmed", row
                )
            pipeline = get_pipeline()
            if doc.get("has_published_version"):
                pipeline.regenerate(
                    doc_id,
                    is_admin=is_admin,
                    processing_scope=snapshot,
                    scope_request_id=request_id,
                )
            else:
                pipeline.ingest(
                    doc_id,
                    path,
                    doc["filename"],
                    user_tags=doc.get("tags") or [],
                    is_admin=is_admin,
                    processing_scope=snapshot,
                    scope_request_id=request_id,
                )
            return ProcessingAdmissionResult("accepted", registry.get(doc_id))
    except Exception:
        with session_scope() as session:
            row = session.get(DocumentProcessingReview, doc_id)
            if (
                row
                and row.id == review_id
                and row.request_id == request_id
                and row.state in {"validating", "submitted"}
                and not row.generation_id
            ):
                row.state = "draft"
                row.validation_token = None
        raise


def prepare_regeneration_scope(doc_id, settings):
    """Return (immutable scope, requires user action) without occupying a worker."""
    from app.services.processing_scope import scope_for_generation, create_review
    from app.services.generation_files import processing_review_paths
    from app.db.models import Document
    from app.services.errors import DomainError
    import shutil

    doc = get_registry().get(doc_id)
    with session_scope() as session:
        review = session.get(DocumentProcessingReview, doc_id)
        if review and (
            review.state in {"draft", "awaiting_similar", "validating"}
            or (review.state == "submitted" and not review.generation_id)
        ):
            raise DomainError(
                "Выберите состав вложений документа.", code="processing_scope_review_required"
            )
        state = session.get(DocumentGenerationState, doc_id)
        active = state.active_generation_id if state else None
        snapshot = scope_for_generation(session, doc_id, active)
        if snapshot is None:
            candidate = state.candidate_generation_id if state else None
            if candidate:
                snapshot = scope_for_generation(session, doc_id, candidate)
            elif review and review.state == "submitted" and review.generation_id:
                snapshot = scope_for_generation(session, doc_id, review.generation_id)
    if snapshot is not None or not settings.attachment_review_enabled:
        return snapshot, False
    path = settings.uploads_dir / (doc_id + Path(doc["filename"]).suffix.lower())
    with TemporaryDirectory(prefix="scope-regenerate-", dir=settings.uploads_dir) as folder:
        inventory, selection = prepare_upload_scope(
            path, doc["filename"], settings, attachments_dir=Path(folder)
        )
        if not selection.requires_review:
            from app.services.processing_scope import automatic_snapshot

            return automatic_snapshot(inventory, assessment_limits(settings)), False
        with session_scope() as session:
            review = create_review(
                session,
                doc_id,
                inventory,
                base_generation_id=active,
                actor=doc.get("uploaded_by"),
                limits=assessment_limits(settings),
            )
            target = processing_review_paths(settings, doc_id, review.id)
            target.mkdir(parents=True, exist_ok=False)
            shutil.copytree(folder, target / "attachments")
            if not doc.get("has_published_version"):
                session.get(Document, doc_id).status = "awaiting_attachments"
    return None, True
