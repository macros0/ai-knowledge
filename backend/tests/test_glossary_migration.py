from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

BACKEND_DIR = Path(__file__).resolve().parents[1]


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
