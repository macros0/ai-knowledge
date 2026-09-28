"""Diagnostics degrade independently of application startup and stop promptly."""
from datetime import datetime, timezone
import json

from app.config import Settings
from app.auth.models import User
from app.services.diagnostics.runtime import initialize_diagnostics


def settings(tmp_path, **changes):
    values = {"_env_file": None, "data_dir": tmp_path / "data",
              "diagnostics_dir": tmp_path / "diagnostics", "diagnostics_min_free_mb": 0,
              "diagnostics_capture_enabled": True, "diagnostics_bundle_enabled": True,
              "diagnostics_download_enabled": True}
    values.update(changes)
    return Settings(**values)


def test_unavailable_diagnostics_directory_does_not_crash_runtime(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    runtime = initialize_diagnostics(settings(tmp_path, diagnostics_dir=blocked))
    assert runtime.available is False
    assert runtime.start() is False
    runtime.stop()


def test_idempotent_start_disabled_projection_and_bounded_shutdown(tmp_path):
    runtime = initialize_diagnostics(settings(tmp_path))
    assert runtime.available
    assert runtime.start()
    first_thread = runtime.recorder._thread
    assert runtime.start()
    assert runtime.recorder._thread is first_thread
    projection = json.loads((runtime.store.root / "control" / "capture.json").read_text())
    assert projection["active"] is False
    assert runtime.bundle_queue._running
    runtime.stop()
    runtime.stop()
    assert not runtime.bundle_queue._running


def test_maintenance_runs_raw_debug_cleanup_even_with_capture_off(tmp_path, monkeypatch):
    import app.services.diagnostics.runtime as module
    called = []
    monkeypatch.setattr(module, "sweep_raw_debug", lambda settings, now=None: called.append(now) or 0)
    runtime = initialize_diagnostics(settings(tmp_path, diagnostics_capture_enabled=False,
                                              diagnostics_bundle_enabled=False))
    try:
        runtime.start()
        runtime.maintain_once(datetime.now(timezone.utc))
        assert called
        assert runtime.sessions.status()["session"] is None
    finally:
        runtime.stop()


def test_maintenance_prunes_session_and_bundle_metadata(tmp_path, monkeypatch):
    runtime = initialize_diagnostics(settings(tmp_path))
    called = []
    monkeypatch.setattr(runtime.sessions, "prune_metadata", lambda now: called.append(("session", now)))
    monkeypatch.setattr(runtime.bundle_queue, "prune_metadata", lambda now: called.append(("bundle", now)))
    now = datetime.now(timezone.utc)
    try:
        runtime._bundle_recovered = True
        runtime.maintain_once(now)
        assert called == [("session", now), ("bundle", now)]
    finally:
        runtime.stop()


def test_start_runs_maintenance_without_waiting_for_periodic_timer(tmp_path, monkeypatch):
    runtime = initialize_diagnostics(settings(tmp_path))
    called = []
    monkeypatch.setattr(runtime.store, "sweep", lambda now: called.append(now))
    try:
        assert runtime.start()
        assert len(called) == 1
    finally:
        runtime.stop()


def test_early_import_failure_keeps_only_safe_event(tmp_path):
    from app import diagnostic_entrypoint
    def fail(**_):
        raise RuntimeError("CANARY_PRIVATE password")
    result = diagnostic_entrypoint.main(["--host", "127.0.0.1", "--port", "18000"],
                                         settings=settings(tmp_path), runner=fail)
    assert result == 1
    content = b"".join(path.read_bytes() for path in (tmp_path / "diagnostics/events").rglob("*.jsonl"))
    assert b"server_start_failed" in content
    assert b"CANARY_PRIVATE" not in content


def test_frontend_control_lease_is_refreshed_within_ten_seconds(tmp_path):
    runtime = initialize_diagnostics(settings(tmp_path))
    runtime.start()
    try:
        clock = [100.0]
        runtime.sessions.monotonic = lambda: clock[0]
        admin = User(user_id="admin-1", username="Administrator", roles=["admin"])
        runtime.sessions.start("interface", 5, admin)
        initial = json.loads((runtime.store.root / "control" / "capture.json").read_text())
        clock[0] = 106.0
        runtime.heartbeat_once(datetime.fromtimestamp(initial["lease_until"] - 4, timezone.utc))
        refreshed = json.loads((runtime.store.root / "control" / "capture.json").read_text())
        assert refreshed["active"] is True
        assert refreshed["lease_until"] > initial["lease_until"]
    finally:
        runtime.stop()


def test_multiworker_start_is_rejected_before_uvicorn(tmp_path):
    from app import diagnostic_entrypoint
    called = []
    result = diagnostic_entrypoint.main(["--workers", "2"], settings=settings(tmp_path),
                                         runner=lambda *_args, **_kwargs: called.append(True))
    assert result == 1
    assert called == []
    content = b"".join(path.read_bytes() for path in (tmp_path / "diagnostics/events").rglob("*.jsonl"))
    assert b"server_start_failed" in content
