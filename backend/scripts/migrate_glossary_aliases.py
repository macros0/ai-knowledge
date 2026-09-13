"""Apply the alias-uniqueness migration to create_all-managed dev databases.

Check-only by default; does not stamp or alter Alembic history. Preserves all
aliases, translations and terms. Existing code aliases become ordinary aliases.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def migrate(engine, *, apply=False):
    with engine.begin() as connection:
        if apply:
            path = BACKEND / "alembic/versions/c3d4e5f6a7b8_glossary_alias_duplicates.py"
            spec = importlib.util.spec_from_file_location("alias_migration", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
        constraints = inspect(connection).get_unique_constraints("domain_term_aliases")
        columns = [item["column_names"] for item in constraints]
        return {"ready": ["term_id", "normalized_alias"] in columns and ["normalized_alias"] not in columns}


if __name__ == "__main__":
    from app.db.session import get_engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    result = migrate(get_engine(), apply=parser.parse_args().apply)
    print(json.dumps(result))
    raise SystemExit(0 if result["ready"] else 1)
