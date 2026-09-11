"""Idempotent companion to f8d9e0a1b2c3 for dev databases ahead of Alembic.

Run from backend: .venv/Scripts/python scripts/migrate_reference_origin.py
Existing text and translations are preserved; historical unknown origins = und.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text
from app.db.session import get_engine


def migrate(engine):
    with engine.begin() as conn:
        for table in ("developments", "attribute_values"):
            columns = {c["name"] for c in inspect(conn).get_columns(table)}
            if "canonical_locale" not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN canonical_locale VARCHAR(16) NOT NULL DEFAULT 'und'"))


if __name__ == "__main__":
    migrate(get_engine())
    print("Reference origin schema is ready.")
