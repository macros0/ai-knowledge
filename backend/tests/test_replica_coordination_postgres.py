"""Real independent processes must share PostgreSQL resource/window locks."""
from multiprocessing import get_context

import pytest
from sqlalchemy import select, func, text

from app.db.models import ReplicaCapacitySlot, ReplicaRateEvent
from app.db.session import session_scope
from tests.support.replica_postgres import disposable_postgres


def _contender(url, identity, go, pipe):
    from app.db.session import configure_database, get_engine
    from app.services.work.coordination import reserve_capacity, consume_rate
    configure_database(url)
    try:
        if not go.wait(30):
            raise RuntimeError("Coordination barrier timed out")
        with session_scope() as session:
            lease = reserve_capacity(session, resource="llm", owner_id=identity, limit=2, ttl_seconds=120)
        with session_scope() as session:
            accepted = consume_rate(session, key="chat:shared-actor", limit=2, window_seconds=60)
        pipe.send((lease is not None, accepted))
    finally:
        get_engine().dispose()
        pipe.close()


@pytest.fixture
def postgres():
    with disposable_postgres() as url:
        yield url


def test_independent_apis_and_workers_share_limits(postgres):
    context = get_context("spawn")
    go, processes, readers = context.Event(), [], []
    for number in range(4):
        reader, writer = context.Pipe(duplex=False)
        process = context.Process(target=_contender, args=(postgres, f"instance-{number}", go, writer))
        process.start()
        writer.close()
        processes.append(process)
        readers.append(reader)
    try:
        go.set()
        results = []
        for reader in readers:
            assert reader.poll(45)
            results.append(reader.recv())
        assert sum(value[0] for value in results) == 2
        assert sum(value[1] for value in results) == 2
        with session_scope() as session:
            assert session.scalar(select(func.count()).select_from(ReplicaCapacitySlot)) == 2
            assert session.scalar(select(func.count()).select_from(ReplicaRateEvent)) == 2
    finally:
        go.set()
        for process in processes:
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(5)
        for reader in readers:
            reader.close()
    assert all(process.exitcode == 0 for process in processes)


def test_real_postgres_upgrade_and_lossy_downgrade_refusal(postgres, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from alembic import command
    from alembic.config import Config
    from app.db.base import Base
    from app.db.session import get_engine
    from app.db.models import Job
    from tests.test_replica_coordination_migration import COORDINATION_TABLES
    from app.services.work.coordination import reserve_capacity
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in COORDINATION_TABLES:
            table.drop(get_engine())
    with session_scope() as session:
        session.add(Job(job_type="bulk_delete", status="awaiting_approval", created_by="admin"))
        schema = session.scalar(text("SELECT current_schema()"))
    assert schema.startswith("artifact_") and len(schema) == 41
    monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=10000")
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(db_url=postgres))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl02b_work_progress")
    command.upgrade(config, "rpl03_replica_coordination")
    with session_scope() as session:
        assert session.query(Job).one().status == "awaiting_approval"
        reserve_capacity(session, resource="llm", owner_id="api-a", limit=1, ttl_seconds=60, release_required=True)
    with pytest.raises(RuntimeError, match="coordination"):
        command.downgrade(config, "rpl02b_work_progress")
