"""The HTTP API has no document/background execution ownership."""
import subprocess
import sys
import textwrap
import time
from threading import Event, Thread
import pytest

from app.config import Settings


@pytest.mark.parametrize("service", ["", "scope", "pipeline", "bulk", "export"])
def test_api_import_starts_no_executor(tmp_path, service):
    script = textwrap.dedent(r'''
        import concurrent.futures, sys, threading
        from pathlib import Path
        from app.config import Settings
        import app.config
        root = Path(sys.argv[1])
        settings = Settings(_env_file=None, data_dir=root/'data', diagnostics_dir=root/'diagnostics',
            database_url='sqlite:///'+(root/'isolated.db').as_posix(),
            embedding_provider='fake', qdrant_url='http://127.0.0.1:1')
        settings.okf_deployment_schema, settings.app_role = 'replicas-v1', 'api'
        app.config.get_settings = lambda: settings
        original_pool = concurrent.futures.ThreadPoolExecutor.__init__
        original_start = threading.Thread.start
        def application_caller():
            return '/backend/app/' in Path(sys._getframe(2).f_code.co_filename).as_posix()
        def pool(self, *args, **kwargs):
            if application_caller():
                raise RuntimeError('API import constructed an application executor')
            return original_pool(self, *args, **kwargs)
        def start(self, *args, **kwargs):
            if application_caller():
                raise RuntimeError('API import started an application background thread')
            return original_start(self, *args, **kwargs)
        concurrent.futures.ThreadPoolExecutor.__init__ = pool
        threading.Thread.start = start
        import app.main
        assert app.main.app is not None
        if sys.argv[2] == 'scope':
            # This helper is imported by the selective-processing endpoint.
            import app.services.processing_scope_jobs
        elif sys.argv[2] == 'pipeline':
            from app.services.pipeline import Pipeline
            Pipeline()
        elif sys.argv[2] == 'bulk':
            from app.services.job_queue import JobQueue
            JobQueue()
        elif sys.argv[2] == 'export':
            from app.services.export_queue import ExportQueue
            ExportQueue()
    ''')
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), service], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr


def test_worker_import_does_not_import_http_application():
    result = subprocess.run([sys.executable, "-c", "import app.worker, sys; assert 'app.main' not in sys.modules"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_worker_command_requires_worker_role():
    from app.worker import main
    with pytest.raises(ValueError, match="worker role"):
        main(settings=Settings(_env_file=None))


def test_api_runtime_starts_no_consumers(monkeypatch):
    from app.services.work import runtime
    settings = Settings(_env_file=None)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    def forbidden(*_a, **_k):
        raise AssertionError("API must not construct a background consumer")
    monkeypatch.setattr(runtime, "build_handlers", forbidden)
    handle = runtime.start_runtime(settings)
    assert handle.role == "api" and handle.stop(timeout=0.1)


def test_worker_health_expires_with_mainloop_and_requires_storage():
    from app.services.work.runtime import RuntimeHealth
    now = [0.0]
    health = RuntimeHealth(clock=lambda: now[0])
    health.pulse()
    health.set_storage_ready(True)
    assert health.live() and health.ready()
    now[0] = 91
    assert not health.live() and not health.ready()
    health.pulse()
    health.set_storage_ready(False)
    assert health.live() and not health.ready()


def configured_worker(monkeypatch, handler):
    from app.services.work import runtime
    from app.services.work import scratch
    from app.services.work.handlers import HandlerRegistry
    settings = Settings(_env_file=None, pipeline_max_workers=1, work_poll_seconds=0.1)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "worker"
    registry = HandlerRegistry()
    registry.register("test", 1, handler)
    monkeypatch.setattr(runtime, "build_handlers", lambda: registry)
    monkeypatch.setattr(runtime, "probe_storage", lambda _settings: True, raising=False)
    monkeypatch.setattr(scratch, "clean_export_attempts", lambda *_args, **_kwargs: None)
    return runtime, settings


def test_long_handler_does_not_stale_the_mainloop_heartbeat(monkeypatch):
    from app.db.session import session_scope
    from app.services.work.store import enqueue
    from tests.test_work_store import spec
    entered, release = Event(), Event()
    def handler(_work):
        entered.set()
        assert release.wait(10)
    runtime, settings = configured_worker(monkeypatch, handler)
    original_health, now = runtime.RuntimeHealth, [0.0]
    monkeypatch.setattr(runtime, "RuntimeHealth", lambda: original_health(clock=lambda: now[0]))
    with session_scope() as session:
        enqueue(session, spec())
    handle = runtime.start_runtime(settings)
    try:
        assert entered.wait(3)
        now[0] = 91.0
        deadline = time.monotonic() + 3
        while not handle.health.live() and time.monotonic() < deadline:
            Event().wait(0.02)
        assert handle.health.live() and handle.health.ready()
    finally:
        release.set()
        assert handle.stop(timeout=3)


def test_runtime_stop_reports_inflight_work_and_never_claims_the_next(monkeypatch):
    from app.db.models import WorkItem
    from app.db.session import session_scope
    from app.services.work.store import enqueue
    from tests.test_work_store import spec
    entered, release = Event(), Event()
    def handler(_work):
        entered.set()
        assert release.wait(10)
    runtime, settings = configured_worker(monkeypatch, handler)
    with session_scope() as session:
        first = enqueue(session, spec())
        second = enqueue(session, spec("next"))
    handle = runtime.start_runtime(settings)
    try:
        assert entered.wait(3)
        assert not handle.stop(timeout=0.05)
        assert not handle.health.ready()
        with session_scope() as session:
            assert session.get(WorkItem, first).status == "running"
    finally:
        release.set()
        assert handle.stop(timeout=3)
    with session_scope() as session:
        assert session.get(WorkItem, second).status == "queued"


def test_worker_scheduler_admits_a_durable_slot_and_stops_with_runtime(monkeypatch):
    from app.db.models import WorkItem
    from app.db.session import session_scope
    from app.services.work import scheduler
    entered = Event()
    runtime, settings = configured_worker(monkeypatch, lambda _work: entered.set())
    monkeypatch.setattr(scheduler, "periodic_specs", lambda _settings: [scheduler.PeriodicSpec("test", 60)], raising=False)
    handle = runtime.start_runtime(settings)
    try:
        assert entered.wait(3)
        with session_scope() as session:
            row = session.query(WorkItem).one()
            assert row.dedupe_key.startswith("schedule/test/global/")
    finally:
        assert handle.stop(timeout=3)


def test_workers_do_not_reset_foreign_processing(monkeypatch):
    from app.db.models import Document, WorkItem
    from app.db.session import session_scope
    from app.services.work.store import enqueue, claim
    from tests.test_work_store import spec
    def foreign_handler(_work):
        pytest.fail("An unexpired foreign claim must not be executed")
    runtime, settings = configured_worker(monkeypatch, foreign_handler)
    with session_scope() as session:
        session.add(Document(id="foreign", filename="foreign.pdf", status="processing"))
        work_id = enqueue(session, spec())
        work = claim(session, owner_id="another-process", supported={"test": {1}}, lease_seconds=120)
    handle = runtime.start_runtime(settings)
    try:
        deadline = time.monotonic() + 3
        while not handle.health.ready() and time.monotonic() < deadline:
            Event().wait(0.02)
        assert handle.health.ready()
        with session_scope() as session:
            row = session.get(WorkItem, work_id)
            assert (row.owner_id, row.fence, row.status) == (work.owner_id, work.fence, "running")
            assert session.get(Document, "foreign").status == "processing"
    finally:
        assert handle.stop(timeout=3)


def test_worker_http_health_has_separate_live_and_ready_endpoints():
    import httpx
    from app.services.work.runtime import RuntimeHealth
    from app.worker import WorkerHealthServer
    health = RuntimeHealth()
    health.pulse()
    server = WorkerHealthServer(health, host="127.0.0.1", port=0)
    server.start()
    try:
        with httpx.Client(trust_env=False) as client:
            root = f"http://127.0.0.1:{server.address[1]}"
            assert client.get(root + "/health/live").status_code == 200
            assert client.get(root + "/health/ready").status_code == 503
            health.set_storage_ready(True)
            assert client.get(root + "/health/ready").status_code == 200
            assert client.get(root + "/api/documents").status_code == 404
            health.close()
            assert client.get(root + "/health/live").status_code == 503
    finally:
        assert server.stop(timeout=1)


def test_worker_command_serves_health_until_stop(tmp_path, monkeypatch):
    import httpx
    import app.worker as command
    _, settings = configured_worker(monkeypatch, lambda _work: None)
    settings.data_dir, settings.diagnostics_dir = tmp_path / "data", tmp_path / "diagnostics"
    settings.audit_stdout_mode = "disabled"
    import app.services.diagnostics.runtime as diagnostics
    monkeypatch.setattr(diagnostics, "_entrypoint_runtime", None)
    stop, bound = Event(), Event()
    addresses, results, errors = [], [], []
    original_server = command.WorkerHealthServer
    class Server(original_server):
        def start(self):
            super().start()
            addresses.append(self.address)
            bound.set()
    monkeypatch.setattr(command, "WorkerHealthServer", Server)
    def run():
        try:
            results.append(command.main(["--health-host", "127.0.0.1", "--health-port", "0", "--shutdown-timeout", "2"],
                settings=settings, stop_event=stop))
        except Exception as exc:
            errors.append(type(exc).__name__)
    thread = Thread(target=run, daemon=True)
    thread.start()
    try:
        assert bound.wait(3), errors
        with httpx.Client(trust_env=False) as client:
            assert client.get(f"http://127.0.0.1:{addresses[0][1]}/health/live").status_code == 200
        assert thread.is_alive()
    finally:
        stop.set()
        thread.join(4)
    assert not thread.is_alive() and results == [0] and not errors
    assert diagnostics._entrypoint_runtime is not None
    assert not diagnostics._entrypoint_runtime._started


def test_api_lifespan_does_not_run_legacy_recovery_or_schedulers(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    import app.main as application
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", diagnostics_dir=tmp_path / "diagnostics",
        embedding_provider="fake", audit_stdout_mode="disabled")
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    monkeypatch.setattr(application, "get_settings", lambda: settings)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.diagnostics.runtime.initialize_diagnostics", lambda: SimpleNamespace(
        available=False, start=lambda: False, stop=lambda: None))
    calls = []
    def record(name):
        def called(*_a, **_k):
            calls.append(name)
        return called
    for path in ("app.services.registry.reset_stale_statuses", "app.services.processing_scope.recover_processing_reviews",
                 "app.services.processing_scope_jobs.recover_scope_changes", "app.services.job_queue.get_job_queue",
                 "app.services.export_queue.get_export_queue", "app.services.trash.start_purge_loop",
                 "app.services.chat_history.start_chat_purge_loop", "app.auth.service.purge_expired_sessions",
                 "app.services.stopwords.ensure_seeded", "app.services.glossary.seed.ensure_initial_rules"):
        monkeypatch.setattr(path, record(path))
    monkeypatch.setattr(application.VectorStore, "ensure_collection", record("qdrant-init"))
    original_start = __import__("threading").Thread.start
    def start(thread, *args, **kwargs):
        from pathlib import Path
        if Path(sys._getframe(1).f_code.co_filename).as_posix().endswith("/app/main.py"):
            raise AssertionError("API started a legacy scheduler")
        return original_start(thread, *args, **kwargs)
    monkeypatch.setattr("threading.Thread.start", start)
    with TestClient(application.create_app()) as client:
        assert client.get("/docs").status_code == 200
    assert not calls


def test_worker_role_cannot_construct_the_public_http_app(monkeypatch):
    import app.main as application
    settings = Settings(_env_file=None)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "worker"
    monkeypatch.setattr(application, "get_settings", lambda: settings)
    with pytest.raises(ValueError, match="worker role"):
        application.create_app()


@pytest.mark.parametrize("role", ["api", "worker"])
def test_distributed_diagnostics_start_preserves_another_instances_capture(tmp_path, role):
    from app.db.models import DiagnosticSession
    from app.db.session import session_scope
    from app.services.diagnostics.runtime import initialize_diagnostics
    from tests.test_diagnostics_lifecycle import settings as diagnostic_settings
    from tests.test_diagnostics_sessions import actor
    foreign = initialize_diagnostics(diagnostic_settings(tmp_path / "foreign"))
    settings = diagnostic_settings(tmp_path / "api")
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", role
    local = initialize_diagnostics(settings)
    try:
        capture = foreign.sessions.start("interface", 5, actor())
        assert local.start()
        with session_scope() as session:
            row = session.get(DiagnosticSession, str(capture.id))
            assert row.active_slot == 1 and row.boot_id == foreign.boot_id
        assert not local.bundle_queue._running
    finally:
        local.stop()
        foreign.stop()
