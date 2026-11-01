"""Nonempty replica upgrades start held and retire the old schema binding."""
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, select, text

from app.db.base import Base
from app.db.models import ArtifactStorageControl


def old_schema(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "maintenance-migration.db").as_posix())
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name != "replica_maintenance_state"])
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE work_items DROP COLUMN maintenance_epoch"))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return engine, config


def test_installed_upgrade_is_held_and_preserves_queued_work(tmp_path):
    engine, config = old_schema(tmp_path)
    with engine.begin() as conn:
        conn.execute(ArtifactStorageControl.__table__.insert().values(id=1, installation_id="1" * 32, readonly_roots=[]))
        conn.execute(text("INSERT INTO replica_runtime_identity VALUES (1,:installation,:fingerprint,:receipt,'rpl04_runtime_identity',CURRENT_TIMESTAMP)"),
            {"installation": "1" * 32, "fingerprint": "2" * 64, "receipt": "sha256:" + "3" * 64})
        conn.execute(text("INSERT INTO work_items(id,kind,payload_version,payload_json,dedupe_key) VALUES(:id,'probe',1,'{}','preserved')"), {"id": "4" * 32})
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.stamp(config, "rpl04_runtime_identity")
        command.upgrade(config, "head")
        assert conn.scalar(text("SELECT schema_revision FROM replica_runtime_identity")) == "rpl05_maintenance_epoch"
        state = conn.execute(text("SELECT * FROM replica_maintenance_state")).mappings().one()
        assert state["enabled"] and state["epoch"] == 1
        assert state["installation_id"] == "1" * 32
        assert conn.execute(text("SELECT status,maintenance_epoch FROM work_items")).one() == ("queued", 0)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        with pytest.raises(RuntimeError, match="recovery"):
            command.downgrade(config, "rpl04_runtime_identity")
    engine.dispose()


def test_uninstalled_empty_schema_can_roundtrip(tmp_path):
    engine, config = old_schema(tmp_path)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.stamp(config, "rpl04_runtime_identity")
        command.upgrade(config, "head")
        assert conn.scalar(select(text("count(*)")).select_from(text("replica_maintenance_state"))) == 0
        command.downgrade(config, "rpl04_runtime_identity")
    engine.dispose()
