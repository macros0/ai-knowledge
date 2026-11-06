"""Identity migration preserves existing jobs and refuses installed rollback."""
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text

from app.db.base import Base


def test_runtime_identity_migration_preserves_existing_work(tmp_path, monkeypatch):
    url = "sqlite:///" + (tmp_path / "identity-migration.db").as_posix()
    engine = create_engine(url)
    Base.metadata.create_all(engine, tables=[table for table in Base.metadata.sorted_tables
        if table.name != "replica_runtime_identity"])
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO config_revisions(domain,revision) VALUES ('references',17)"))
    monkeypatch.setenv("DATABASE_URL", url)
    from app.config import get_settings
    get_settings.cache_clear()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl03b_diagnostics")
    command.upgrade(config, "rpl04_runtime_identity")
    assert "replica_runtime_identity" in inspect(engine).get_table_names()
    command.downgrade(config, "rpl03b_diagnostics")
    command.upgrade(config, "rpl04_runtime_identity")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT revision FROM config_revisions")) == 17
        connection.execute(text("INSERT INTO replica_runtime_identity VALUES (1,:installation,:fingerprint,:receipt,:revision,CURRENT_TIMESTAMP)"),
            {"installation": "1" * 32, "fingerprint": "2" * 64,
             "receipt": "sha256:" + "3" * 64, "revision": "rpl04_runtime_identity"})
    with pytest.raises(RuntimeError, match="identity"):
        command.downgrade(config, "rpl03b_diagnostics")
    engine.dispose()
