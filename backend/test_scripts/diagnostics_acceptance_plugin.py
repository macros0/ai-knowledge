"""Run existing outcome contracts under real capture policy on disposable data."""
from uuid import uuid4

import pytest

from app.auth.models import User
from app.config import Settings
from app.services.diagnostics.recorder import DiagnosticRecorder, set_recorder
from app.services.diagnostics.sessions import DiagnosticSessionService
from app.services.diagnostics.store import DiagnosticStore


def pytest_addoption(parser):
    parser.addoption("--diagnostics-mode", choices=("baseline", "standard", "detailed"),
                     required=True)


@pytest.fixture(autouse=True)
def actual_diagnostic_policy(request, tmp_path, _db):
    mode = request.config.getoption("--diagnostics-mode")
    settings = Settings(_env_file=None, data_dir=tmp_path / "diagnostic-data",
                        diagnostics_dir=tmp_path / "diagnostic-spool",
                        diagnostics_capture_enabled=True, diagnostics_min_free_mb=0)
    with DiagnosticStore(settings.diagnostics_dir, settings.diagnostics_limits()) as store:
        sessions = DiagnosticSessionService(store, settings)
        if mode != "baseline":
            sessions.start("system", 5, User(user_id="mode-admin", username="mode-admin", roles=["admin"]),
                           capture_level=mode)
        recorder = DiagnosticRecorder(store, boot_id=str(uuid4()),
                                      capture_selector=sessions.active_for,
                                      capture_view_selector=sessions.runtime_view_for,
                                      on_capture_written=sessions.record_written,
                                      on_capture_failed=sessions.recording_failed)
        recorder.start()
        set_recorder(recorder)
        try:
            yield
        finally:
            set_recorder(None)
            recorder.stop()
        status = recorder.status()
        assert not any(status.get(key, 0) for key in
                       ("dropped", "invalid", "expired_queue", "storage_errors", "drain_timeouts"))
