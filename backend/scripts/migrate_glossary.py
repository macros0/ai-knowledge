"""Check or apply the additive glossary schema for dev databases.

Default mode is check-only.  ``--apply`` creates only missing glossary tables
and the history column; it never stamps Alembic and refuses to guess repairs
for partially existing tables.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text

from app.db.models import DomainTerm, DomainTermAlias, DomainTermTranslation
from app.db.session import get_engine

TABLES = (DomainTerm.__table__, DomainTermTranslation.__table__, DomainTermAlias.__table__)
REQUIRED_COLUMNS = {
    table.name: {column.name for column in table.columns} for table in TABLES
}


def inspect_schema(engine) -> dict:
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    missing_tables = [table.name for table in TABLES if table.name not in existing]
    missing_columns: dict[str, list[str]] = {}
    missing_constraints: dict[str, list[str]] = {}
    for table in TABLES:
        if table.name not in existing:
            continue
        columns = {column["name"] for column in inspector.get_columns(table.name)}
        missing = sorted(REQUIRED_COLUMNS[table.name] - columns)
        if missing:
            missing_columns[table.name] = missing

        unique_columns = {
            tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(table.name)
        }
        expected = {
            "domain_terms": {("canonical",)},
            "domain_term_aliases": {("term_id", "normalized_alias")},
        }.get(table.name, set())
        missing_unique = sorted(expected - unique_columns)
        if missing_unique:
            missing_constraints[table.name] = [
                f"unique({', '.join(columns)})" for columns in missing_unique
            ]

    chat_columns = {column["name"] for column in inspector.get_columns("chat_messages")}
    missing_history = "retrieval_metadata" not in chat_columns
    return {
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "missing_constraints": missing_constraints,
        "missing_history_column": missing_history,
    }


def migrate(engine, *, apply: bool = False) -> dict:
    issues = inspect_schema(engine)
    if not apply:
        return issues

    partial = issues["missing_columns"] or issues["missing_constraints"]
    if partial:
        raise RuntimeError(
            "Найдена частично созданная glossary-схема; исправление требует явной миграции: "
            + json.dumps(partial, ensure_ascii=False)
        )

    tables_by_name = {table.name: table for table in TABLES}
    with engine.begin() as connection:
        for table_name in ("domain_terms", "domain_term_translations", "domain_term_aliases"):
            if table_name in issues["missing_tables"]:
                tables_by_name[table_name].create(connection, checkfirst=True)
        if issues["missing_history_column"]:
            connection.execute(text("ALTER TABLE chat_messages ADD COLUMN retrieval_metadata JSON"))
    return inspect_schema(engine)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply missing additive objects")
    args = parser.parse_args()
    result = migrate(get_engine(), apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if any(result.values()):
        return 1
    print("Glossary schema is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
