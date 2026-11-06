"""Real transaction shared-lock drain and owned operator connection loss."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from tests.support.replica_postgres import disposable_postgres
from tests.test_replica_maintenance import installed_maintenance as installed_maintenance, stopped
from app.db.models import ConfigRevision, MaintenanceState
from app.db.session import get_engine, get_session_factory, session_scope
from app.services.work.maintenance_control import (MaintenanceOperator, MaintenanceRefused,
    bind_runtime_epoch, enter_maintenance, leave_maintenance)


@pytest.fixture(autouse=True)
def postgres():
    with disposable_postgres():
        yield


def test_pg_entry_waits_for_transaction_and_old_process_stays_fenced(installed_maintenance, monkeypatch):
    ready, release, entered = threading.Event(), threading.Event(), threading.Event()
    engine = get_engine()
    operation_id = uuid4().hex

    def writer():
        with session_scope() as s:
            s.add(ConfigRevision(domain="pre-fence", revision=1))
            s.flush()
            ready.set()
            assert release.wait(10)

    def operator():
        with MaintenanceOperator(engine, operation_id=operation_id, owner_id="owned-test") as op:
            with op.transaction() as s:
                epoch = enter_maintenance(s, operation_id, kind="backup",
                    intended_counts={"frontend": 2, "api": 2, "worker": 2})
            entered.set()
            assert epoch == 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(writer)
        assert ready.wait(10)
        b = pool.submit(operator)
        try:
            assert not entered.wait(0.15)
        finally:
            release.set()
        a.result(timeout=15)
        b.result(timeout=15)
    with pytest.raises(MaintenanceRefused):
        with session_scope():
            pass
    with MaintenanceOperator(engine, operation_id=operation_id, owner_id="owned-test") as op:
        with op.transaction() as s:
            leave_maintenance(s, operation_id, writer_receipts=stopped())
    with pytest.raises(MaintenanceRefused, match="epoch"):
        with session_scope():
            pass
    fresh = installed_maintenance.model_copy(deep=True)
    fresh._maintenance_epoch = None
    monkeypatch.setattr("app.config.get_settings", lambda: fresh)
    with get_session_factory()() as s:
        bind_runtime_epoch(s, fresh)
    with session_scope() as s:
        assert s.get(ConfigRevision, "pre-fence").revision == 1


def test_pg_lost_operator_cannot_finish_through_new_connection(installed_maintenance):
    engine, operation_id = get_engine(), uuid4().hex
    with MaintenanceOperator(engine, operation_id=operation_id, owner_id="owned-test") as op:
        with op.transaction() as s:
            enter_maintenance(s, operation_id, kind="backup",
                intended_counts={"frontend": 2, "api": 2, "worker": 2})
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as killer:
            assert killer.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": op.backend_pid}) is True
        with pytest.raises((OperationalError, MaintenanceRefused)):
            with op.transaction() as s:
                leave_maintenance(s, operation_id, writer_receipts=stopped())
        assert op.connection.invalidated
        with pytest.raises(MaintenanceRefused):
            with op.transaction():
                pytest.fail("Lost operator must never reconnect")
    with engine.connect() as conn:
        row = conn.execute(select(MaintenanceState.__table__)).mappings().one()
        assert row["enabled"] is True and row["operation_id"] == operation_id


def test_pg_audit_marker_is_fenced_and_replacement_replays_same_event(installed_maintenance, monkeypatch):
    from app.db.models import AuditOutbox
    from app.services import audit
    from app.services.audit_outbox import AuditDispatcher
    settings = installed_maintenance
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    settings.audit_stdout_mode = "durable"
    with session_scope() as s:
        audit.record_in_session(s, action_type=audit.DOCUMENT_UPLOAD)
        original_id = s.scalar(select(AuditOutbox.event_id))
    engine, operation_id, written = get_engine(), uuid4().hex, []

    def sink(payload):
        written.append(payload)
        with MaintenanceOperator(engine, operation_id=operation_id, owner_id="owned-audit-test", settings=settings) as op:
            with op.transaction() as s:
                enter_maintenance(s, operation_id, kind="backup",
                    intended_counts={"frontend": 2, "api": 2, "worker": 2})

    old = AuditDispatcher(settings, write_line=sink)
    try:
        assert old.tick() == 0
        with engine.connect() as conn:
            assert conn.scalar(select(AuditOutbox.status).where(AuditOutbox.event_id == original_id)) == "pending"
        with MaintenanceOperator(engine, operation_id=operation_id, owner_id="owned-audit-test", settings=settings) as op:
            with op.transaction() as s:
                leave_maintenance(s, operation_id, writer_receipts=stopped())
        old._database_retry_at = 0
        assert old.tick() == 0
        assert len(written) == 1
    finally:
        old.stop()
    fresh = settings.model_copy(deep=True)
    fresh._maintenance_epoch = None
    monkeypatch.setattr("app.config.get_settings", lambda: fresh)
    with get_session_factory()() as s:
        bind_runtime_epoch(s, fresh)
    replacement = AuditDispatcher(fresh, write_line=written.append)
    try:
        assert replacement.tick() >= 1
    finally:
        replacement.stop()
    assert sum(json.loads(payload)["event_id"] == original_id for payload in written) == 2
    with session_scope() as s:
        assert s.scalar(select(AuditOutbox.status).where(AuditOutbox.event_id == original_id)) == "stdout_written"
