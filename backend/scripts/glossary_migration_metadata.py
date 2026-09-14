"""Detached glossary metadata and private SQLite simulation sessions.

The application's engine/session factory is never replaced. Only explicitly
selected glossary rows are copied; documents, Qdrant and LLM are not involved.
"""
from contextlib import contextmanager
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import (
    AuditLog, DomainTerm, DomainTermAlias, DomainTermTranslation,
    GlossaryIdentityKey, GlossaryInfotypePrefix, GlossaryInfotypeRule,
    GlossaryMergeReceipt, GlossaryState,
)


TABLES = (DomainTerm, DomainTermAlias, DomainTermTranslation, GlossaryInfotypeRule,
          GlossaryInfotypePrefix, GlossaryIdentityKey, GlossaryState, GlossaryMergeReceipt)


def read_metadata(session) -> dict:
    """Copy every persisted column, deterministically ordered, without ORM caches."""
    rows = {}
    for model in TABLES:
        table = model.__table__
        rows[table.name] = [dict(row) for row in session.execute(
            select(table).order_by(*table.primary_key.columns)).mappings()]
    return rows


def public_metadata(rows: dict) -> dict:
    """Nested backup/audit representation retaining authors, versions and IDs."""
    aliases, translations, prefixes = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows[DomainTermAlias.__tablename__]:
        aliases[row["term_id"]].append(dict(row))
    for row in rows[DomainTermTranslation.__tablename__]:
        translations[row["term_id"]].append(dict(row))
    for row in rows[GlossaryInfotypePrefix.__tablename__]:
        prefixes[row["rule_id"]].append(dict(row))
    terms = [{**row, "aliases": aliases[row["id"]], "translations": translations[row["id"]]}
             for row in rows[DomainTerm.__tablename__]]
    rules = [{**row, "prefixes": sorted(prefixes[row["id"]], key=lambda prefix: prefix["position"])}
             for row in rows[GlossaryInfotypeRule.__tablename__]]
    return {"terms": terms, "rules": rules,
            "state": next(iter(rows[GlossaryState.__tablename__]), None),
            "identity_keys": rows[GlossaryIdentityKey.__tablename__],
            "merge_receipts": rows[GlossaryMergeReceipt.__tablename__]}


def json_value(value):
    if isinstance(value, datetime):
        # SQLite drops timezone information; PostgreSQL returns aware datetimes.
        # Both store UTC in these tables and must hash identically after commit.
        return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()
    raise TypeError(f"Unsupported metadata value: {type(value).__name__}")


@contextmanager
def simulation_session(rows: dict):
    engine = create_engine("sqlite:///:memory:")
    try:
        for model in (*TABLES, AuditLog):
            model.__table__.create(engine)
        with engine.begin() as connection:
            for model in TABLES:
                values = rows[model.__tablename__]
                if values:
                    connection.execute(model.__table__.insert(), values)
        with Session(engine, expire_on_commit=False) as session:
            yield session
    finally:
        engine.dispose()
