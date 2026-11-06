"""Audit leadership uses the same PostgreSQL session as queue mutations."""
import json
from datetime import datetime, timedelta, timezone
from multiprocessing import get_context
from pathlib import Path

import pytest
from sqlalchemy import event, select, text

from app.config import Settings
from app.db.models import AuditLog, AuditOutbox
from app.db.session import get_engine, session_scope
from app.services import audit
from app.services.audit_outbox import AuditDispatcher
from tests.support.replica_postgres import disposable_postgres


def _settings(root):
    settings = Settings(_env_file=None, data_dir=root, audit_stdout_mode="durable")
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    return settings


def _row():
    with session_scope() as session:
        row = session.scalar(select(AuditOutbox))
        return row.status, row.attempt_count, row.payload


def test_two_apis_use_one_audit_leader_with_separate_disks(tmp_path, monkeypatch):
    with disposable_postgres():
        first_settings = _settings(tmp_path / "api-a")
        monkeypatch.setattr("app.config.get_settings", lambda: first_settings)
        audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD)
        first_bytes, second_bytes = [], []
        first = AuditDispatcher(first_settings, write_line=first_bytes.append)
        second = AuditDispatcher(_settings(tmp_path / "api-b"), write_line=second_bytes.append)
        # Hold the first owner's connection after one delivery, then add work.
        try:
            assert first.tick() == 1
            audit.get_audit().append(action_type=audit.DOCUMENT_DELETE)
            statements = []
            def observed(_conn, _cursor, statement, *_args):
                statements.append(statement)
            event.listen(get_engine(), "before_cursor_execute", observed)
            try:
                assert second.tick() == 0
            finally:
                event.remove(get_engine(), "before_cursor_execute", observed)
            assert not any("audit_outbox" in statement for statement in statements)
            assert second.status()["state"] == "standby"
            assert not second_bytes
            assert first.tick() == 1
            assert len(first_bytes) == 2
            assert not (first_settings.data_dir / "audit").exists()
        finally:
            first.stop()
            second.stop()


@pytest.mark.parametrize("db_enabled", [True, False])
def test_audit_failover_replays_same_event_id(tmp_path, monkeypatch, db_enabled):
    with disposable_postgres():
        settings = _settings(tmp_path / "api-a")
        settings.audit_db_enabled = db_enabled
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD)
        original = _row()[2].encode()
        with session_scope() as session:
            assert (session.scalar(select(AuditLog)) is not None) is db_enabled
        written = []
        first = None

        def lose_connection_after_write(payload):
            written.append(payload)
            # Terminate ONLY this dispatcher's dedicated, owned test connection.
            pid = first._leadership.backend_pid
            with get_engine().begin() as connection:
                assert connection.scalar(text("SELECT datname=current_database() FROM pg_stat_activity WHERE pid=:pid"), {"pid": pid}) is True
                assert connection.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}) is True

        first = AuditDispatcher(settings, write_line=lose_connection_after_write)
        second = AuditDispatcher(_settings(tmp_path / "api-b"), write_line=written.append)
        try:
            assert first.tick() == 0
            assert _row()[:2] == ("pending", 1)
            assert second.tick() == 1
            assert _row()[:2] == ("stdout_written", 2)
            assert written == [original, original]
            assert {json.loads(value)["event_id"] for value in written} == {json.loads(original)["event_id"]}
        finally:
            first.stop()
            second.stop()


def test_replica_audit_event_and_eligibility_use_database_time(tmp_path, monkeypatch):
    with disposable_postgres():
        settings = _settings(tmp_path)
        monkeypatch.setattr("app.config.get_settings", lambda: settings)

        class SkewedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(days=30)

        monkeypatch.setattr(audit, "datetime", SkewedClock)
        item = audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD)
        with session_scope() as session:
            now = session.scalar(text("SELECT clock_timestamp()"))
            row = session.scalar(select(AuditOutbox))
            assert abs((row.created_at - now).total_seconds()) < 10
            assert abs((row.next_attempt_at - now).total_seconds()) < 10
            assert datetime.fromisoformat(json.loads(row.payload)["timestamp"]) == item["created_at"]
        written = []
        sender = AuditDispatcher(settings, write_line=written.append,
            clock=lambda: datetime(1900, 1, 1, tzinfo=timezone.utc))
        try:
            assert sender.tick() == 1
        finally:
            sender.stop()


def _other_api(url, root, pipe):
    import app.config
    from app.db.session import configure_database
    app.config.get_settings = lambda: _settings(Path(root))
    configure_database(url)
    written = []
    sender = AuditDispatcher(app.config.get_settings(), write_line=written.append)
    try:
        pipe.send("ready")
        while pipe.poll(30):
            if pipe.recv() == "stop":
                return
            count = sender.tick()
            pipe.send((count, sender.status()["state"], tuple(written)))
        raise RuntimeError("Audit acceptance control timed out")
    finally:
        sender.stop()
        get_engine().dispose()
        pipe.close()


def test_audit_failover_to_another_api_process(tmp_path, monkeypatch):
    with disposable_postgres() as url:
        settings = _settings(tmp_path / "api-a")
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        written = []
        sender = AuditDispatcher(settings, write_line=written.append)
        context = get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_other_api, args=(url, str(tmp_path / "api-b"), child))
        process.start()
        child.close()

        def probe():
            parent.send("tick")
            assert parent.poll(15)
            return parent.recv()

        try:
            assert parent.poll(25) and parent.recv() == "ready"
            assert sender.tick() == 0  # Own the lock with an empty queue.
            item = audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD)
            assert probe() == (0, "standby", ())
            sender.stop()  # Physical connection close releases ownership.
            assert sender.tick() == 0  # A stopped owner must not reacquire.
            count, state, replay = probe()
            assert count == 1 and state == "running" and len(replay) == 1
            assert json.loads(replay[0])["event_id"] == item["event_id"]
            assert not written
            assert _row()[:2] == ("stdout_written", 1)
        finally:
            sender.stop()
            if process.is_alive():
                parent.send("stop")
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join(5)
            parent.close()
        assert process.exitcode == 0
