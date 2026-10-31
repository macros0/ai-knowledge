"""Required PostgreSQL evidence: independent processes and deterministic barriers."""
from multiprocessing import get_context
from multiprocessing.connection import wait
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from time import monotonic

import pytest
from sqlalchemy import text, update

from app.db.models import WorkItem
from app.db.session import configure_database, get_engine, session_scope
from app.services.work.models import LostOwnership
from app.services.work.store import claim, enqueue, finish, renew
from tests.support.replica_postgres import disposable_postgres
from tests.test_work_store import spec


@pytest.fixture(autouse=True)
def _db():
    with disposable_postgres() as url:
        yield url


def _claim_process(url, owner, barrier, release, pipe):
    configure_database(url)
    try:
        barrier.wait(20)
        with session_scope() as session:
            work = claim(session, owner_id=owner, supported={"test": {1}}, lease_seconds=120)
            pipe.send(work)
            if not release.wait(20):
                raise RuntimeError("Test barrier timed out")
    finally:
        get_engine().dispose()
        pipe.close()


def test_two_processes_claim_once(_db):
    with session_scope() as session:
        work_id = enqueue(session, spec())
    context = get_context("spawn")
    barrier, release = context.Barrier(2), context.Event()
    handles = []
    try:
        for index in range(2):
            receiving, sending = context.Pipe(duplex=False)
            process = context.Process(target=_claim_process, args=(_db, f"worker-{index}", barrier, release, sending))
            process.start()
            sending.close()
            handles.append((process, receiving))
        claims = []
        for _, receiving in handles:
            assert receiving.poll(25), "Claim did not finish while peer held the SQL lock"
            claims.append(receiving.recv())
        assert [work.work_id for work in claims if work] == [work_id]
    finally:
        release.set()
        for process, receiving in handles:
            process.join(25)
            if process.is_alive():
                process.terminate()
                process.join(5)
            receiving.close()
    assert all(process.exitcode == 0 for process, _ in handles)


def _returning_owner(url, pipe, resume):
    configure_database(url)
    try:
        with session_scope() as session:
            work = claim(session, owner_id="old-process", supported={"test": {1}}, lease_seconds=120)
        pipe.send(work)  # committed claim; simulated loss of acknowledgement follows
        if not resume.wait(25):
            return
        try:
            with session_scope() as session:
                finish(session, work, outcome="succeeded")
        except LostOwnership:
            pipe.send("lost")
        else:
            pipe.send("published")
    finally:
        get_engine().dispose()
        pipe.close()


def test_old_process_cannot_finish_after_lease_reclaim(_db):
    with session_scope() as session:
        enqueue(session, spec())
    context = get_context("spawn")
    receiving, sending = context.Pipe(duplex=False)
    resume = context.Event()
    process = context.Process(target=_returning_owner, args=(_db, sending, resume))
    process.start()
    sending.close()
    try:
        assert receiving.poll(25)
        old = receiving.recv()
        with session_scope() as session:
            session.execute(update(WorkItem).where(WorkItem.id == old.work_id)
                .values(lease_until=text("clock_timestamp() - interval '1 second'")))
        with session_scope() as session:
            current = claim(session, owner_id="new-process", supported={"test": {1}}, lease_seconds=120)
            assert current.fence == old.fence + 1
        resume.set()
        assert receiving.poll(25) and receiving.recv() == "lost"
        with session_scope() as session:
            finish(session, current, outcome="succeeded")
    finally:
        resume.set()
        process.join(25)
        if process.is_alive():
            process.terminate()
            process.join(5)
        receiving.close()
    assert process.exitcode == 0


def test_expired_lease_cannot_be_renewed_before_reclaim(_db):
    with session_scope() as session:
        enqueue(session, spec())
        work = claim(session, owner_id="worker", supported={"test": {1}}, lease_seconds=120)
    with session_scope() as session:
        session.execute(update(WorkItem).where(WorkItem.id == work.work_id)
            .values(lease_until=text("clock_timestamp() - interval '1 millisecond'")))
    with pytest.raises(LostOwnership), session_scope() as session:
        renew(session, work, lease_seconds=120)


def _enqueue_process(url, barrier, release, pipe):
    configure_database(url)
    try:
        barrier.wait(20)
        with session_scope() as session:
            pipe.send(enqueue(session, spec()))
            assert release.wait(20)
    finally:
        get_engine().dispose()
        pipe.close()


def test_two_processes_dedupe_one_committed_work(_db):
    context = get_context("spawn")
    barrier, release = context.Barrier(2), context.Event()
    handles = []
    try:
        for _ in range(2):
            receiving, sending = context.Pipe(duplex=False)
            process = context.Process(target=_enqueue_process, args=(_db, barrier, release, sending))
            process.start()
            sending.close()
            handles.append((process, receiving))
        ready = wait([pipe for _, pipe in handles], timeout=25)
        assert ready
        first = ready[0].recv()
        release.set()  # Let the inserter commit before the unique-key waiter proceeds.
        other = next(pipe for _, pipe in handles if pipe is not ready[0])
        assert other.poll(25) and other.recv() == first
        with session_scope() as session:
            assert session.query(WorkItem).count() == 1
    finally:
        release.set()
        for process, receiving in handles:
            process.join(25)
            if process.is_alive():
                process.terminate()
                process.join(5)
            receiving.close()
    assert all(process.exitcode == 0 for process, _ in handles)


def test_postgres_nonempty_job_schema_upgrade_and_unsafe_downgrade(_db, monkeypatch):
    # Exercise the historical R1 contract, before R2 introduces a dependent FK.
    from app.db.models import DocumentWorkInput
    DocumentWorkInput.__table__.drop(get_engine())
    from types import SimpleNamespace
    from alembic import command
    from alembic.config import Config
    from app.db.models import Job
    with session_scope() as session:
        session.add(Job(job_type="bulk_delete", status="awaiting_approval", created_by="admin"))
        schema = session.scalar(text("select current_schema()"))
    assert schema.startswith("artifact_") and len(schema) == 41
    monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=10000")
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(db_url=get_engine().url.set(query={}).render_as_string(hide_password=False)))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl02_work_items")
    command.downgrade(config, "rpl01h_storage_migration")
    command.upgrade(config, "rpl02_work_items")
    with session_scope() as session:
        assert session.query(Job).one().status == "awaiting_approval"
        enqueue(session, spec())
    with pytest.raises(RuntimeError, match="work ledger"):
        command.downgrade(config, "rpl01h_storage_migration")


def test_process_crash_after_committed_claim_is_recoverable(_db):
    with session_scope() as session:
        enqueue(session, spec())
    context = get_context("spawn")
    receiving, sending = context.Pipe(duplex=False)
    process = context.Process(target=_returning_owner, args=(_db, sending, context.Event()))
    process.start()
    sending.close()
    try:
        assert receiving.poll(25)
        old = receiving.recv()
        process.terminate()  # Only this test-owned child, after its SQL commit.
        process.join(10)
        assert not process.is_alive()
        with session_scope() as session:
            row = session.get(WorkItem, old.work_id)
            assert row.status == "running" and row.fence == old.fence
            session.execute(update(WorkItem).where(WorkItem.id == old.work_id)
                .values(lease_until=text("clock_timestamp() - interval '1 second'")))
        with session_scope() as session:
            current = claim(session, owner_id="replacement", supported={"test": {1}}, lease_seconds=120)
            assert current.work_id == old.work_id and current.fence == old.fence + 1
            finish(session, current, outcome="succeeded")
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        receiving.close()


def test_renew_uses_db_time_after_waiting_for_row_lock(_db):
    with session_scope() as session:
        enqueue(session, spec())
        work = claim(session, owner_id="waiting-owner", supported={"test": {1}}, lease_seconds=120)
    pids = Queue()
    def renew_waiter():
        try:
            with session_scope() as session:
                session.execute(text("SET LOCAL lock_timeout='5s'"))
                pids.put(session.scalar(text("SELECT pg_backend_pid()")))
                renew(session, work, lease_seconds=120)
        except LostOwnership:
            return "lost"
        return "renewed"
    with ThreadPoolExecutor(max_workers=1) as executor:
        with session_scope() as blocker:
            deadline = blocker.scalar(update(WorkItem).where(WorkItem.id == work.work_id)
                .values(lease_until=text("clock_timestamp() + interval '3 seconds'"))
                .returning(WorkItem.lease_until))
            future = executor.submit(renew_waiter)
            backend_pid = pids.get(timeout=10)
            limit = monotonic() + 2
            while True:
                blocker.execute(text("SELECT pg_stat_clear_snapshot()"))
                blocked = blocker.scalar(text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"), {"pid": backend_pid})
                if blocked == "Lock":
                    break
                assert monotonic() < limit, "Renewal never reached the test-owned row lock"
            # Confirmed lock wait + DB-time barrier, rather than a guessed host sleep.
            blocker.execute(text("SELECT pg_sleep_until(cast(:deadline AS timestamptz))"), {"deadline": deadline})
        assert future.result(timeout=10) == "lost"
