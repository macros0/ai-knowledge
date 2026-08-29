"""Тесты очереди массовых операций (jobs): four-eyes, circuit breaker, статусы."""
import pytest

from app.config import Settings
from app.services import job_queue as jq
from app.services.job_queue import (
    BULK_DELETE,
    BULK_REGENERATE,
    STATUS_AWAITING_APPROVAL,
    STATUS_CANCELLED,
    STATUS_QUEUED,
    JobQueue,
    QueueOverloadedError,
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

    def test_approve_rejects_non_awaiting(self, q, make_docs):
        job = q.submit(BULK_DELETE, make_docs(3), _User())
        with pytest.raises(ValueError):
            q.approve(job["id"], _User())

    def test_cancel_queued(self, q, make_docs):
        job = q.submit(BULK_DELETE, make_docs(3), _User())
        cancelled = q.cancel(job["id"], _User())
        assert cancelled["status"] == STATUS_CANCELLED

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
