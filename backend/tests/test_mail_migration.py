"""Mail schema upgrade/rollback on real migrations, never Base.create_all.

Optional PostgreSQL: MAIL_MIGRATION_POSTGRES_ADMIN_URL names an isolated test
server. Each invocation creates a new mail_migration_<uuid> database and leaves
it for inspection; existing databases are never migrated or cleared.
"""
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, Table, create_engine, event, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.config import get_settings
from app.db.base import Base
from app.db.models import Document, DocumentSource
from app.services.source_store import replace_sources

BACKEND = Path(__file__).resolve().parents[1]
BEFORE_MAIL = "d7e8f9a0b1c2"
LEGACY_TABLES = ("documents", "document_chunks", "okf_concepts", "okf_attachments")
MAIL_TABLES = {*LEGACY_TABLES, "document_sources", "document_staging",
               "document_generations", "document_generation_states"}


@pytest.fixture(params=["sqlite", "postgres"])
def migration_db(request, tmp_path, monkeypatch, record_property):
    if request.param == "postgres":
        admin_url = os.environ.get("MAIL_MIGRATION_POSTGRES_ADMIN_URL")
        if not admin_url:
            pytest.skip("MAIL_MIGRATION_POSTGRES_ADMIN_URL is not configured")
        url = make_url(admin_url)
        assert url.drivername.startswith("postgresql")
        name = "mail_migration_" + uuid4().hex
        admin = create_engine(url, isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as conn:
                conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
        finally:
            admin.dispose()
        db_url = url.set(database=name).render_as_string(hide_password=False)
        record_property("isolated_database", name)
    else:
        db_url = f"sqlite:///{(tmp_path / 'mail-migration.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    config = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(db_url)
    if request.param == "sqlite":
        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
    try:
        assert inspect(engine).get_table_names() == []
        yield config, engine
    finally:
        engine.dispose()
        get_settings.cache_clear()


def _snapshot(engine):
    result = {}
    with engine.connect() as conn:
        for name in LEGACY_TABLES:
            table = Table(name, MetaData(), autoload_with=conn)
            result[name] = [dict(row) for row in conn.execute(select(table).order_by(table.c.id)).mappings()]
    return result


def _seed_legacy(engine):
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    with engine.begin() as conn:
        tables = {name: Table(name, MetaData(), autoload_with=conn) for name in LEGACY_TABLES}
        for index, suffix in enumerate(("docx", "xlsx", "pdf")):
            doc_id = f"legacy{index}"
            content = f"Канонический текст {suffix} 😀"
            digest = hashlib.sha256(content.encode()).hexdigest()
            conn.execute(tables["documents"].insert().values(
                id=doc_id, filename=f"исходник.{suffix}", content_type="test/document", size=123,
                status="done", total_chunks=1, processed_chunks=1, okf_concept_count=1,
                created_at=now, updated_at=now,
            ))
            conn.execute(tables["document_chunks"].insert().values(
                doc_id=doc_id, chunk_index=0, content=content, content_hash=digest,
                char_count=len(content), created_at=now,
            ))
            conn.execute(tables["okf_concepts"].insert().values(
                doc_id=doc_id, slug="legacy", title="Старый концепт", type="concept",
                content=content, tags=["старый"], relations=[], chunk_index=0, created_at=now,
                source_spans=[{"start": 0, "end": len(content), "chunk_hash": digest}],
            ))
            conn.execute(tables["okf_attachments"].insert().values(
                doc_id=doc_id, name="same.bin", kind="other", caption="Старое вложение",
                saved_path="attachments/same.bin", is_processable=False, created_at=now,
            ))


def _assert_legacy(engine, before):
    after = _snapshot(engine)
    for table, rows in before.items():
        assert len(after[table]) == len(rows)
        for original, current in zip(rows, after[table], strict=True):
            assert {key: current[key] for key in original} == original


@pytest.mark.parametrize("populated", [False, True])
def test_mail_migrations_roundtrip_preserves_legacy_data(migration_db, populated):
    config, engine = migration_db
    command.upgrade(config, BEFORE_MAIL)
    if populated:
        _seed_legacy(engine)
    before = _snapshot(engine)
    command.upgrade(config, "head")
    _assert_legacy(engine, before)
    inspector = inspect(engine)
    for table in LEGACY_TABLES[1:]:
        assert next(column for column in inspector.get_columns(table) if column["name"] == "source_id")["nullable"]
        with engine.connect() as conn:
            assert conn.execute(text(f"SELECT count(*) FROM {table} WHERE source_id IS NOT NULL")).scalar() == 0
    assert inspector.get_pk_constraint("document_sources")["constrained_columns"] == ["doc_id", "source_id"]
    assert any(index["column_names"] == ["doc_id", "parent_source_id"]
               for index in inspector.get_indexes("document_sources"))
    with engine.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn, opts={
            "include_object": lambda _obj, name, type_, _reflected, _compare_to:
                type_ != "table" or name in MAIL_TABLES,
        }), Base.metadata)
    flat = [item for diff in diffs for item in (diff if isinstance(diff, list) else [diff])]
    assert not flat, flat
    command.downgrade(config, BEFORE_MAIL)
    assert "document_sources" not in inspect(engine).get_table_names()
    _assert_legacy(engine, before)
    command.upgrade(config, "head")
    _assert_legacy(engine, before)


def test_migrated_source_tree_constraints_and_restart(migration_db):
    config, engine = migration_db
    command.upgrade(config, "head")
    metadata = {"subject": "Кириллица 😀", "references": ["id" + str(i) for i in range(1000)]}
    warnings = [{"code": "unsupported_attachment_method", "source_id": "root/0"}]
    with Session(engine) as session, session.begin():
        session.add_all([Document(id=doc_id, filename="source.msg") for doc_id in ("first", "second")])
        session.flush()
        replace_sources(session, "first", [
            {"source_id": "root", "metadata": metadata},
            {"source_id": "root/0", "parent_source_id": "root", "display_name": "same.msg",
             "artifact_kind": "container_only", "container_source_id": "root", "warnings": warnings},
            {"source_id": "root/1", "parent_source_id": "root", "display_name": "same.msg"},
        ])
        replace_sources(session, "second", [{"source_id": "root"}])
    engine.dispose()  # Reopen connections: persisted JSON and file-less nodes must survive.
    with Session(engine) as session:
        assert session.get(DocumentSource, ("first", "root")).metadata_json == metadata
        child = session.get(DocumentSource, ("first", "root/0"))
        assert child.saved_path is None and child.warnings == warnings
        assert session.get(DocumentSource, ("first", "root/1")).display_name == child.display_name
    for doc_id, source_id, parent_id in [("first", "root", None), ("second", "root/2", "root/0")]:
        with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
            session.add(DocumentSource(doc_id=doc_id, source_id=source_id, parent_source_id=parent_id))
            session.flush()
    with Session(engine) as session, session.begin():
        session.execute(text("DELETE FROM documents WHERE id='first'"))
    with Session(engine) as session:
        assert session.scalar(select(DocumentSource).where(DocumentSource.doc_id == "first")) is None
        assert session.get(DocumentSource, ("second", "root")) is not None


def test_dev_mail_repair_is_additive_idempotent_and_does_not_stamp(migration_db):
    from scripts.migrate_mail_schema import migrate

    config, engine = migration_db
    command.upgrade(config, BEFORE_MAIL)
    _seed_legacy(engine)
    before = _snapshot(engine)
    # Reproduce create_all drift: new tables exist, columns on old tables do not.
    for name in ("document_sources", "document_generation_states", "document_generations"):
        Base.metadata.tables[name].create(engine)
    report = migrate(engine)
    assert report["ready"] is False and report["missing"] and not report["conflicts"]
    assert report["alembic_versions"] == [BEFORE_MAIL]
    assert "source_id" not in {column["name"] for column in inspect(engine).get_columns("document_chunks")}
    assert migrate(engine, apply=True)["ready"] is True
    _assert_legacy(engine, before)
    again = migrate(engine, apply=True)
    assert again["ready"] is True and again["applied"] is False
    assert again["alembic_versions"] == [BEFORE_MAIL]
    _assert_legacy(engine, before)


def test_dev_mail_repair_refuses_incompatible_existing_column_before_writes(migration_db):
    from scripts.migrate_mail_schema import migrate

    config, engine = migration_db
    command.upgrade(config, BEFORE_MAIL)
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE document_chunks ADD COLUMN source_id INTEGER")
    report = migrate(engine)
    assert report["conflicts"] and not report["ready"]
    with pytest.raises(ValueError, match="incompatible drift"):
        migrate(engine, apply=True)
    assert "document_sources" not in inspect(engine).get_table_names()
