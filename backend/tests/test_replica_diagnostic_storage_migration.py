"""Durable diagnostic schema preserves work and refuses evidence-erasing rollback."""
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Job


def test_diagnostic_schema_roundtrip_and_nonempty_downgrade_refusal(tmp_path, monkeypatch):
    url = "sqlite:///" + (tmp_path / "diagnostic-migration.db").as_posix()
    engine = create_engine(url)
    tables = {"diagnostic_replica_state", "diagnostic_instances", "diagnostic_segments"}
    Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables if table.name not in tables])
    with Session(engine) as session, session.begin():
        session.add(Job(job_type="bulk_delete", status="queued", created_by="admin"))
    monkeypatch.setenv("DATABASE_URL", url)
    from app.config import get_settings
    get_settings.cache_clear()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl03a_chat_runs")
    command.upgrade(config, "rpl03b_diagnostics")
    assert tables <= set(inspect(engine).get_table_names())
    command.downgrade(config, "rpl03a_chat_runs")
    command.upgrade(config, "rpl03b_diagnostics")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT status FROM jobs")) == "queued"
        connection.execute(text("INSERT INTO diagnostic_replica_state(id,policy_hash,evicted_segments) VALUES (1,:hash,1)"), {"hash": "0" * 64})
    with pytest.raises(RuntimeError, match="diagnostic"):
        command.downgrade(config, "rpl03a_chat_runs")
    engine.dispose()
