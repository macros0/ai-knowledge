"""Execute the isolated revision on SQLite and optional private PostgreSQL schema."""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, inspect, text

REVISION = Path(__file__).resolve().parents[1] / "alembic/versions/030b1c2d3e4f_diagnostics_sessions_bundles.py"


def revision():
    spec = importlib.util.spec_from_file_location("diagnostics_revision", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def exercise(connection):
    module = revision()
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
        assert {"diagnostic_sessions", "diagnostic_bundles"} <= set(inspect(connection).get_table_names())
        assert {"active_slot", "audit_receipts", "invitations"} <= {
            item["name"] for item in inspect(connection).get_columns("diagnostic_sessions")}
        connection.execute(text("INSERT INTO diagnostic_sessions "
            "(id, active_slot, status, scope, boot_id, created_by_id, created_by, created_at, expires_at) "
            "VALUES (:id, 1, 'active', 'system', :boot, 'admin', 'admin', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
            {"id": str(uuid4()), "boot": str(uuid4())})
        module.downgrade()
        assert "diagnostic_sessions" not in inspect(connection).get_table_names()
        module.upgrade()
        assert "diagnostic_bundles" in inspect(connection).get_table_names()


def test_revision_upgrade_downgrade_upgrade(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "migration.db"))
    with engine.begin() as connection:
        exercise(connection)
    engine.dispose()


def test_revision_on_private_postgres_schema():
    url = os.getenv("DIAGNOSTICS_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Private PostgreSQL test connection not configured")
    # No migration against the application's public schema; disposable schema has exact ownership.
    schema = "diag_test_" + uuid4().hex
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        try:
            exercise(connection)
        finally:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()
