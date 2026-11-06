"""Shared capture control on independent API processes and isolated PostgreSQL."""
from multiprocessing import get_context
from pathlib import Path

from app.db.session import configure_database, get_engine
from app.services.diagnostics.runtime import initialize_diagnostics
from app.services.diagnostics.schema import DiagnosticContext
from tests.support.replica_postgres import disposable_postgres
from tests.test_diagnostics_lifecycle import settings as diagnostic_settings
from tests.test_diagnostics_sessions import actor


def _settings(root):
    settings = diagnostic_settings(root)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    return settings


def _other_api(url, root, pipe):
    import app.config
    settings = _settings(Path(root))
    app.config.get_settings = lambda: settings
    configure_database(url)
    runtime = initialize_diagnostics(settings)
    try:
        assert runtime.available
        pipe.send("ready")
        while pipe.poll(30):
            command = pipe.recv()
            if command == "exit":
                return
            if command[0] == "stop":
                result = runtime.sessions.stop(command[1], actor())
                pipe.send(result.status)
            else:
                runtime.sessions.tick()
                pipe.send(runtime.sessions.active_for(DiagnosticContext(), "render_failed"))
        raise RuntimeError("Diagnostic acceptance control timed out")
    finally:
        runtime.stop()
        get_engine().dispose()
        pipe.close()


def test_capture_control_shared_between_api_processes(tmp_path, monkeypatch):
    with disposable_postgres() as url:
        settings = _settings(tmp_path / "api-a")
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        runtime = initialize_diagnostics(settings)
        context = get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_other_api, args=(url, str(tmp_path / "api-b"), child))
        process.start()
        child.close()

        def command(value):
            parent.send(value)
            assert parent.poll(15)
            return parent.recv()

        try:
            assert runtime.available
            capture = runtime.sessions.start("interface", 5, actor())
            assert parent.poll(25) and parent.recv() == "ready"
            assert command(("probe",)) == str(capture.id)
            assert command(("stop", str(capture.id))) == "stopped"
            runtime.sessions.tick()
            assert runtime.sessions.active_for(DiagnosticContext(), "render_failed") is None
            assert command(("probe",)) is None
        finally:
            runtime.stop()
            if process.is_alive():
                parent.send("exit")
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join(5)
            parent.close()
        assert process.exitcode == 0
