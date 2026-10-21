import pytest
from sqlalchemy import create_engine, inspect, text
from app.services.audit_schema import upgrade_audit_schema, validate_audit_schema

LEGACY = "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL, user_id VARCHAR(255), username VARCHAR(255), action_type VARCHAR(64) NOT NULL, target_type VARCHAR(64), target_id VARCHAR(255), old_value JSON, new_value JSON, ip_address VARCHAR(64), meta JSON)"

def test_nonempty_legacy_upgrade_and_repeat_preserves_history(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as c:
        c.execute(text(LEGACY))
        c.execute(text("INSERT INTO audit_log(id,created_at,action_type,username) VALUES (1,'2026-10-04','document_upload','old')"))
        upgrade_audit_schema(c)
        validate_audit_schema(c)
        upgrade_audit_schema(c)
        assert c.execute(text("SELECT username,event_id FROM audit_log")).one()==('old',None)
        assert c.execute(text("SELECT count(*) FROM audit_outbox")).scalar()==0

def test_unknown_partial_schema_is_refused_without_modification(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path / 'partial.db'}")
    with engine.begin() as c:
        c.execute(text(LEGACY))
        c.execute(text("ALTER TABLE audit_log ADD COLUMN event_id VARCHAR(36)"))
        with pytest.raises(ValueError):upgrade_audit_schema(c)
        assert 'audit_outbox' not in inspect(c).get_table_names()


@pytest.mark.parametrize("drift", ["status_check", "attempt_check", "cascade", "payload_type", "literal_decoy"])
def test_named_but_incompatible_outbox_is_refused(tmp_path, drift):
    from app.db.models import AuditLog
    from app.services.audit_hold import hold_pending
    engine = create_engine(f"sqlite:///{tmp_path / 'owned-drift.db'}")
    try:
        AuditLog.__table__.create(engine)
        status = "status IN ('pending', 'held', 'stdout_written')"
        attempt = "attempt_count >= 0"
        cascade = ""
        payload_type = "TEXT"
        if drift == "status_check": status = "status <> 'invalid'"
        if drift == "literal_decoy": status = "status IN ('pend(ing', 'held', 'stdout_written')"
        if drift == "attempt_check": attempt = "attempt_count >= -1"
        if drift == "cascade": cascade = " ON DELETE CASCADE"
        if drift == "payload_type": payload_type = "VARCHAR(32)"
        with engine.begin() as c:
            c.execute(text(f"CREATE TABLE audit_outbox (audit_id INTEGER NOT NULL PRIMARY KEY, payload {payload_type} NOT NULL, status VARCHAR(16) NOT NULL, attempt_count INTEGER NOT NULL, next_attempt_at DATETIME NOT NULL, last_error_code VARCHAR(32), stdout_written_at DATETIME, CONSTRAINT ck_audit_outbox_status CHECK ({status}), CONSTRAINT ck_audit_outbox_attempt_count CHECK ({attempt}), FOREIGN KEY(audit_id) REFERENCES audit_log(id){cascade})"))
            c.execute(text("CREATE INDEX ix_audit_outbox_delivery ON audit_outbox(status,next_attempt_at,audit_id)"))
            with pytest.raises(ValueError): validate_audit_schema(c)
        with pytest.raises(ValueError): hold_pending(engine)
    finally: engine.dispose()


def test_fixture_only_empty_downgrade_preserves_legacy_rows(tmp_path):
    from pathlib import Path
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    engine = create_engine(f"sqlite:///{tmp_path / 'owned-downgrade.db'}")
    try:
        path = Path(__file__).resolve().parents[1] / 'alembic/versions/066b1c2d3e4f_audit_outbox.py'
        spec = importlib.util.spec_from_file_location('audit_fixture_migration', path)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        with engine.begin() as connection:
            connection.execute(text(LEGACY))
            connection.execute(text("INSERT INTO audit_log(id,created_at,action_type,username) VALUES(1,'2026-10-04','document_upload','legacy')"))
            from app.services.audit_schema_v1 import upgrade_audit_schema as upgrade_frozen
            upgrade_frozen(connection)
            with Operations.context(MigrationContext.configure(connection)): migration.downgrade()
            assert 'audit_outbox' not in inspect(connection).get_table_names()
            assert 'event_id' not in {c['name'] for c in inspect(connection).get_columns('audit_log')}
            assert connection.execute(text('SELECT id,username FROM audit_log')).one() == (1, 'legacy')
    finally: engine.dispose()
