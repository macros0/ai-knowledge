"""Bounded index maintenance of an explicitly frozen active generation."""
from sqlalchemy import select

from app.db.models import Document, DocumentGenerationState, WorkItem
from app.db.session import session_scope
from app.services.generation_store import lock_generation_read
from .context import coordinator_session, work_execution
from .handlers import WorkDeferred, TransientWorkError
from .models import WorkSpec, LostOwnership
from .progress import load_progress, save_progress
from .store import enqueue

MODES = ("sparse", "relations", "chunks")


class IndexSuperseded(RuntimeError):
    pass


def assert_index_generation(doc_id, generation_id):
    """Call after taking the document read lock, including after embedding."""
    from .context import current_claim
    work = current_claim()
    if work is not None and work.spec.kind == "index_backfill":
        if doc_id != work.spec.doc_id or generation_id != work.spec.payload["generation_id"]:
            raise IndexSuperseded()


def sweep_indexes(work):
    if work.spec.payload:
        raise ValueError("Invalid index sweep input")
    with coordinator_session(work) as session:
        cursor = load_progress(session, work, version=1).get("last_id", "")
        if not isinstance(cursor, str) or len(cursor) > 64:
            raise ValueError("Invalid index sweep cursor")
        rows = session.execute(select(Document.id, DocumentGenerationState.active_generation_id)
            .outerjoin(DocumentGenerationState, DocumentGenerationState.doc_id == Document.id)
            .where(Document.id > cursor).order_by(Document.id).limit(100)).all()
        for doc_id, generation_id in rows:
            # Legacy identities require offline adoption before distributed activation.
            if generation_id is None:
                continue
            for mode in MODES:
                pending = session.scalar(select(WorkItem.id).where(WorkItem.kind == "index_backfill",
                    WorkItem.doc_id == doc_id, WorkItem.status.in_(["queued", "running"]),
                    WorkItem.payload_json["mode"].as_string() == mode).limit(1).with_for_update())
                if pending is None:
                    enqueue(session, WorkSpec("index_backfill", 1, {"doc_id": doc_id, "generation_id": generation_id, "mode": mode},
                        f"index-backfill/{work.work_id}/{doc_id}/{mode}", doc_id))
        if rows:
            save_progress(session, work, version=1, state={"last_id": rows[-1][0]})
    return WorkDeferred(1) if len(rows) == 100 else None


def backfill_index(work):
    from app.services.pipeline import get_pipeline
    from app.services.work.maintenance import _validate_ids
    from app.services.errors import public_error_code
    from app import error_codes as codes
    from app.services.artifacts.contract import BlobUnavailable
    import httpx
    payload = work.spec.payload
    if set(payload) != {"doc_id", "generation_id", "mode"} or payload["doc_id"] != work.spec.doc_id or payload["mode"] not in MODES:
        raise ValueError("Invalid index work input")
    _validate_ids(payload["doc_id"], payload["generation_id"])
    with work_execution(work):
        try:
            pipeline = get_pipeline()
            with session_scope() as session:
                active = lock_generation_read(session, [work.spec.doc_id]).get(work.spec.doc_id)
                document = session.get(Document, work.spec.doc_id)
                if document is None or active != payload["generation_id"]:
                    return  # Publication/purge superseded this derived index intent.
                if payload["mode"] != "sparse" and document.deleted_at is not None:
                    return
                if payload["mode"] == "chunks" and document.status != "done":
                    return
            if payload["mode"] == "chunks":
                pipeline.vector_store.backfill_chunks(pipeline.embedder, doc_id=work.spec.doc_id)
            elif payload["mode"] == "sparse":
                pipeline.vector_store.backfill_sparse(doc_id=work.spec.doc_id)
            else:
                pipeline.vector_store.backfill_relations(doc_id=work.spec.doc_id)
        except IndexSuperseded:
            return
        except LostOwnership:
            raise
        except Exception as exc:
            if isinstance(exc, (BlobUnavailable, httpx.TransportError, ConnectionError, TimeoutError)) or public_error_code(exc) in {
                    codes.DEPENDENCY_UNAVAILABLE, codes.TIMEOUT, codes.RATE_LIMITED}:
                raise TransientWorkError("storage_unavailable") from exc
            raise


def index_scope(settings, mode, doc_id):
    """Old whole-corpus entry points cannot bypass future ownership."""
    from .admission import durable_documents
    from .context import current_claim
    if durable_documents(settings):
        work = current_claim()
        if (work is None or work.spec.kind != "index_backfill" or work.spec.payload_version != 1
                or doc_id is None or work.spec.doc_id != doc_id or work.spec.payload.get("mode") != mode):
            raise LostOwnership("Distributed backfill requires its scoped work claim")
