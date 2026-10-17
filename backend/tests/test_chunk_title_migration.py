"""Canonical chunk titles survive storage and safe reverse migration."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Text, create_engine, inspect, text
from app.db.models import DocumentChunk


def revision():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/062b1c2d3e4f_chunk_title_text.py"
    spec = importlib.util.spec_from_file_location("chunk_title_revision", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_title_schema_can_store_complete_title():
    assert isinstance(DocumentChunk.__table__.c.section_title.type, Text)


def test_upgrade_preserves_titles_null_and_content_and_refuses_lossy_downgrade(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "titles.db"))
    with engine.begin() as c:
        c.execute(
            text(
                "CREATE TABLE document_chunks (id INTEGER PRIMARY KEY, section_title VARCHAR(1024), content TEXT NOT NULL)"
            )
        )
        c.execute(
            text(
                "INSERT INTO document_chunks VALUES (1,'title','body 0 False'),(2,NULL,'unchanged')"
            )
        )
        original = c.execute(text("SELECT * FROM document_chunks ORDER BY id")).all()
        with Operations.context(MigrationContext.configure(c)):
            migration = revision()
            assert migration.down_revision == "061b1c2d3e4f"
            migration.upgrade()
            assert c.execute(text("SELECT * FROM document_chunks ORDER BY id")).all() == original
            assert isinstance(inspect(c).get_columns("document_chunks")[1]["type"], Text)
            long_title = "Заголовок 👨‍💻 0 False " * 200
            c.execute(
                text("UPDATE document_chunks SET section_title=:title WHERE id=1"),
                {"title": long_title},
            )
            with pytest.raises(RuntimeError):
                migration.downgrade()
            assert (
                c.execute(text("SELECT section_title FROM document_chunks WHERE id=1")).scalar_one()
                == long_title
            )
            c.execute(text("UPDATE document_chunks SET section_title='title' WHERE id=1"))
            migration.downgrade()
            assert c.execute(text("SELECT * FROM document_chunks ORDER BY id")).all() == original
            assert inspect(c).get_columns("document_chunks")[1]["type"].length == 1024
    engine.dispose()
