from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.db.base import Base


def test_document_work_migration_preserves_generations_and_refuses_lost_fencing(tmp_path, monkeypatch):
    path = tmp_path / "document-work.db"
    url = "sqlite:///" + path.as_posix()
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    from app.db.models import Document, DocumentGeneration, DocumentWorkInput
    with Session(engine) as session:
        session.add(Document(id="legacy", filename="legacy.pdf", status="done"))
        session.flush()
        session.add(DocumentGeneration(id="a" * 32, doc_id="legacy", phase="active"))
        session.commit()
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE work_progress"))
        connection.execute(text("DROP TABLE document_work_inputs"))
        connection.execute(text("DROP TABLE deleted_work_generations"))
        connection.execute(text("DROP TABLE work_admission_state"))
        connection.execute(text("DROP INDEX ix_generation_work_attempt"))
        connection.execute(text("DROP INDEX ix_document_generations_work_id"))
        for name in ("work_id", "work_fence", "work_cleanup_done"):
            connection.execute(text("ALTER TABLE document_generations DROP COLUMN " + name))
    monkeypatch.setenv("DATABASE_URL", url)
    from app.config import get_settings
    get_settings.cache_clear()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl02_work_items")
    command.upgrade(config, "rpl02b_work_progress")
    migrated_doc_id = next(column for column in inspect(engine).get_columns("document_work_inputs") if column["name"] == "doc_id")
    assert migrated_doc_id["type"].length == DocumentWorkInput.__table__.c.doc_id.type.length
    assert {"work_id", "work_fence", "work_cleanup_done"} <= {column["name"] for column in inspect(engine).get_columns("document_generations")}
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT phase FROM document_generations")) == "active"
    command.downgrade(config, "rpl02_work_items")
    command.upgrade(config, "rpl02b_work_progress")
    with engine.begin() as connection:
        connection.execute(text("UPDATE document_generations SET work_id=:id, work_fence=1"), {"id": "b" * 32})
    with pytest.raises(RuntimeError, match="fencing"):
        command.downgrade(config, "rpl02_work_items")
    engine.dispose()
