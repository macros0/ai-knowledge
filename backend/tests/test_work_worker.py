"""Explicit workers poll with bounded waits and never hold SQL during handlers."""
from threading import Event

import pytest

from app.config import Settings
from app.db.models import WorkItem
from app.db.session import session_scope
from app.services.work.handlers import HandlerRegistry, TransientWorkError
from app.services.work.worker import Worker
from app.services.work.store import enqueue
from tests.test_work_store import expire, spec


def worker(handler, **settings):
    registry = HandlerRegistry()
    registry.register("test", 1, handler)
    config = Settings(_env_file=None, work_lease_seconds=6, work_heartbeat_seconds=1,
                      work_retry_backoff_seconds=(1, 1, 1), **settings)
    return Worker(config, registry=registry, owner_id="test-worker")


def test_handler_runs_outside_sql_and_finishes():
    def handler(work):
        with session_scope() as session:
            enqueue(session, spec("child"))
    runner = worker(handler)
    with session_scope() as session:
        work_id = enqueue(session, spec())
    assert runner.run_once(Event())
    with session_scope() as session:
        assert session.get(WorkItem, work_id).status == "succeeded"


def test_heartbeat_row_lock_timeout_retries_while_handler_owns_local_slot(monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.services.work import store
    release = Event()
    calls = []
    original = store.renew
    class LockBusy(Exception):
        sqlstate = "55P03"
    def renewal(session, work, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError("renew", {}, LockBusy())
        result = original(session, work, **kwargs)
        release.set()
        return result
    monkeypatch.setattr(store, "renew", renewal)
    def handler(_work):
        assert release.wait(10)
    runner = worker(handler)
    with session_scope() as session:
        work_id = enqueue(session, spec())
    try:
        assert runner.run_once(Event())
        assert len(calls) >= 2
        with session_scope() as session:
            assert session.get(WorkItem, work_id).status == "succeeded"
    finally:
        release.set()
        if runner._future is not None:
            runner._future.result(timeout=10)


def test_only_classified_transient_errors_retry():
    def handler(_work):
        raise TransientWorkError("storage_unavailable")
    runner = worker(handler, work_max_attempts=2)
    with session_scope() as session:
        work_id = enqueue(session, spec())
    assert runner.run_once(Event())
    with session_scope() as session:
        row = session.get(WorkItem, work_id)
        assert row.status == "queued" and row.attempts == 1
        row.available_at = row.created_at
    assert runner.run_once(Event())
    with session_scope() as session:
        row = session.get(WorkItem, work_id)
        assert row.status == "failed" and row.attempts == 2 and row.error_code == "storage_unavailable"


def test_validation_failure_does_not_retry_or_persist_exception_content():
    def handler(_work):
        raise ValueError("private credentials and content must not be persisted")
    runner = worker(handler)
    with session_scope() as session:
        work_id = enqueue(session, spec())
    assert runner.run_once(Event())
    with session_scope() as session:
        row = session.get(WorkItem, work_id)
        assert row.status == "failed" and row.error_code == "handler_failed" and row.attempts == 1


def test_expired_handler_return_cannot_ack():
    def handler(work):
        expire(work.work_id)
    runner = worker(handler)
    with session_scope() as session:
        work_id = enqueue(session, spec())
    runner.run_once(Event())
    with session_scope() as session:
        assert session.get(WorkItem, work_id).status == "running"


def test_database_outage_uses_bounded_interruptible_backoff(monkeypatch):
    runner = worker(lambda _: None)
    def unavailable(_stop):
        raise OSError("private database credentials")
    monkeypatch.setattr(runner, "run_once", unavailable)
    waits = []
    class Stop:
        def is_set(self):
            return len(waits) == 3
        def wait(self, delay):
            waits.append(delay)
            return self.is_set()
    runner.run(Stop())
    assert len(waits) == 3 and all(1 <= delay <= 30 for delay in waits)


def test_handler_timeout_is_a_failure_and_never_a_heartbeat_timeout(monkeypatch):
    from app.services.work import store
    def handler(_work):
        raise TimeoutError("handler timeout")
    def renew_completed(*_args, **_kwargs):
        pytest.fail("Completed handler must not renew")
    monkeypatch.setattr(store, "renew", renew_completed)
    runner = worker(handler)
    with session_scope() as session:
        work_id = enqueue(session, spec())
    runner.run_once(Event())
    with session_scope() as session:
        assert session.get(WorkItem, work_id).status == "failed"


def test_lost_owner_keeps_local_slot_until_handler_returns(monkeypatch):
    from app.services.work import store
    from app.services.work.models import LostOwnership
    release, calls = Event(), []
    def handler(work):
        calls.append(work.work_id)
        assert release.wait(10)
    def lost(*_args, **_kwargs):
        raise LostOwnership("simulated coordinator loss")
    monkeypatch.setattr(store, "renew", lost)
    runner = worker(handler)
    with session_scope() as session:
        enqueue(session, spec())
        enqueue(session, spec("other"))
    try:
        assert runner.run_once(Event())
        assert not runner.run_once(Event())
        assert len(calls) == 1
    finally:
        release.set()


def test_heartbeat_renews_while_handler_has_no_sql_transaction(monkeypatch):
    from app.services.work import store
    release, renewed = Event(), []
    def handler(_work):
        assert release.wait(10)
    original = store.renew
    def heartbeat(session, old, **kwargs):
        new = original(session, old, **kwargs)
        renewed.append((old.lease_until, new.lease_until))
        release.set()
        return new
    monkeypatch.setattr(store, "renew", heartbeat)
    runner = worker(handler)
    with session_scope() as session:
        work_id = enqueue(session, spec())
    try:
        runner.run_once(Event())
    finally:
        release.set()
    assert renewed and all(after > before for before, after in renewed)
    with session_scope() as session:
        assert session.get(WorkItem, work_id).status == "succeeded"
