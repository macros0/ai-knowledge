"""Slow control I/O must not hold the lock used by hot producer selection."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

from app.auth.models import User
from app.config import Settings
from app.services.diagnostics.schema import DiagnosticContext, DiagnosticLimits
from app.services.diagnostics.sessions import DiagnosticSessionService
from app.services.diagnostics.store import DiagnosticStore


def service(tmp_path):
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    store.open()
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", diagnostics_dir=tmp_path / "spool",
                        diagnostics_capture_enabled=True)
    return DiagnosticSessionService(store, settings)


def actor():
    return User(user_id="admin-1", username="admin", roles=["admin"])


def test_start_disk_admission_does_not_block_producer_selector(tmp_path, monkeypatch):
    sessions = service(tmp_path)
    entered, release = Event(), Event()
    original = sessions.store.reserve
    def slow_reserve(size):
        entered.set()
        assert release.wait(5)
        return original(size)
    monkeypatch.setattr(sessions.store, "reserve", slow_reserve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        started = pool.submit(sessions.start, "system", 15, actor())
        assert entered.wait(5)
        try:
            assert pool.submit(sessions.active_for, DiagnosticContext(), "stage_started").result(timeout=0.5) is None
        finally:
            release.set()
        assert started.result(timeout=10).id
    sessions.store.close()


def test_stop_projection_does_not_block_new_producer_selection(tmp_path, monkeypatch):
    import app.services.diagnostics.sessions as module
    sessions = service(tmp_path)
    started = sessions.start("system", 15, actor())
    entered, release = Event(), Event()
    original = module.write_projection
    def slow_projection(*args, **kwargs):
        if kwargs.get("active") is None:
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "write_projection", slow_projection)
    with ThreadPoolExecutor(max_workers=2) as pool:
        stopped = pool.submit(sessions.stop, started.id, actor())
        assert entered.wait(5)
        try:
            assert pool.submit(sessions.active_for, DiagnosticContext(), "stage_started").result(timeout=0.5) is None
        finally:
            release.set()
        assert stopped.result(timeout=10).status == "stopped"
    sessions.store.close()


def test_stale_active_projection_cannot_restore_capture(tmp_path):
    import json
    from datetime import datetime, timedelta, timezone
    from app.services.diagnostics.control import write_projection
    sessions = service(tmp_path)
    now = datetime.now(timezone.utc)
    active = {"id": str(uuid4()), "scope": "interface", "expires_at": now + timedelta(minutes=5)}
    assert write_projection(sessions.store, boot_id=sessions.boot_id, active=None, now=now, revision=3) is True
    assert write_projection(sessions.store, boot_id=sessions.boot_id, active=active, now=now, revision=2) is False
    saved = json.loads((sessions.store.root / "control/capture.json").read_text(encoding="utf-8"))
    assert saved["active"] is False
    assert saved["revision"] == 3
    sessions.store.close()


def test_active_projection_contains_only_safe_v2_policy(tmp_path):
    import json
    sessions = service(tmp_path)
    started = sessions.start("interface", 15, actor(), capture_level="standard")
    value = json.loads((sessions.store.root / "control/capture.json").read_text(encoding="utf-8"))
    assert value["schema_version"] == 2
    assert value["active"] is True
    assert value["session_id"] == started.id
    assert value["policy"]["level"] == "standard"
    assert "created_by" not in str(value)
    sessions.store.close()
