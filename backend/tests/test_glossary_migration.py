from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy import text
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_alias_migration_preserves_rows_allows_duplicates_and_guards_rollback(tmp_path, monkeypatch):
    db_url = f"sqlite:///{(tmp_path / 'alias-migration.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings
    from scripts.migrate_glossary_aliases import migrate
    get_settings.cache_clear()
    engine = create_engine(db_url)
    try:
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        command.upgrade(config, "b2c3d4e5f6a7")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO domain_terms (id,canonical,kind,original_name,created_at,updated_at) VALUES (1,'IT0003','sap_infotype','One',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),(2,'SECOND','business_term','Two',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
            conn.execute(text("INSERT INTO domain_term_aliases (id,term_id,alias,normalized_alias,created_at,updated_at) VALUES (1,1,'IT0003','it0003',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
            before = conn.execute(text("SELECT * FROM domain_term_aliases")).all()
        assert migrate(engine)["ready"] is False
        command.upgrade(config, "head")
        assert migrate(engine, apply=True)["ready"] is True
        assert migrate(engine, apply=True)["ready"] is True
        with engine.begin() as conn:
            assert conn.execute(text("SELECT * FROM domain_term_aliases")).all() == before
            conn.execute(text("INSERT INTO domain_term_aliases (id,term_id,alias,normalized_alias,created_at,updated_at) VALUES (2,2,'IT0003','it0003',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        with pytest.raises(RuntimeError, match="Resolve duplicate"):
            command.downgrade(config, "b2c3d4e5f6a7")
        with engine.begin() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM domain_term_aliases")).scalar() == 2
            conn.execute(text("DELETE FROM domain_term_aliases WHERE id=2"))
        command.downgrade(config, "b2c3d4e5f6a7")
        # Also exercise the standalone dev upgrade of a populated old schema.
        assert migrate(engine, apply=True)["ready"] is True
        with engine.connect() as conn:
            assert conn.execute(text("SELECT * FROM domain_term_aliases")).all() == before
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_glossary_migration_creates_schema_and_history_column(tmp_path, monkeypatch):
    db_url = f"sqlite:///{(tmp_path / 'glossary.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    from app.config import get_settings

    get_settings.cache_clear()
    try:
        command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
        engine = create_engine(db_url)
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            assert {
                "domain_terms",
                "domain_term_translations",
                "domain_term_aliases",
            } <= tables
            columns = {column["name"] for column in inspector.get_columns("chat_messages")}
            assert "retrieval_metadata" in columns
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()
