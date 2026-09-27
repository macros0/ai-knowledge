"""Durable cancellation and restoration of the still-published document version.

All transitions take the same Document writer lock as publication. Files and
points may be cleaned only after the worker has stopped; cancellation alone
does not make a candidate safe for cleanup.
"""
import uuid

from sqlalchemy import func, select

from app import error_codes as codes
from app.db.models import Document, DocumentChunk, DocumentGenerationState, DocumentStaging, DocumentUpdateAttempt
from app.services.errors import ConflictError, NotFoundError
from app.services.generation_store import GenerationConflict, abandon_generation, lock_document_write


PUBLISHED_FIELDS = (
    "okf_concept_count", "total_chunks", "processed_chunks", "current_chunk",
    "problem", "parser_version", "parse_warnings",
)


class DocumentUpdateCancelled(GenerationConflict):
    """An update cannot publish after the cancellation transaction won the lock."""


def capture_published_update(session, doc_id: str, settings, *, fresh: bool = False) -> bool:
    """Snapshot before changing progress; preserve it across retries and restarts."""
    if not lock_document_write(session, doc_id, allow_deleted=False):
        raise NotFoundError("Документ не найден", code=codes.DOCUMENT_NOT_FOUND)
    attempt = session.get(DocumentUpdateAttempt, doc_id, populate_existing=True)
    if attempt:
        if attempt.cancel_requested:
            raise ConflictError("Обновление останавливается", code=codes.PROCESSING_STOPPING)
        if fresh:
            attempt.id = uuid.uuid4().hex
        return False
    document = session.get(Document, doc_id, populate_existing=True)
    state = session.get(DocumentGenerationState, doc_id, populate_existing=True)
    active_id = state.active_generation_id if state else None
    if document.status != "done" and not active_id:
        return False  # First upload has no published result to restore.
    fields = {key: getattr(document, key) for key in PUBLISHED_FIELDS}
    if document.status != "done":
        # Updates interrupted before this feature was installed have no snapshot.
        # Recover completion diagnostics from the verified active publication.
        from app.db.models import DocumentGeneration
        from app.services.generation_artifacts import load_verified_publication

        active = session.get(DocumentGeneration, active_id)
        publication = load_verified_publication(settings, doc_id, active_id, active.publication_hash)
        fields.update({key: value for key, value in publication["document_fields"].items() if key in PUBLISHED_FIELDS})
        fields["total_chunks"] = session.scalar(select(func.count()).select_from(DocumentChunk).where(
            DocumentChunk.doc_id == doc_id,
        ))
        fields["processed_chunks"] = fields["total_chunks"]
        fields["current_chunk"] = None
    session.add(DocumentUpdateAttempt(
        doc_id=doc_id, id=uuid.uuid4().hex, base_generation_id=active_id,
        previous_fields=fields, cancel_requested=False,
    ))
    return True


def request_update_cancel(session, doc_id: str, expected_update_id: str | None = None) -> None:
    if not lock_document_write(session, doc_id, allow_deleted=False):
        raise NotFoundError("Документ не найден", code=codes.DOCUMENT_NOT_FOUND)
    attempt = session.get(DocumentUpdateAttempt, doc_id, populate_existing=True)
    state = session.get(DocumentGenerationState, doc_id, populate_existing=True)
    active_id = state.active_generation_id if state else None
    if (not attempt or attempt.base_generation_id != active_id
            or (expected_update_id is not None and attempt.id != expected_update_id)):
        raise ConflictError("Нет текущего обновления для отмены", code=codes.DOCUMENT_UPDATE_CONFLICT)
    attempt.cancel_requested = True


def restore_canceled_update(session, doc_id: str) -> bool:
    """Called only after worker exit (or at startup, before serving requests)."""
    if not lock_document_write(session, doc_id, allow_deleted=False):
        return False
    attempt = session.get(DocumentUpdateAttempt, doc_id, populate_existing=True)
    if not attempt or not attempt.cancel_requested:
        return False
    state = session.get(DocumentGenerationState, doc_id, populate_existing=True)
    if attempt.base_generation_id != (state.active_generation_id if state else None):
        raise GenerationConflict("Published version changed during cancellation")
    if state and state.candidate_generation_id:
        abandon_generation(session, doc_id, state.candidate_generation_id)
    document = session.get(Document, doc_id, populate_existing=True)
    for key, value in attempt.previous_fields.items():
        if key in PUBLISHED_FIELDS:
            setattr(document, key, value)
    document.status = "done"
    document.error = document.error_code = None
    checkpoint = session.get(DocumentStaging, doc_id)
    if checkpoint:
        session.delete(checkpoint)
    session.delete(attempt)
    return True
