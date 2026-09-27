"""Transactional publication state for immutable document generations.

Callers own the transaction. Call publish_generation before replacing canonical
rows, in that same transaction: this acquires the Document lock before row
locks on sources, chunks, concepts and attachments and avoids lock inversion.
This module deliberately does not commit, write files or contact Qdrant.
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import select, update

from app.db.models import Document, DocumentGeneration, DocumentGenerationState, DocumentUpdateAttempt


class GenerationConflict(ValueError):
    """The requested transition is no longer valid for this document."""


def get_generation_state(session, doc_id: str) -> DocumentGenerationState | None:
    return session.get(DocumentGenerationState, doc_id, populate_existing=True)


def lock_generation_read(session, doc_ids: list[str] | None = None) -> dict[str, str | None]:
    """Keep canonical rows and their generation IDs consistent during batch reads."""
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
    elif connection.dialect.name == "postgresql":
        statement = select(Document.id).order_by(Document.id).with_for_update(read=True)
        if doc_ids is not None:
            statement = statement.where(Document.id.in_(doc_ids))
        session.execute(statement).all()
    statement = select(DocumentGenerationState.doc_id, DocumentGenerationState.active_generation_id)
    if doc_ids is not None:
        statement = statement.where(DocumentGenerationState.doc_id.in_(doc_ids))
    return dict(session.execute(statement).all())


def lock_document_write(session, doc_id: str, *, allow_deleted: bool = True) -> bool:
    """Serialize metadata/canonical writers before reading their document state."""
    session.flush()
    # A no-op UPDATE takes a write lock on both PostgreSQL and SQLite. SQLite
    # ignores SELECT FOR UPDATE, so a read-before-write lock would admit races.
    conditions = [Document.id == doc_id]
    if not allow_deleted:
        conditions.append(Document.deleted_at.is_(None))
    result = session.execute(
        update(Document)
        .where(*conditions)
        .values(updated_at=Document.updated_at)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def _locked_state(session, doc_id: str, *, allow_deleted: bool = False) -> DocumentGenerationState:
    if not lock_document_write(session, doc_id, allow_deleted=allow_deleted):
        raise GenerationConflict("Document is missing or deleted")
    state = get_generation_state(session, doc_id)
    if state is None:
        state = DocumentGenerationState(doc_id=doc_id)
        session.add(state)
        session.flush()
    return state


def _generation(session, doc_id: str, generation_id: str) -> DocumentGeneration:
    generation = session.scalar(
        select(DocumentGeneration)
        .where(DocumentGeneration.id == generation_id, DocumentGeneration.doc_id == doc_id)
        .execution_options(populate_existing=True)
    )
    if generation is None:
        raise GenerationConflict("Generation does not belong to this document")
    return generation


def begin_generation(session, doc_id: str) -> DocumentGeneration:
    state = _locked_state(session, doc_id)
    if state.candidate_generation_id is not None:
        raise GenerationConflict("Document already has a candidate generation")
    generation = DocumentGeneration(
        id=uuid.uuid4().hex,
        doc_id=doc_id,
        base_generation_id=state.active_generation_id,
        phase="preparing",
    )
    session.add(generation)
    state.candidate_generation_id = generation.id
    session.flush()
    return generation


def prepare_generation_attempt(session, doc_id: str, *, resume: bool) -> DocumentGeneration:
    """Resume a candidate, or replace only an unpublished attempt on regenerate."""
    state = _locked_state(session, doc_id)
    if state.candidate_generation_id:
        generation = _generation(session, doc_id, state.candidate_generation_id)
        if resume:
            return generation
        abandon_generation(session, doc_id, generation.id)
    return begin_generation(session, doc_id)


def mark_generation_ready(session, doc_id: str, generation_id: str, *, publication_hash: str | None = None) -> None:
    """Called only after all immutable files and search points were verified."""
    state = _locked_state(session, doc_id)
    generation = _generation(session, doc_id, generation_id)
    if state.candidate_generation_id != generation_id or generation.phase not in {"preparing", "ready"}:
        raise GenerationConflict("Generation is not the current preparing candidate")
    generation.phase = "ready"
    generation.publication_hash = publication_hash
    session.flush()


def publish_generation(session, doc_id: str, generation_id: str) -> bool:
    state = _locked_state(session, doc_id)
    attempt = session.get(DocumentUpdateAttempt, doc_id, populate_existing=True)
    if attempt and attempt.cancel_requested:
        from app.services.document_update import DocumentUpdateCancelled

        raise DocumentUpdateCancelled("Document update was canceled")
    generation = _generation(session, doc_id, generation_id)
    if state.active_generation_id == generation_id and generation.phase == "active":
        return False  # The caller must not replay canonical writes either.
    if state.candidate_generation_id != generation_id or generation.phase != "ready":
        raise GenerationConflict("Only the current ready generation can be published")
    if generation.base_generation_id != state.active_generation_id:
        raise GenerationConflict("The active generation changed after this attempt began")
    if state.active_generation_id is not None:
        previous = _generation(session, doc_id, state.active_generation_id)
        if previous.phase != "active":
            raise GenerationConflict("The previous generation is not active")
        previous.phase = "retired"
    else:
        generation.legacy_cleanup_pending = True
    generation.phase = "active"
    generation.activated_at = datetime.now(timezone.utc)
    state.active_generation_id = generation_id
    state.candidate_generation_id = None
    session.flush()
    return True


def abandon_generation(session, doc_id: str, generation_id: str) -> None:
    state = _locked_state(session, doc_id)
    generation = _generation(session, doc_id, generation_id)
    if generation.phase == "abandoned" and state.candidate_generation_id != generation_id:
        return
    if state.candidate_generation_id != generation_id or generation.phase not in {"preparing", "ready"}:
        raise GenerationConflict("Only a non-active candidate can be abandoned")
    generation.phase = "abandoned"
    state.candidate_generation_id = None
    session.flush()


def lock_writable_generation(session, doc_id: str, generation_id: str) -> DocumentGeneration:
    """Hold the document lock while preparing a candidate's file directories."""
    state = _locked_state(session, doc_id)
    generation = _generation(session, doc_id, generation_id)
    if state.candidate_generation_id != generation_id or generation.phase != "preparing":
        raise GenerationConflict("Only the current preparing generation is writable")
    return generation


def lock_cleanup_generation(
    session, doc_id: str, generation_id: str, *, missing_ok: bool = False,
) -> DocumentGeneration | None:
    """Hold the document lock until cleanup finishes; never delete live artifacts."""
    state = _locked_state(session, doc_id, allow_deleted=True)
    generation = session.get(DocumentGeneration, generation_id, populate_existing=True)
    if generation is None and missing_ok:
        return None
    if generation is None or generation.doc_id != doc_id:
        raise GenerationConflict("Generation does not belong to this document")
    if (
        generation_id in {state.active_generation_id, state.candidate_generation_id}
        or generation.phase not in {"retired", "abandoned"}
    ):
        raise GenerationConflict("Only a retired or abandoned generation can be cleaned")
    return generation


def lock_legacy_cleanup(session, doc_id: str) -> list[DocumentGeneration]:
    """Legacy artifacts can retire only after an active generation was committed."""
    state = _locked_state(session, doc_id, allow_deleted=True)
    if not state.active_generation_id:
        raise GenerationConflict("Cannot clean legacy artifacts before publication")
    active = _generation(session, doc_id, state.active_generation_id)
    if active.phase != "active":
        raise GenerationConflict("The publication pointer is inconsistent")
    return list(session.scalars(select(DocumentGeneration).where(
        DocumentGeneration.doc_id == doc_id,
        DocumentGeneration.legacy_cleanup_pending.is_(True),
    )))
