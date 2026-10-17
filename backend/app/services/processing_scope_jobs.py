"""Durable admission and execution of source-level publication changes."""
from concurrent.futures import ThreadPoolExecutor, Future
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import BoundedSemaphore, Event, Thread
import logging
import uuid

from sqlalchemy import select
from app.db.models import Document, DocumentGenerationState, DocumentProcessingReview, DocumentScopeChange
from app.db.session import session_scope
from app.services.processing_scope import (ProcessingScopeConflict, locked_review, snapshot_from_review)
from app.services.processing_scope_change import load_scope_base, analyze_scope_change, scope_base_digest, scope_sql_digest
from app.services.generation_store import begin_generation, lock_document_write

_fast_slots = BoundedSemaphore(1)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scope-reuse")
TERMINAL = {"published", "failed", "canceled"}
logger = logging.getLogger(__name__)


def _maintain_queued_owner(doc_id, plan_id, owner_token, pipeline):
    """Queue registration has an owner lease even before a worker is available."""
    from app.db.models import DocumentUpdateAttempt
    tick = Event()
    def heartbeat():
        while not tick.wait(20):
            try:
                with session_scope() as session:
                    if not lock_document_write(session, doc_id, allow_deleted=False):
                        return
                    row = session.get(DocumentScopeChange, plan_id, populate_existing=True)
                    if not row or row.owner_token != owner_token or row.state != "queued":
                        return
                    attempt = session.get(DocumentUpdateAttempt, doc_id, populate_existing=True)
                    canceled = bool(attempt and attempt.cancel_requested)
                    if not canceled:
                        row.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)
                if canceled:
                    fail_scope_change(doc_id, plan_id, owner_token)
                    with pipeline._start_lock:
                        future = pipeline._threads.get(doc_id)
                        if future and future.cancel():
                            pipeline._threads.pop(doc_id, None)
                            pipeline._pipeline_slots.release()
                    return
            except Exception:
                logger.warning("Scope queue ownership heartbeat stopped", exc_info=True)
                return
    Thread(target=heartbeat, daemon=True, name="scope-queue-owner").start()


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def _public(row):
    return {**deepcopy(row.plan_json["preview"]), "plan_id": row.id, "state": row.state,
            "candidate_generation_id": row.candidate_generation_id,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "result": deepcopy(row.result_json), "error_code": row.error_code}


def _check_base(session, base, expected, *, snapshot_locked=False):
    state = session.get(DocumentGenerationState, base.doc_id, populate_existing=True)
    if not state or state.active_generation_id != expected or state.candidate_generation_id:
        raise ProcessingScopeConflict()
    if base.generation_id != expected or base.candidate_id or base.status != "done":
        raise ProcessingScopeConflict()
    if base.current_file_hash is not None and base.current_file_hash != base.file_hash:
        raise ProcessingScopeConflict("Исходный файл изменился. Повторите анализ.", code="source_changed")
    if not snapshot_locked and base.canonical_digest and scope_sql_digest(session, base.doc_id) != base.canonical_digest:
        raise ProcessingScopeConflict()


class ScopeWorkerBusy(ValueError):
    pass


def _dispatch(doc_id, plan_id, owner_token):
    with session_scope() as session:
        mode = session.get(DocumentScopeChange, plan_id).plan_json["preview"]["mode"]
    if mode == "incremental_generate":
        from app.services.pipeline import get_pipeline
        pipeline = get_pipeline()
        with pipeline._start_lock:
            pipeline._threads[doc_id] = pipeline._executor.submit(_run, doc_id, plan_id, owner_token, pipeline=pipeline)
        _maintain_queued_owner(doc_id, plan_id, owner_token, pipeline)
    else:
        _executor.submit(_run, doc_id, plan_id, owner_token)


def create_change_preview(doc_id, review_id, revision, expected_active_generation_id, actor_id, *, edit_owner_id=None, edit_token=None):
    with session_scope() as session:
        if not lock_document_write(session, doc_id, allow_deleted=False):
            raise ProcessingScopeConflict()
        if edit_owner_id is not None:
            from app.services.processing_scope_edit_lock import require_edit_lock
            require_edit_lock(session, doc_id, edit_owner_id, edit_token)
        base = load_scope_base(doc_id,session=session)
        review = locked_review(session, doc_id, review_id, revision)
        _check_base(session, base, expected_active_generation_id,snapshot_locked=True)
        if review.state not in {"draft", "awaiting_similar"}:
            raise ProcessingScopeConflict()
        snapshot = snapshot_from_review(review)
        plan = analyze_scope_change(base, snapshot)
        row = DocumentScopeChange(id=uuid.uuid4().hex, doc_id=doc_id, created_by=actor_id,
            state="preview", expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            plan_json={"schema_version": 1, "preview": plan.to_dict(), "snapshot": snapshot,
                       "input_digest": scope_base_digest(base), "canonical_digest": base.canonical_digest,
                       "review_id": review_id, "revision": revision})
        session.add(row)
        session.flush()
        return _public(row)


def _replay_receipt(session, doc_id, plan_id, request_id, expected_active_generation_id, allowed_modes):
    replay = session.scalar(select(DocumentScopeChange).where(DocumentScopeChange.request_id == request_id)
                            .execution_options(populate_existing=True))
    if replay is None:
        return None
    if replay.id != plan_id or replay.doc_id != doc_id or (
        replay.plan_json["preview"]["base_generation_id"] != expected_active_generation_id
        or replay.plan_json["preview"]["mode"] not in allowed_modes
    ):
        raise ProcessingScopeConflict()
    return _public(replay)


def apply_scope_change(doc_id, plan_id, request_id, expected_active_generation_id, actor_id, allowed_modes, *, is_admin=False,
                       edit_owner_id=None, edit_token=None):
    # Replays are checked before worker capacity/base/review: they remain valid
    # after cleanup, later publications or a lost HTTP response.
    with session_scope() as session:
        replay = _replay_receipt(session, doc_id, plan_id, request_id, expected_active_generation_id, allowed_modes)
        if replay is not None:
            return replay
        pending_plan = session.get(DocumentScopeChange, plan_id)
        if not pending_plan or pending_plan.doc_id != doc_id:
            raise ProcessingScopeConflict()
        mode = pending_plan.plan_json["preview"]["mode"]
    pipeline = None
    if mode == "incremental_generate":
        from app.services.pipeline import get_pipeline
        pipeline = get_pipeline()
        with pipeline._start_lock:
            capacity = pipeline.settings.pipeline_max_workers + pipeline._queue_limit(is_admin)
            if doc_id in pipeline._threads or len(pipeline._threads) >= capacity or not pipeline._pipeline_slots.acquire(blocking=False):
                raise ScopeWorkerBusy("Очередь обработки занята. Повторите применение позже.")
            # Account for the reservation in the same FIFO/capacity as ordinary
            # processing before committing its durable queued operation.
            pipeline._threads[doc_id] = Future()
    elif not _fast_slots.acquire(blocking=False):
        raise ScopeWorkerBusy("Быстрый обработчик занят. Повторите применение позже.")
    admitted = False
    try:
        with session_scope() as session:
            if not lock_document_write(session, doc_id, allow_deleted=False):
                raise ProcessingScopeConflict()
            replay = _replay_receipt(session, doc_id, plan_id, request_id, expected_active_generation_id, allowed_modes)
            if replay is not None:
                return replay
            if edit_owner_id is not None:
                from app.services.processing_scope_edit_lock import require_edit_lock
                require_edit_lock(session, doc_id, edit_owner_id, edit_token)
            base = load_scope_base(doc_id,session=session)
            row = session.get(DocumentScopeChange, plan_id, populate_existing=True)
            if not row or row.doc_id != doc_id or row.state != "preview":
                raise ProcessingScopeConflict()
            value, plan = row.plan_json, row.plan_json["preview"]
            if plan["mode"] not in allowed_modes or plan["mode"] == "blocked":
                raise ProcessingScopeConflict("Для этого изменения требуется другое действие.")
            if _utc(row.expires_at) <= datetime.now(timezone.utc):
                raise ProcessingScopeConflict("Анализ устарел. Проверьте изменения повторно.")
            _check_base(session, base, expected_active_generation_id,snapshot_locked=True)
            review = locked_review(session, doc_id, value["review_id"], value["revision"])
            if review.state not in {"draft", "awaiting_similar"} or review.selection_hash != value["snapshot"]["selection_hash"]:
                raise ProcessingScopeConflict()
            if scope_base_digest(base) != value["input_digest"]:
                raise ProcessingScopeConflict("Результаты документа изменились. Повторите анализ.")
            row.request_id = request_id
            now = datetime.now(timezone.utc)
            review.confirmed_by, review.request_id = actor_id, request_id
            value = deepcopy(value)
            value["snapshot"].update(confirmed_by=actor_id, confirmed_at=now.isoformat())
            from app.services.processing_scope_cache import generation_vector_space
            value["snapshot"]["vector_space"] = base.vector_space or generation_vector_space(base.scope,base.generation_id)
            row.plan_json = value
            from app.services.processing_scope import append_scope_audit
            append_scope_audit(session, doc_id, actor_id, "document_processing_scope_confirmed", review)
            if plan["mode"] == "noop":
                row.state, row.finished_at = "published", datetime.now(timezone.utc)
                row.result_json = {"mode": "noop", "generation_id": base.generation_id}
                review.state = "published"
                return _public(row)
            from app.config import get_settings
            from app.services.document_update import capture_published_update
            capture_published_update(session, doc_id, get_settings())
            generation = begin_generation(session, doc_id, chunk_projection_version=base.rows.get("chunk_projection_version"))
            generation.processing_scope_json = deepcopy(value["snapshot"])
            generation.scope_request_id = request_id
            row.candidate_generation_id = generation.id
            row.owner_token, row.state = uuid.uuid4().hex, "queued" if pipeline else "admitted"
            row.started_at = datetime.now(timezone.utc)
            row.lease_expires_at = row.started_at + timedelta(seconds=60)
            review.state, review.generation_id, review.request_id = "submitted", generation.id, request_id
            session.get(Document, doc_id).status = "queued" if pipeline else "indexing"
            result, owner = _public(row), row.owner_token
        try:
            _dispatch(doc_id, plan_id, owner)
        except Exception:
            fail_scope_change(doc_id, plan_id, owner)
            raise
        admitted = True
        return result
    finally:
        if not admitted:
            if pipeline:
                with pipeline._start_lock:
                    pipeline._threads.pop(doc_id, None)
                    pipeline._pipeline_slots.release()
            else:
                _fast_slots.release()


def get_scope_change(doc_id, plan_id, actor_id):
    with session_scope() as session:
        row = session.get(DocumentScopeChange, plan_id)
        document = session.get(Document, doc_id)
        if not row or row.doc_id != doc_id or not document or document.deleted_at:
            raise ValueError("Scope change unavailable")
        return _public(row)


def _run(doc_id, plan_id, owner_token, *, pipeline=None):
    try:
        if pipeline:
            with pipeline._start_lock:
                pass  # Wait until dispatch replaced the reservation Future.
            with session_scope() as session:
                lock_document_write(session, doc_id, allow_deleted=False)
                row = session.get(DocumentScopeChange, plan_id)
                from app.db.models import DocumentUpdateAttempt
                attempt = session.get(DocumentUpdateAttempt, doc_id)
                if not row or row.owner_token != owner_token or row.state != "queued" or (attempt and attempt.cancel_requested):
                    raise ProcessingScopeConflict()
                row.state = "processing"
                row.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)
                session.get(Document, doc_id).status = "processing"
        from app.services.processing_scope_worker import execute_scope_change
        execute_scope_change(doc_id, plan_id, owner_token)
    except Exception:
        logger.warning("Scope change worker failed", exc_info=True)
        fail_scope_change(doc_id, plan_id, owner_token)
    finally:
        if pipeline:
            with pipeline._start_lock:
                pipeline._threads.pop(doc_id, None)
                pipeline._pipeline_slots.release()
        else:
            _fast_slots.release()


def fail_scope_change(doc_id, plan_id, owner_token):
    """Called only after the worker has stopped writing its own artifacts."""
    from app.db.models import DocumentUpdateAttempt
    from app.services.document_update import restore_canceled_update
    with session_scope() as session:
        lock_document_write(session, doc_id)
        row = session.get(DocumentScopeChange, plan_id, populate_existing=True)
        if not row or row.owner_token != owner_token or row.state in TERMINAL:
            return
        attempt = session.get(DocumentUpdateAttempt, doc_id)
        canceled = bool(attempt and attempt.cancel_requested)
        if attempt:
            attempt.cancel_requested = True
            restore_canceled_update(session, doc_id)
        row.state = "canceled" if canceled else "failed"
        row.error_code = "update_canceled" if canceled else "scope_change_failed"
        row.owner_token, row.lease_expires_at = None, None
        row.finished_at = datetime.now(timezone.utc)
        review = session.get(DocumentProcessingReview, doc_id)
        if review and review.id == row.plan_json["review_id"] and review.generation_id == row.candidate_generation_id:
            review.state, review.generation_id, review.request_id = "draft", None, None
            review.revision += 1


def recover_scope_changes():
    """Reclaim only expired owners under the same lock as a bounded write."""
    now, reclaimed = datetime.now(timezone.utc), 0
    with session_scope() as session:
        ids = list(session.scalars(select(DocumentScopeChange.id).where(
            DocumentScopeChange.state.in_(["admitted", "queued", "processing", "copying", "ready"]))))
    for plan_id in ids:
        recovery_owner = None
        with session_scope() as session:
            row = session.get(DocumentScopeChange, plan_id)
            if row is None:
                continue
            lock_document_write(session, row.doc_id)
            session.refresh(row)
            state = session.get(DocumentGenerationState, row.doc_id)
            if state and state.active_generation_id == row.candidate_generation_id:
                row.state = "published"
                row.result_json = row.result_json or {"mode": row.plan_json["preview"]["mode"], "generation_id": row.candidate_generation_id}
                row.owner_token = row.lease_expires_at = None
                row.finished_at = now
                continue
            if row.state not in {"admitted", "queued", "processing", "copying", "ready"} or (
                row.lease_expires_at and _utc(row.lease_expires_at) > now
            ):
                continue
            # Revoke the old token before releasing the lock. A late writer
            # cannot revive the lease or race cleanup on its next portion.
            recovery_owner = row.owner_token = uuid.uuid4().hex
            doc_id = row.doc_id
        if recovery_owner:
            fail_scope_change(doc_id, plan_id, recovery_owner)
            reclaimed += 1
    return reclaimed
