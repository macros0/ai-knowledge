"""Four-eyes public Job transitions admit private work in the same transaction."""
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db.models import Job, WorkItem
from app.db.session import session_scope
from app.services.job_queue import JobQueue, SelfApprovalError

CREATOR = SimpleNamespace(user_id="creator", username="creator")
APPROVER = SimpleNamespace(user_id="approver", username="approver")


@pytest.fixture
def durable_queue(monkeypatch):
    settings = Settings(_env_file=None, approval_threshold_docs_delete=2)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    monkeypatch.setattr("app.services.job_queue.get_settings", lambda: settings)
    return JobQueue(start_worker=False), settings


def test_bulk_admission_enqueues_private_work_without_local_execution(durable_queue):
    queue, _ = durable_queue
    job = queue.submit("bulk_resume", ["a" * 16], CREATOR)
    assert job["status"] == "queued" and queue._queue.empty()
    with session_scope() as session:
        work = session.query(WorkItem).one()
        assert work.kind == "bulk" and work.payload_json == {"job_id": job["id"]}


def test_four_eyes_approval_is_required_before_private_work(durable_queue):
    queue, _ = durable_queue
    job = queue.submit("bulk_delete", ["a" * 16, "b" * 16], CREATOR)
    assert job["status"] == "awaiting_approval"
    with pytest.raises(SelfApprovalError):
        queue.approve(job["id"], CREATOR)
    with session_scope() as session:
        assert session.query(WorkItem).count() == 0
    result = queue.approve(job["id"], APPROVER)
    assert result["status"] == "queued" and queue._queue.empty()
    with session_scope() as session:
        assert session.query(WorkItem).one().payload_json == {"job_id": job["id"]}


def test_approval_audit_failure_rolls_back_work_and_public_status(durable_queue, monkeypatch):
    queue, _ = durable_queue
    job = queue.submit("bulk_delete", ["a" * 16, "b" * 16], CREATOR)
    def fail(*_a, **_k):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr("app.services.job_queue.audit_mod.record_in_session", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        queue.approve(job["id"], APPROVER)
    with session_scope() as session:
        assert session.get(Job, job["id"]).status == "awaiting_approval"
        assert session.query(WorkItem).count() == 0


def test_job_restart_recovery_cannot_fail_another_workers_job(durable_queue):
    queue, _ = durable_queue
    job = queue.submit("bulk_resume", ["a" * 16], CREATOR)
    with session_scope() as session:
        session.get(Job, job["id"]).status = "running"
    queue.recover_after_restart()
    assert queue.get(job["id"])["status"] == "running"


def test_bulk_cancel_revokes_private_work_in_same_transaction(durable_queue):
    queue, _ = durable_queue
    job = queue.submit("bulk_resume", ["a" * 16], CREATOR)
    assert queue.cancel(job["id"], CREATOR)["status"] == "cancelled"
    with session_scope() as session:
        work = session.query(WorkItem).one()
        assert work.cancel_requested and work.status == "cancelled"


def test_cancel_audit_failure_keeps_job_and_work_queued(durable_queue, monkeypatch):
    queue, _ = durable_queue
    job = queue.submit("bulk_resume", ["a" * 16], CREATOR)
    def fail(*_a, **_k):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr("app.services.job_queue.audit_mod.record_in_session", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        queue.cancel(job["id"], CREATOR)
    with session_scope() as session:
        assert session.get(Job, job["id"]).status == "queued"
        work = session.query(WorkItem).one()
        assert work.status == "queued" and not work.cancel_requested
