"""Private versioned progress survives reclaim without accepting stale writes."""
import pytest

from app.db.session import session_scope
from app.services.work.models import LostOwnership
from app.services.work.store import enqueue, claim
from tests.test_work_store import spec, expire


def test_progress_survives_reclaim_and_rejects_old_owner():
    from app.services.work.progress import load_progress, save_progress
    with session_scope() as session:
        enqueue(session, spec())
        old = claim(session, owner_id="old", supported={"test": {1}}, lease_seconds=120)
        save_progress(session, old, version=1, state={"last_id": 7})
    expire(old.work_id)
    with session_scope() as session:
        current = claim(session, owner_id="new", supported={"test": {1}}, lease_seconds=120)
        assert load_progress(session, current, version=1) == {"last_id": 7}
    with pytest.raises(LostOwnership), session_scope() as session:
        save_progress(session, old, version=1, state={"last_id": 99})
    with session_scope() as session:
        assert load_progress(session, current, version=1) == {"last_id": 7}


def test_progress_transaction_rollback_preserves_last_accepted_cursor():
    from app.services.work.progress import load_progress, save_progress
    with session_scope() as session:
        enqueue(session, spec())
        work = claim(session, owner_id="worker", supported={"test": {1}}, lease_seconds=120)
        save_progress(session, work, version=1, state={"last_id": 3})
    with pytest.raises(RuntimeError), session_scope() as session:
        save_progress(session, work, version=1, state={"last_id": 4})
        raise RuntimeError("crash before commit")
    with session_scope() as session:
        assert load_progress(session, work, version=1) == {"last_id": 3}
    with pytest.raises(ValueError, match="version"), session_scope() as session:
        load_progress(session, work, version=2)


@pytest.mark.parametrize("accepted_state", ["cursor", "queued_scope"])
def test_progress_migration_preserves_jobs_and_refuses_lost_cursors(tmp_path, monkeypatch, accepted_state):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import Session
    from app.db.base import Base
    from app.db.models import Job, WorkItem
    url = "sqlite:///" + (tmp_path / "progress.db").as_posix()
    engine = create_engine(url)
    Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables if table.name != "work_progress"])
    with Session(engine) as session:
        session.add(Job(job_type="bulk_delete", status="awaiting_approval", params={"doc_ids": ["a" * 16]}))
        session.add(WorkItem(id="b" * 32, kind="test", payload_version=1, payload_json={}, dedupe_key="accepted"))
        session.commit()
    monkeypatch.setenv("DATABASE_URL", url)
    from app.config import get_settings
    get_settings.cache_clear()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl02a_document_work")
    command.upgrade(config, "rpl02b_work_progress")
    assert "work_progress" in inspect(engine).get_table_names()
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT status FROM jobs")) == "awaiting_approval"
        if accepted_state == "cursor":
            connection.execute(text("INSERT INTO work_progress(work_id,version,input_hash,state_json) VALUES(:id,1,:hash,:state)"),
                {"id": "b" * 32, "hash": "a" * 64, "state": '{"cursor":3}'})
        else:
            connection.execute(text("UPDATE work_items SET kind='scope_change'"))
    with pytest.raises(RuntimeError, match="progress|scope"):
        command.downgrade(config, "rpl02a_document_work")
    engine.dispose()
