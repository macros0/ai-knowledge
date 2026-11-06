"""Bulk coordinators yield a single execution slot to durable document work."""
from datetime import datetime, timedelta, timezone
from threading import Event

from app.db.models import WorkItem
from app.db.session import session_scope
from app.services.job_queue import JobQueue
from app.services.work.runtime import build_handlers
from app.services.work.worker import Worker
from tests.test_generation_pipeline import DOC_ID, pipeline_env as pipeline_env
from tests.test_work_document_handler import durable_original
from tests.test_work_bulk_admission import CREATOR
import pytest


@pytest.fixture
def bulk_runtime(pipeline_env, tmp_path, monkeypatch, request):
    pipeline, _, _ = pipeline_env
    durable_original(pipeline_env, tmp_path)
    settings = pipeline.settings
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "worker"
    settings.approval_threshold_docs_regenerate = 999
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    monkeypatch.setattr("app.services.job_queue.get_settings", lambda: settings)
    queue = JobQueue(start_worker=False)
    monkeypatch.setattr("app.services.job_queue.get_job_queue", lambda: queue)
    job = queue.submit(getattr(request, "param", "bulk_regenerate"), [DOC_ID], CREATOR)
    worker = Worker(settings, registry=build_handlers(), owner_id="single")
    return worker, queue, job


def test_bulk_does_not_deadlock_single_worker(bulk_runtime):
    worker, queue, job = bulk_runtime
    assert worker.run_once(Event())  # Parent creates child coordinators and releases its slot.
    assert worker.run_once(Event())  # Child admits document work and yields.
    assert queue.get(job["id"])["status"] == "running"
    assert worker.run_once(Event())  # Same worker can now run the actual document handler.
    with session_scope() as session:
        assert session.query(WorkItem).filter_by(kind="document").one().status == "succeeded"
        item = session.query(WorkItem).filter_by(kind="bulk_item").one()
        item.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert worker.run_once(Event())
    result = queue.get(job["id"])
    assert result["status"] == "completed"
    assert result["result"]["results"] == [{"doc_id": DOC_ID, "ok": True}]
    assert result["result"]["errors"] == []


def test_bulk_reconciles_failed_item_after_admission_audit_rollback(bulk_runtime, monkeypatch):
    from app.services.work.models import WorkSpec
    from app.services.work.store import enqueue
    worker, queue, job = bulk_runtime
    assert worker.run_once(Event())
    def fail(*_a, **_k):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr("app.services.audit.record_in_session", fail)
    assert worker.run_once(Event())
    with session_scope() as session:
        assert session.query(WorkItem).filter_by(kind="document").count() == 0
        assert session.query(WorkItem).filter_by(kind="bulk_item").one().status == "failed"
        enqueue(session, WorkSpec("bulk_reconcile", 1, {}, "reconcile"))
    assert worker.run_once(Event())
    result = queue.get(job["id"])
    assert result["status"] == "failed"
    assert result["result"]["errors"][0]["error_code"] == "handler_failed"


def test_future_delete_rollback_cannot_change_qdrant_visibility(pipeline_env, tmp_path):
    from qdrant_client import models as qm
    pipeline, _, _ = pipeline_env
    durable_original(pipeline_env, tmp_path)
    pipeline.settings.okf_deployment_schema, pipeline.settings.app_role = "replicas-v1", "api"
    from app.services.work.context import coordinator_session
    from app.services.work.models import WorkSpec
    from app.services.work.store import enqueue, claim
    with session_scope() as session:
        enqueue(session, WorkSpec("bulk_item", 1, {}, "delete-rollback"))
        work = claim(session, owner_id="worker", supported={"bulk_item": {1}}, lease_seconds=120)
    with pytest.raises(RuntimeError), coordinator_session(work):
        pipeline.soft_delete(DOC_ID, deleted_by="admin")
        raise RuntimeError("audit rollback")
    assert pipeline.registry.get(DOC_ID)["deleted_at"] is None
    points, _ = pipeline.vector_store.client.scroll(collection_name=pipeline.vector_store.collection,
        scroll_filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=DOC_ID))]),
        with_payload=True, limit=100)
    assert points and all(not point.payload.get("deleted") for point in points)
    with session_scope() as session:
        assert session.query(WorkItem).filter_by(kind="document_visibility").count() == 0


def test_visibility_work_uses_latest_state_after_restore(pipeline_env, tmp_path, monkeypatch):
    from qdrant_client import models as qm
    from app.services.work.store import claim
    pipeline, _, _ = pipeline_env
    durable_original(pipeline_env, tmp_path)
    pipeline.settings.okf_deployment_schema, pipeline.settings.app_role = "replicas-v1", "worker"
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    pipeline.soft_delete(DOC_ID)
    pipeline.restore(DOC_ID)
    with session_scope() as session:
        assert session.query(WorkItem).filter_by(kind="document_visibility").count() == 2
        work = claim(session, owner_id="visibility", supported={"document_visibility": {1}}, lease_seconds=120)
    build_handlers().resolve(work.spec)(work)
    points, _ = pipeline.vector_store.client.scroll(collection_name=pipeline.vector_store.collection,
        scroll_filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=DOC_ID))]),
        with_payload=True, limit=100)
    assert points and all(not point.payload.get("deleted") for point in points)


@pytest.mark.parametrize("bulk_runtime", ["bulk_delete"], indirect=True)
def test_bulk_delete_cannot_report_success_for_missing_document(bulk_runtime):
    from app.db.models import Document
    worker, queue, job = bulk_runtime
    with session_scope() as session:
        session.delete(session.get(Document, DOC_ID))
    assert worker.run_once(Event())
    assert worker.run_once(Event())
    result = queue.get(job["id"])
    assert result["status"] == "failed"
    assert result["result"]["errors"][0]["error_code"] == "document_not_found"


def test_visibility_transport_outage_is_retryable(pipeline_env, tmp_path, monkeypatch):
    from app.services.errors import VectorStoreError
    from app.services.work.handlers import TransientWorkError
    from app.services.work.store import claim
    pipeline, _, _ = pipeline_env
    durable_original(pipeline_env, tmp_path)
    pipeline.settings.okf_deployment_schema, pipeline.settings.app_role = "replicas-v1", "worker"
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    pipeline.soft_delete(DOC_ID)
    with session_scope() as session:
        work = claim(session, owner_id="visibility", supported={"document_visibility": {1}}, lease_seconds=120)
    def unavailable(*_a, **_k):
        raise VectorStoreError(cause=ConnectionError("offline"))
    monkeypatch.setattr(pipeline.vector_store, "set_document_deleted", unavailable)
    with pytest.raises(TransientWorkError, match="storage_unavailable"):
        build_handlers().resolve(work.spec)(work)
