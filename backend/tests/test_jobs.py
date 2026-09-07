"""Тесты очереди массовых операций (jobs): four-eyes, circuit breaker, статусы."""
import pytest

from app.config import Settings
from app.services import job_queue as jq
from app.services.job_queue import (
    BULK_DELETE,
    BULK_REGENERATE,
    STATUS_AWAITING_APPROVAL,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    JobQueue,
    QueueOverloadedError,
    SelfApprovalError,
)
from app.services.registry import DocumentRegistry


class _User:
    def __init__(self, user_id="u-admin", username="demo.admin"):
        self.user_id = user_id
        self.username = username


@pytest.fixture
def q():
    return JobQueue(start_worker=False)


@pytest.fixture
def make_docs():
    reg = DocumentRegistry()
    counter = {"n": 0}

    def _make(n):
        ids = []
        for _ in range(n):
            counter["n"] += 1
            doc_id = f"doc{counter['n']:04d}"
            reg.create(doc_id, f"f{counter['n']}.docx", "doc", 10)
            ids.append(doc_id)
        return ids

    return _make


class TestSubmit:
    def test_submit_delete_below_threshold_queued(self, q, make_docs):
        ids = make_docs(3)
        job = q.submit(BULK_DELETE, ids, _User())
        assert job["status"] == STATUS_QUEUED
        assert job["params"]["doc_ids"] == ids
        assert job["created_by"] == "demo.admin"

    def test_submit_delete_at_threshold_awaits_approval(self, q, make_docs):
        ids = make_docs(50)
        job = q.submit(BULK_DELETE, ids, _User())
        assert job["status"] == STATUS_AWAITING_APPROVAL

    def test_submit_regenerate_threshold_15(self, q, make_docs):
        assert q.submit(BULK_REGENERATE, make_docs(14), _User())["status"] == STATUS_QUEUED
        assert q.submit(BULK_REGENERATE, make_docs(15), _User())["status"] == STATUS_AWAITING_APPROVAL

    def test_submit_empty_rejected(self, q):
        with pytest.raises(ValueError):
            q.submit(BULK_DELETE, [], _User())

    def test_submit_invalid_type_rejected(self, q):
        with pytest.raises(ValueError):
            q.submit("bulk_magic", ["doc001"], _User())


class TestApproveCancel:
    def test_approve_moves_awaiting_to_queued(self, q, make_docs):
        ids = make_docs(50)
        job = q.submit(BULK_DELETE, ids, _User())
        approved = q.approve(job["id"], _User("u-admin2", "demo.admin2"))
        assert approved["status"] == STATUS_QUEUED
        assert approved["approved_by"] == "demo.admin2"

    def test_approve_rejects_self_approval(self, q, make_docs):
        """Four-eyes: создатель задачи не может одобрить её сам."""
        creator = _User("u-admin", "demo.admin")
        job = q.submit(BULK_DELETE, make_docs(50), creator)
        with pytest.raises(SelfApprovalError):
            q.approve(job["id"], creator)
        # Статус не изменился — другой админ всё ещё может одобрить.
        assert q.get(job["id"])["status"] == STATUS_AWAITING_APPROVAL
        approved = q.approve(job["id"], _User("u-admin2", "demo.admin2"))
        assert approved["status"] == STATUS_QUEUED
        assert approved["approved_by"] == "demo.admin2"

    def test_approve_rejects_non_awaiting(self, q, make_docs):
        job = q.submit(BULK_DELETE, make_docs(3), _User())
        with pytest.raises(ValueError):
            q.approve(job["id"], _User())

    def test_cancel_queued(self, q, make_docs):
        job = q.submit(BULK_DELETE, make_docs(3), _User())
        cancelled = q.cancel(job["id"], _User())
        assert cancelled["status"] == STATUS_CANCELLED

    def test_approve_cancel_audit_records_ip(self, q, make_docs):
        """ip_address из запроса доезжает до audit-записей job_approve/job_cancel."""
        from app.services.audit import AuditService

        creator = _User("u-admin", "demo.admin")
        awaiting = q.submit(BULK_DELETE, make_docs(50), creator)
        q.approve(awaiting["id"], _User("u-admin2", "demo.admin2"), ip_address="10.0.0.9")

        queued = q.submit(BULK_DELETE, make_docs(3), creator)
        q.cancel(queued["id"], creator, ip_address="10.0.0.10")

        approve_entry = AuditService().query(action_type="job_approve")[-1]
        cancel_entry = AuditService().query(action_type="job_cancel")[-1]
        assert approve_entry["ip_address"] == "10.0.0.9"
        assert cancel_entry["ip_address"] == "10.0.0.10"

    def test_cancel_rejects_running_or_finished(self, q, make_docs):
        job = q.submit(BULK_DELETE, make_docs(3), _User())
        q.cancel(job["id"], _User())
        with pytest.raises(ValueError):
            q.cancel(job["id"], _User())


class TestCircuitBreaker:
    def test_submit_rejected_when_queue_full(self, make_docs, monkeypatch, tmp_path):
        q = JobQueue(start_worker=False)
        settings = Settings(**{"_env_file": None, "data_dir": tmp_path, "job_queue_max_pending": 2})
        monkeypatch.setattr(jq, "get_settings", lambda: settings)

        ids = make_docs(10)
        q.submit(BULK_DELETE, ids[:1], _User())
        q.submit(BULK_DELETE, ids[1:2], _User())
        with pytest.raises(QueueOverloadedError):
            q.submit(BULK_DELETE, ids[2:3], _User())

    def test_awaiting_approval_counts_toward_pending(self, make_docs, monkeypatch, tmp_path):
        q = JobQueue(start_worker=False)
        settings = Settings(**{"_env_file": None, "data_dir": tmp_path, "job_queue_max_pending": 1})
        monkeypatch.setattr(jq, "get_settings", lambda: settings)

        ids = make_docs(50)
        q.submit(BULK_DELETE, ids, _User())  # awaiting_approval → занимает очередь
        with pytest.raises(QueueOverloadedError):
            q.submit(BULK_DELETE, ids[:1], _User())


class TestPendingCount:
    def test_pending_count_includes_queued_and_awaiting(self, q, make_docs):
        assert q.pending_count() == 0
        q.submit(BULK_DELETE, make_docs(3), _User())
        q.submit(BULK_DELETE, make_docs(50), _User())
        assert q.pending_count() == 2


class TestRestartRecovery:
    """Восстановление задач после рестарта: queued → обратно в очередь,
    running (worker умер вместе с процессом) → failed."""

    def _insert_job(self, status, doc_id="doc0099"):
        from app.db.models import Job
        from app.db.session import session_scope

        with session_scope() as s:
            job = Job(
                job_type=BULK_DELETE,
                status=status,
                created_by_id="u-admin",
                created_by="demo.admin",
                params={"doc_ids": [doc_id]},
            )
            s.add(job)
            s.flush()
            return job.id

    def test_queued_requeued_and_running_failed(self):
        q = JobQueue(start_worker=False)
        queued_id = self._insert_job(STATUS_QUEUED)
        running_id = self._insert_job(STATUS_RUNNING)

        q.recover_after_restart()

        # queued вернулся во внутреннюю очередь.
        assert q._queue.qsize() == 1
        assert q._queue.get() == queued_id
        # running помечен failed с пояснением.
        job = q.get(running_id)
        assert job["status"] == STATUS_FAILED
        assert "перезапущен" in (job["result"] or {}).get("error", "")
