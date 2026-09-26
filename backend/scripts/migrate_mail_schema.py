"""Inspect or add missing mail schema objects in a create_all-managed dev DB.

Run without --apply first, with writers stopped and a verified backup. This
does not stamp Alembic, change existing column definitions, or touch corpus data.
"""
from __future__ import annotations

import argparse
import json

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, inspect, text

from app.db import models  # noqa: F401 - registers canonical tables
from app.db.base import Base

NEW_TABLES = ("document_sources", "document_generation_states", "document_generations")
NEW_COLUMNS = {
    "documents": {"mail_fingerprint", "parser_version", "parse_warnings"},
    "document_chunks": {"source_id"},
    "okf_concepts": {"source_id"},
    "okf_attachments": {"source_id"},
    "document_staging": {"parser_version", "source_file_hash", "generation_id"},
    "document_generations": {"publication_hash"},
}
NEW_INDEXES = {
    "ix_documents_mail_fingerprint", "ix_document_chunks_source_id",
    "ix_okf_concepts_source_id", "ix_okf_attachments_source_id",
    "ix_document_sources_doc_parent", "ix_document_generations_doc_id",
}
TABLES = set(NEW_TABLES) | set(NEW_COLUMNS)


def _diffs(connection):
    context = MigrationContext.configure(connection, opts={
        "include_object": lambda _obj, name, type_, _reflected, _compare_to:
            type_ != "table" or name in TABLES,
    })
    return [item for diff in compare_metadata(context, Base.metadata)
            for item in (diff if isinstance(diff, list) else [diff])]


def _is_additive(diff):
    kind = diff[0]
    if kind == "add_table":
        return diff[1].name in NEW_TABLES
    if kind == "add_column":
        return diff[3].name in NEW_COLUMNS.get(diff[2], set()) and diff[3].nullable
    if kind == "add_index":
        return diff[1].name in NEW_INDEXES
    return False


def _description(diff):
    kind = diff[0]
    if kind.endswith("table"):
        return f"{kind}: {diff[1].name}"
    if kind.endswith("index"):
        return f"{kind}: {diff[1].table.name}.{diff[1].name}"
    if kind.endswith("column") or kind.startswith("modify_"):
        return f"{kind}: {diff[2]}.{getattr(diff[3], 'name', diff[3])}"
    return f"{kind}: {getattr(getattr(diff[1], 'table', None), 'name', 'schema')}"


def migrate(engine, *, apply=False):
    with engine.begin() as connection:
        inspector = inspect(connection)
        versions = (list(connection.scalars(text("SELECT version_num FROM alembic_version")))
                    if inspector.has_table("alembic_version") else [])
        before = _diffs(connection)
        conflicts = [_description(diff) for diff in before if not _is_additive(diff)]
        missing = [_description(diff) for diff in before if _is_additive(diff)]
        if apply and conflicts:
            raise ValueError("Mail schema has incompatible drift: " + "; ".join(conflicts))
        if apply:
            for name in NEW_TABLES:
                Base.metadata.tables[name].create(connection, checkfirst=True)
            operations = Operations(MigrationContext.configure(connection))
            for name, columns in NEW_COLUMNS.items():
                existing = {column["name"] for column in inspect(connection).get_columns(name)}
                for column_name in sorted(columns - existing):
                    column = Base.metadata.tables[name].c[column_name]
                    operations.add_column(name, Column(column_name, column.type, nullable=True))
            for name in TABLES:
                for index in Base.metadata.tables[name].indexes:
                    if index.name in NEW_INDEXES:
                        index.create(connection, checkfirst=True)
        after = _diffs(connection) if apply else before
        return {"ready": not after, "applied": bool(apply and before),
                "missing": missing, "conflicts": conflicts, "alembic_versions": versions,
                "remaining": [_description(diff) for diff in after]}


def main():
    from app.config import get_settings
    from app.db.session import get_engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and get_settings().environment != "development":
        parser.error("--apply is only supported for development databases")
    result = migrate(get_engine(), apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
