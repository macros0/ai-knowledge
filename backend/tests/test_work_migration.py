from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Job


def test_nonempty_jobs_preserved_and_work_ledger_cannot_be_downgraded(tmp_path, monkeypatch):
    database = tmp_path / "work-migration.db"
    url = "sqlite:///" + database.as_posix()
    engine = create_engine(url)
    Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables if table.name not in {"work_items", "document_work_inputs", "work_progress"}])
    with Session(engine) as session:
        session.add(Job(job_type="bulk_delete", status="awaiting_approval", created_by="admin-a", params={"doc_ids": ["a" * 16]}))
        session.commit()
    monkeypatch.setenv("DATABASE_URL", url)
    from app.config import get_settings
    get_settings.cache_clear()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl01h_storage_migration")
    command.upgrade(config, "rpl02_work_items")
    assert "work_items" in inspect(engine).get_table_names()
    command.downgrade(config, "rpl01h_storage_migration")
    command.upgrade(config, "rpl02_work_items")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT status FROM jobs")) == "awaiting_approval"
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "rpl02_work_items"
        connection.execute(text("INSERT INTO work_items(id,kind,payload_version,payload_json,dedupe_key) VALUES(:id,'test',1,'{}','accepted')"), {"id": "b" * 32})
    with pytest.raises(RuntimeError, match="work ledger"):
        command.downgrade(config, "rpl01h_storage_migration")
    engine.dispose()


def test_work_lease_settings_require_heartbeat_margin():
    from app.config import Settings
    settings = Settings(_env_file=None)
    assert (settings.work_lease_seconds, settings.work_heartbeat_seconds, settings.work_max_attempts) == (120, 30, 3)
    with pytest.raises(ValueError):
        Settings(_env_file=None, work_lease_seconds=30, work_heartbeat_seconds=30)
