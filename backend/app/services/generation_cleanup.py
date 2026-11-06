"""Retryable removal of retired artifacts after SQL publication.

Qdrant is acknowledged before retiring durable bindings. Work generations keep
exclusion identity after cleanup because an old process can still write points.
An accepted checkpoint protects its source until an explicit handoff/retirement.
"""
import logging

from sqlalchemy import or_, select

from app.db.models import DocumentGeneration, DocumentGenerationState, DocumentSource, OkfAttachment
from app.db.session import session_scope
from app.services.generation_files import cleanup_generation_files, cleanup_legacy_files
from app.services.generation_store import GenerationConflict, lock_cleanup_generation, lock_legacy_cleanup


def run_cleanup_loop(stop_event, cleanup, *, interval: float = 60.0) -> None:
    """Discover durable work on startup and retry until application shutdown."""
    while not stop_event.is_set():
        try:
            from app.services.processing_scope_jobs import recover_scope_changes
            recover_scope_changes()
            from app.config import get_settings
            from app.services.processing_scope import recover_processing_reviews
            recover_processing_reviews(get_settings())
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
                from app.db.models import ArtifactBinding, Document
                from sqlalchemy import delete
                session.execute(delete(ArtifactBinding).where(ArtifactBinding.owner_type == "source", ArtifactBinding.owner_id == doc_id,
                    ArtifactBinding.generation_id == "", ArtifactBinding.logical_name.startswith("legacy/")))
                document = session.get(Document, doc_id)
                document.legacy_artifacts_id, document.legacy_artifacts_version = None, 1
                for generation in generations:
                    generation.legacy_cleanup_pending = False

    return _cleanup_retired_generations(settings, vector_store, doc_id)


def _cleanup_retired_generations(settings, vector_store, doc_id: str) -> int:
    from app.db.models import DocumentStaging
    with session_scope() as session:
        retired = list(session.scalars(select(DocumentGeneration.id).where(
            DocumentGeneration.doc_id == doc_id, DocumentGeneration.phase.in_(["retired", "abandoned"]),
            or_(DocumentGeneration.work_id.is_(None), DocumentGeneration.work_cleanup_done.is_(False)),
            ~select(DocumentStaging.doc_id).where(DocumentStaging.generation_id == DocumentGeneration.id).exists(),
        )))
    removed = 0
    for generation_id in retired:
        with session_scope() as session:
            candidate = session.get(DocumentGeneration, generation_id)
            if candidate is not None and candidate.work_id is not None:
                from app.db.models import WorkItem
                owner = session.scalar(select(WorkItem.id).where(WorkItem.id == candidate.work_id).with_for_update())
                if owner is None:
                    raise GenerationConflict("Work generation ownership record is missing")
            # Check existence only after taking the writer lock: another worker
            # may already have removed this row since the initial snapshot.
            generation = lock_cleanup_generation(session, doc_id, generation_id, missing_ok=True)
            if generation is None:
                continue
            vector_store.delete_generation_points(doc_id, generation_id)
            cleanup_generation_files(session, settings, doc_id, generation_id)
            from app.db.models import ArtifactBinding, CheckpointBinding, SourceCacheHead
            for model in (SourceCacheHead, ArtifactBinding, CheckpointBinding):
                session.query(model).filter(model.doc_id == doc_id, model.generation_id == generation_id).delete(synchronize_session=False)
            if generation.work_id is None:
                session.delete(generation)
            else:
                # Late external writes can outlive a process/lease. Keep exclusion identity.
                generation.migration_artifacts_id, generation.migration_artifacts_version = None, 1
                generation.work_cleanup_done = True
        removed += 1
    return removed
