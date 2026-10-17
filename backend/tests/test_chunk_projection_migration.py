"""Add nullable version/materialized rows without migrating legacy contents."""

import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text, inspect
from alembic.migration import MigrationContext
from alembic.operations import Operations

REVISION = (
    Path(__file__).resolve().parents[1] / "alembic/versions/061b1c2d3e4f_chunk_projections.py"
)


def migration():
    spec = importlib.util.spec_from_file_location("chunk_projection_revision", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_preserves_legacy_and_downgrade_refuses_new_format(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "migration.db"))
    with engine.begin() as c:
        c.execute(text("CREATE TABLE documents(id varchar(16) PRIMARY KEY)"))
        c.execute(
            text(
                "CREATE TABLE document_generations(id varchar(32) PRIMARY KEY,doc_id varchar(16),phase varchar(16))"
            )
        )
        c.execute(text("INSERT INTO documents VALUES ('legacy')"))
        c.execute(text("INSERT INTO document_generations VALUES ('legacy-gen','legacy','active')"))
        before = c.execute(text("SELECT id,doc_id,phase FROM document_generations")).all()
        with Operations.context(MigrationContext.configure(c)):
            revision = migration()
            assert revision.down_revision == "060b1c2d3e4f"
            revision.upgrade()
            assert (
                c.execute(text("SELECT id,doc_id,phase FROM document_generations")).all() == before
            )
            assert (
                c.execute(
                    text("SELECT chunk_projection_version FROM document_generations")
                ).scalar_one()
                is None
            )
            assert {"document_chunk_projections", "document_chunk_fragments"} <= set(
                inspect(c).get_table_names()
            )
            c.execute(text("UPDATE document_generations SET chunk_projection_version=1"))
            with pytest.raises(RuntimeError):
                revision.downgrade()
            assert "document_chunk_fragments" in inspect(c).get_table_names()
            c.execute(text("UPDATE document_generations SET chunk_projection_version=NULL"))
            revision.downgrade()
            assert (
                c.execute(text("SELECT id,doc_id,phase FROM document_generations")).all() == before
            )
            assert "document_chunk_fragments" not in inspect(c).get_table_names()
            assert "chunk_projection_version" not in {
                row["name"] for row in inspect(c).get_columns("document_generations")
            }
    engine.dispose()
