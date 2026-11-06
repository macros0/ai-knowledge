"""Coordination schema preserves the accepted queue and refuses lossy rollback."""
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Job

COORDINATION_TABLES = {"replica_capacity_resources", "replica_capacity_slots", "replica_rate_buckets",
    "replica_rate_events", "config_revisions"}


@pytest.mark.parametrize("state", ["capacity", "rate", "revision"])
def test_upgrade_preserves_job_and_downgrade_refuses_coordination_state(tmp_path, monkeypatch, state):
    url = "sqlite:///" + (tmp_path / "coordination.db").as_posix()
    engine = create_engine(url)
    Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables
        if table.name not in COORDINATION_TABLES])
    with Session(engine) as session:
        session.add(Job(job_type="bulk_delete", status="awaiting_approval", created_by="admin"))
        session.commit()
    monkeypatch.setenv("DATABASE_URL", url)
    from app.config import get_settings
    get_settings.cache_clear()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl02b_work_progress")
    command.upgrade(config, "rpl03_replica_coordination")
    assert COORDINATION_TABLES <= set(inspect(engine).get_table_names())
    command.downgrade(config, "rpl02b_work_progress")
    command.upgrade(config, "rpl03_replica_coordination")
    from app.services.work.coordination import reserve_capacity, consume_rate, bump_config_revision
    with Session(engine) as session:
        assert session.scalar(text("SELECT status FROM jobs")) == "awaiting_approval"
        if state == "capacity":
            reserve_capacity(session, resource="llm", owner_id="api-a", limit=1, ttl_seconds=60)
        elif state == "rate":
            consume_rate(session, key="actor", limit=1, window_seconds=60)
        else:
            bump_config_revision(session, "localization")
        session.commit()
    with pytest.raises(RuntimeError, match="coordination"):
        command.downgrade(config, "rpl02b_work_progress")
    engine.dispose()


def test_alembic_accepts_percent_escaped_url_without_rendering_credentials(monkeypatch):
    from io import StringIO
    from types import SimpleNamespace
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(
        db_url="postgresql+psycopg://example:dummy%25value@127.0.0.1/example?options=-csearch_path%3Dexample"))
    output = StringIO()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl03_replica_coordination", sql=True)
    assert "rpl03_replica_coordination" in output.getvalue()
    assert "dummy" not in output.getvalue()
