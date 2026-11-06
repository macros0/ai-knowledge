"""Shared audited capture control never depends on an individual API disk."""
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, DiagnosticSession
from app.db.session import session_scope
from app.services import audit
from app.services.diagnostics.runtime import initialize_diagnostics
from app.services.diagnostics.schema import DiagnosticContext
from app.services.diagnostics.sessions import DiagnosticControlError
from tests.test_diagnostics_lifecycle import settings as diagnostic_settings
from tests.test_diagnostics_sessions import actor


@pytest.fixture
def replicas(tmp_path, monkeypatch):
    runtimes = []
    for name in ("api-a", "api-b"):
        settings = diagnostic_settings(tmp_path / name)
        settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
        runtimes.append(initialize_diagnostics(settings))
    monkeypatch.setattr("app.config.get_settings", lambda: runtimes[0].settings)
    assert all(runtime.available for runtime in runtimes)
    try:
        yield tuple(runtimes)
    finally:
        for runtime in runtimes:
            runtime.stop()


def test_capture_started_on_one_api_is_visible_and_stoppable_on_another(replicas):
    first, second = replicas
    capture = first.sessions.start("interface", 5, actor())
    second.sessions.tick()
    assert second.sessions.active_for(DiagnosticContext(), "render_failed") == str(capture.id)
    assert second.sessions.status()["session"]["id"] == str(capture.id)
    assert second.sessions.stop(capture.id, actor()).status == "stopped"
    first.sessions.tick()
    assert first.sessions.active_for(DiagnosticContext(), "render_failed") is None
    with session_scope() as session:
        assert session.get(DiagnosticSession, str(capture.id)).active_slot is None
        assert len(list(session.scalars(select(AuditLog).where(
            AuditLog.action_type == "diagnostic_session_stopped")))) == 1
    assert not list(first.store.root.glob("control/deferred.jsonl"))
    assert not list(second.store.root.glob("control/deferred.jsonl"))


def test_stopping_one_replica_does_not_stop_the_shared_capture(replicas):
    first, second = replicas
    assert first.start() and second.start()
    capture = first.sessions.start("interface", 5, actor())
    first.stop()
    second.sessions.tick()
    assert second.sessions.active_for(DiagnosticContext(), "render_failed") == str(capture.id)
    with session_scope() as session:
        assert session.get(DiagnosticSession, str(capture.id)).status == "active"


def test_failed_stop_is_not_accepted_as_a_file_only_audit_intent(replicas, monkeypatch):
    first, _ = replicas
    capture = first.sessions.start("system", 5, actor())
    record = audit.record_in_session
    monkeypatch.setattr(audit, "record_in_session", lambda *_a, **_k: (_ for _ in ()).throw(OSError("audit down")))
    with pytest.raises(DiagnosticControlError, match="diagnostic_audit_unavailable"):
        first.sessions.stop(capture.id, actor())
    assert first.sessions.active_for(DiagnosticContext(), "stage_started") is None
    assert not (first.store.root / "control/deferred.jsonl").exists()
    with session_scope() as session:
        assert session.get(DiagnosticSession, str(capture.id)).status == "active"
    monkeypatch.setattr(audit, "record_in_session", record)
    first.sessions.tick()
    with session_scope() as session:
        assert session.get(DiagnosticSession, str(capture.id)).status == "stopped"


def test_capture_selector_has_a_bounded_lease_and_does_no_sql(replicas, monkeypatch):
    first, _ = replicas
    monotonic = [100.0]
    first.sessions.monotonic = lambda: monotonic[0]
    capture = first.sessions.start("system", 5, actor())
    module = importlib.import_module(type(first.sessions).__module__)

    @contextmanager
    def down():
        raise OSError("database down")
        yield

    monkeypatch.setattr(module, "session_scope", down)
    assert first.sessions.active_for(DiagnosticContext(), "stage_started") == str(capture.id)
    monotonic[0] = 111.0
    assert first.sessions.active_for(DiagnosticContext(), "stage_started") is None
    first.sessions.tick()
    assert first.sessions.status()["audit_gap"] is True


def test_frontend_control_reads_shared_sql_without_local_projection(replicas):
    from app.services.diagnostics.ingest import current_control
    first, second = replicas
    capture = first.sessions.start("interface", 5, actor())
    control = current_control(second.store)
    assert control["active"] is True and control["session_id"] == str(capture.id)
    second.sessions.stop(capture.id, actor())
    assert current_control(first.store)["active"] is False


def test_delayed_sql_refresh_cannot_extend_selector_lease(replicas, monkeypatch):
    first, _ = replicas
    monotonic = [100.0]
    first.sessions.monotonic = lambda: monotonic[0]
    first.sessions.start("system", 5, actor())
    module = importlib.import_module(type(first.sessions).__module__)
    scope = module.session_scope

    @contextmanager
    def delayed():
        monotonic[0] = 111.0  # Time passes while the DB refresh is blocked.
        with scope() as session:
            yield session

    monkeypatch.setattr(module, "session_scope", delayed)
    first.sessions.tick()
    assert first.sessions.active_for(DiagnosticContext(), "stage_started") is None


def test_stale_browser_selector_cannot_join_a_capture_stopped_on_another_api(replicas):
    first, second = replicas
    capture = first.sessions.start("interface", 5, actor())
    second.sessions.tick()
    first.sessions.stop(capture.id, actor())
    with pytest.raises(DiagnosticControlError, match="diagnostic_browser_not_joined"):
        second.browser.join(None, actor())
    with session_scope() as session:
        assert session.get(DiagnosticSession, str(capture.id)).participants == {}


def test_browser_join_attempt_budget_is_shared_between_apis(replicas):
    first, second = replicas
    first.sessions.start("interface", 5, actor())
    second.sessions.tick()
    for _ in range(5):
        with pytest.raises(DiagnosticControlError, match="diagnostic_browser_not_joined"):
            first.browser.join("invalid-invitation", actor())
    with pytest.raises(DiagnosticControlError, match="diagnostic_rate_limited"):
        second.browser.join("invalid-invitation", actor())


def test_future_runtime_refuses_an_unadopted_legacy_control_journal(tmp_path):
    from app.services.diagnostics.control import DeferredControlJournal
    from app.services.diagnostics.store import DiagnosticStore
    settings = diagnostic_settings(tmp_path)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    store = DiagnosticStore(settings.diagnostics_dir, settings.diagnostics_limits())
    store.open()
    journal = DeferredControlJournal(store)
    journal.append({"event_id": str(uuid4()), "target_id": str(uuid4()),
        "action": "diagnostic_session_stopped", "reason": "manual",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(), "bytes_written": 0,
        "user_id": "admin", "username": "admin"})
    original = journal.path.read_bytes()
    store.close()
    runtime = initialize_diagnostics(settings)
    try:
        assert runtime.available is False
        assert journal.path.read_bytes() == original
    finally:
        runtime.stop()


def test_future_runtimes_can_record_with_the_same_configured_directory(tmp_path):
    settings = diagnostic_settings(tmp_path)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    first, second = initialize_diagnostics(settings), initialize_diagnostics(settings)
    try:
        assert first.available and second.available
        assert first.store.root != second.store.root
        assert first.store.root.parent == second.store.root.parent == settings.diagnostics_dir / "instances"
        assert first.store.durable_segments and second.store.durable_segments
    finally:
        first.stop()
        second.stop()


def test_future_shutdown_does_not_refresh_sql_control(replicas, monkeypatch):
    first, _ = replicas
    first.start()
    original = first.sessions.status
    monkeypatch.setattr(first.sessions, "status", lambda: (_ for _ in ()).throw(AssertionError("shutdown must use local state")))
    try:
        first.stop()
    finally:
        monkeypatch.setattr(first.sessions, "status", original)
