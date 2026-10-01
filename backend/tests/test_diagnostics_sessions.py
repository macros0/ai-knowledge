"""Capture control must never depend on successful audit to stop collecting."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.auth.models import User
from app.config import Settings
from app.db.models import AuditLog, DiagnosticSession
from app.db.session import session_scope
from app.services import audit
from app.services.diagnostics.schema import DiagnosticContext, DiagnosticLimits
from app.services.diagnostics.sessions import DiagnosticSessionService, DiagnosticControlError
from app.services.diagnostics.store import DiagnosticStore


@pytest.fixture
def service(tmp_path):
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    store.open()
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", diagnostics_dir=tmp_path / "spool",
                        diagnostics_capture_enabled=True)
    instance = DiagnosticSessionService(store, settings)
    yield instance
    store.close()


def actor():
    return User(user_id="admin-1", username="Administrator", roles=["admin"])


def test_start_requires_committed_audit(service, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("CANARY_DB_PASSWORD")
    monkeypatch.setattr(audit, "record_in_session", fail)
    with pytest.raises(DiagnosticControlError) as error:
        service.start("system", 15, actor())
    assert error.value.code == "diagnostic_audit_unavailable"
    assert service.active_for(DiagnosticContext(), "stage_started") is None
    with session_scope() as db:
        assert list(db.scalars(select(DiagnosticSession))) == []


def test_concurrent_start_only_one_wins(service):
    def start(_):
        try:
            return service.start("system", 15, actor()).id
        except DiagnosticControlError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, range(2)))
    assert results.count("diagnostic_session_active") == 1
    with session_scope() as db:
        assert len(list(db.scalars(select(DiagnosticSession).where(DiagnosticSession.active_slot == 1)))) == 1


def test_stop_is_idempotent(service):
    started = service.start("system", 15, actor())
    assert service.stop(started.id, actor(), "manual").status == "stopped"
    assert service.stop(started.id, actor(), "manual").status == "stopped"
    with session_scope() as db:
        entries = list(db.scalars(select(AuditLog).where(AuditLog.action_type == "diagnostic_session_stopped")))
        assert len(entries) == 1


def test_monotonic_deadline_survives_wall_clock_change(service):
    mono = [100.0]
    wall = [datetime(2026, 9, 27, tzinfo=timezone.utc)]
    service.monotonic = lambda: mono[0]
    service.utcnow = lambda: wall[0]
    started = service.start("system", 15, actor())
    wall[0] -= timedelta(days=10)
    mono[0] = 1000.1
    service.tick(wall[0], mono[0])
    assert service.active_for(DiagnosticContext(), "stage_started") is None
    with session_scope() as db:
        assert db.get(DiagnosticSession, started.id).stop_reason == "expired"


def test_restart_never_resumes_capture(service):
    started = service.start("interface", 15, actor())
    new = DiagnosticSessionService(service.store, service.settings)
    new.recover(str(uuid4()))
    assert new.active_for(DiagnosticContext(), "render_failed") is None
    with session_scope() as db:
        row = db.get(DiagnosticSession, started.id)
        assert row.status == "stopped"
        assert row.stop_reason == "server_restarted"


def test_expiry_with_database_down(service, monkeypatch):
    started = service.start("system", 5, actor())
    import app.services.diagnostics.sessions as module
    real_scope = module.session_scope
    def down():
        raise ConnectionError("CANARY")
    monkeypatch.setattr(module, "session_scope", down)
    service.tick(service.utcnow() + timedelta(minutes=6), service.monotonic() + 301)
    assert service.active_for(DiagnosticContext(), "stage_started") is None
    assert service.status()["audit_pending"]
    assert service.journal.items()
    monkeypatch.setattr(module, "session_scope", real_scope)
    service.reconcile_control_events()
    with session_scope() as db:
        assert db.get(DiagnosticSession, started.id).status == "stopped"


def test_deferred_audit_replay_is_idempotent(service, monkeypatch):
    started = service.start("system", 15, actor())
    real = audit.record_in_session
    monkeypatch.setattr(audit, "record_in_session", lambda *a, **k: (_ for _ in ()).throw(OSError("audit down")))
    service.stop(started.id, actor(), "manual")
    events = service.journal.items()
    assert len(events) == 1
    monkeypatch.setattr(audit, "record_in_session", real)
    service.reconcile_control_events()
    # A crash can replay the same durable control event after SQL commit.
    service.journal.append(events[0])
    service.reconcile_control_events()
    with session_scope() as db:
        assert len(list(db.scalars(select(AuditLog).where(AuditLog.action_type == "diagnostic_session_stopped")))) == 1


def test_stop_with_full_disk_marks_gap(service, monkeypatch):
    started = service.start("system", 15, actor())
    import app.services.diagnostics.sessions as module
    def down(*a, **k):
        raise OSError("unavailable")
    monkeypatch.setattr(module, "session_scope", down)
    monkeypatch.setattr(service.store, "write_bytes", down)
    stopped = service.stop(started.id, actor(), "manual")
    assert stopped.status == "stopped"
    assert service.active_for(DiagnosticContext(), "stage_started") is None
    assert service.status()["audit_gap"]


def test_scope_filters_before_recording_and_size_stops(service):
    started = service.start("interface", 15, actor())
    assert service.active_for(DiagnosticContext(), "stage_started") is None
    assert service.active_for(DiagnosticContext(), "render_failed") == started.id
    service.record_written(started.id, service.store.limits.session_bytes)
    assert service.active_for(DiagnosticContext(), "render_failed") is None
    service.tick(service.utcnow(), service.monotonic())
    with session_scope() as db:
        assert db.get(DiagnosticSession, started.id).stop_reason == "size_limit"
