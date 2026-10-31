from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.db.base import Base


def test_alembic_upgrade_nonempty_legacy_database_preserves_document(tmp_path, monkeypatch):
    database = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    new_tables = {"artifact_objects", "artifact_bindings", "upload_receipts"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in new_tables])
    from app.db.models import Document
    with Session(engine) as session:
        session.add(Document(id="0123456789abcdef", filename="legacy.pdf"))
        session.commit()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database.as_posix()}")
    from app.config import get_settings
    get_settings.cache_clear()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl00_diagnostic_ingest")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT filename FROM documents")).scalar_one() == "legacy.pdf"
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "rpl01_artifact_objects"
        assert new_tables <= set(inspect(connection).get_table_names())
        assert connection.execute(text("SELECT count(*) FROM artifact_bindings")).scalar_one() == 0
    engine.dispose()
