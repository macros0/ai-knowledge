"""Retryable removal of retired artifacts after SQL publication.

Qdrant is acknowledged before deleting the durable generation record, so an
unfinished cleanup remains excluded from search and discoverable for retry.
"""
import logging

from sqlalchemy import select

from app.db.models import DocumentGeneration, DocumentGenerationState, DocumentSource, OkfAttachment
from app.db.session import session_scope
from app.services.generation_files import cleanup_generation_files, cleanup_legacy_files
from app.services.generation_store import GenerationConflict, lock_cleanup_generation, lock_legacy_cleanup


def run_cleanup_loop(stop_event, cleanup, *, interval: float = 60.0) -> None:
    """Discover durable work on startup and retry until application shutdown."""
    while not stop_event.is_set():
        try:
            cleanup()
        except Exception:
            logging.getLogger(__name__).warning("Очистка старых версий будет повторена", exc_info=True)
        stop_event.wait(interval)


def cleanup_document_generations(settings, vector_store, doc_id: str) -> int:
    with session_scope() as session:
        pending = session.scalar(select(DocumentGeneration.id).where(
            DocumentGeneration.doc_id == doc_id, DocumentGeneration.legacy_cleanup_pending.is_(True),
        ).limit(1))
    if pending:
        with session_scope() as session:
            generations = lock_legacy_cleanup(session, doc_id)
            if generations:
                paths = list(session.scalars(select(OkfAttachment.saved_path).where(OkfAttachment.doc_id == doc_id)))
                paths += list(session.scalars(select(DocumentSource.saved_path).where(DocumentSource.doc_id == doc_id)))
                if any(path and not path.replace("\\", "/").startswith("generations/") for path in paths):
                    raise GenerationConflict("Published content still references legacy artifacts")
                state = session.get(DocumentGenerationState, doc_id)
                vector_store.delete_generation_points(doc_id, None)
                cleanup_legacy_files(settings, doc_id, state.active_generation_id)
                for generation in generations:
                    generation.legacy_cleanup_pending = False

    return _cleanup_retired_generations(settings, vector_store, doc_id)


def _cleanup_retired_generations(settings, vector_store, doc_id: str) -> int:
    with session_scope() as session:
        retired = list(session.scalars(select(DocumentGeneration.id).where(
            DocumentGeneration.doc_id == doc_id, DocumentGeneration.phase.in_(["retired", "abandoned"]),
        )))
    removed = 0
    for generation_id in retired:
        with session_scope() as session:
            # Check existence only after taking the writer lock: another worker
            # may already have removed this row since the initial snapshot.
            generation = lock_cleanup_generation(session, doc_id, generation_id, missing_ok=True)
            if generation is None:
                continue
            vector_store.delete_generation_points(doc_id, generation_id)
            cleanup_generation_files(session, settings, doc_id, generation_id)
            session.delete(generation)
        removed += 1
    return removed
