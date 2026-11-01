"""Both replica entry points participate in audit leadership after preflight."""
from types import SimpleNamespace
from threading import Event

import anyio
import pytest

from app.config import Settings


@pytest.mark.parametrize("role", ["api", "worker"])
def test_replica_entrypoint_starts_and_stops_audit_after_preflight(tmp_path, monkeypatch, role):
    # Import before patching the source of worker's module-bound runtime import.
    import app.worker as command
    from app.services import audit_outbox, audit_runtime
    from app.services.work import runtime
    import app.services.diagnostics.runtime as diagnostics
    calls = []
    settings = Settings(_env_file=None, data_dir=tmp_path, audit_stdout_mode="durable")
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", role
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr(audit_runtime, "validate_existing_audit", lambda: None)
    monkeypatch.setattr(audit_runtime, "validate_audit_transport_profile", lambda *_a: None)
    monkeypatch.setattr(audit_runtime, "preflight_audit", lambda *_a: calls.append("preflight"))
    handle = SimpleNamespace(stop=lambda **_k: True, health=None)
    monkeypatch.setattr(runtime, "start_runtime", lambda *_a, **_k: handle)
    diagnostic = SimpleNamespace(available=False, start=lambda: True, stop=lambda: None)
    monkeypatch.setattr(diagnostics, "initialize_diagnostics", lambda: diagnostic)
    monkeypatch.setattr(diagnostics, "install_entrypoint_runtime", lambda *_a: diagnostic)

    class Sender:
        def __init__(self, configured):
            assert configured is settings

        def start(self):
            calls.append("start")

        def stop(self, **_kwargs):
            calls.append("stop")
            return True

    monkeypatch.setattr(audit_outbox, "AuditDispatcher", Sender)
    if role == "api":
        import app.main as application
        monkeypatch.setattr(application, "get_settings", lambda: settings)
        monkeypatch.setattr(application, "_validate_runtime_dependencies", lambda: None)
        monkeypatch.setattr("app.services.artifacts.control.enforce_runtime", lambda *_a: None)
        application_state = SimpleNamespace(state=SimpleNamespace())

        async def lifecycle():
            async with application._api_lifespan(application_state):
                assert isinstance(application_state.state.audit_dispatcher, Sender)

        anyio.run(lifecycle)
    else:
        monkeypatch.setattr(command, "get_settings", lambda: settings)
        monkeypatch.setattr(command, "start_runtime", lambda *_a, **_k: handle)
        monkeypatch.setattr(command, "WorkerHealthServer", lambda *_a, **_k: SimpleNamespace(start=lambda: None, stop=lambda **_k: True))
        stop = Event()
        stop.set()
        assert command.main(["--health-port", "0"], settings=settings, stop_event=stop) == 0
    assert calls == ["preflight", "start", "stop"]


@pytest.mark.parametrize("unavailable", [False, True])
def test_audit_preflight_checks_leadership_connection_but_accepts_standby(tmp_path, monkeypatch, unavailable):
    from app.services import audit_leadership, audit_runtime
    settings = Settings(_env_file=None, data_dir=tmp_path, audit_stdout_mode="durable")
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    calls = []

    class Guard:
        def __init__(self, engine):
            pass

        def acquire(self):
            calls.append("probe")
            if unavailable:
                raise ValueError("Audit leadership preflight unavailable")
            return False  # Another healthy sender holds the lock.

        def close(self):
            calls.append("close")

    monkeypatch.setattr(audit_leadership, "AuditLeadership", Guard)
    if unavailable:
        with pytest.raises(ValueError, match="leadership preflight"):
            audit_runtime.preflight_audit(settings)
    else:
        audit_runtime.preflight_audit(settings)
    assert calls == ["probe", "close"]
