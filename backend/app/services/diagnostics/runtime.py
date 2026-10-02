"""Optional diagnostic runtime. Failure here must never prevent app startup."""
from datetime import datetime, timezone
import logging
import os
import threading
import time
from uuid import uuid4

from app import config
from .browser import DiagnosticBrowserService, set_browser_service
from .bundle_queue import DiagnosticBundleQueue
from .control import write_projection
from .paths import canonical_root
from .raw_debug import sweep_raw_debug
from .recorder import DiagnosticRecorder, set_recorder
from .sessions import DiagnosticSessionService
from .store import DiagnosticStore

_entrypoint_runtime = None


class DiagnosticRuntime:
    def __init__(self, settings):
        self.settings = settings
        self.boot_id = str(uuid4())
        self.available = False
        self.failure_code = None
        self.store = None
        self.recorder = None
        self.sessions = None
        self.bundle_queue = None
        self.browser = None
        self._started = False
        self._bundle_recovered = False
        self._stop_event = threading.Event()
        self._maintenance_thread = None
        self._handler_installed = False
        configured_frontend = os.getenv("OKF_FRONTEND_DIAGNOSTICS_DIR")
        self.frontend_root = canonical_root(configured_frontend) if configured_frontend else None
        try:
            self.store = DiagnosticStore(settings.diagnostics_dir, settings.diagnostics_limits())
            self.sessions = DiagnosticSessionService(self.store, settings, boot_id=self.boot_id)
            self.recorder = DiagnosticRecorder(
                self.store, capture_selector=self.sessions.active_for,
                capture_view_selector=self.sessions.runtime_view_for, boot_id=self.boot_id,
                baseline_enabled=settings.diagnostics_baseline_enabled,
                on_capture_written=self.sessions.record_written,
                on_capture_failed=self.sessions.recording_failed,
            )
            self.bundle_queue = DiagnosticBundleQueue(
                self.store, frontend_root=self.frontend_root, recorder_status=self.recorder.status,
                recorder_barrier=self.recorder.flush_pending)
            self.browser = DiagnosticBrowserService(self.sessions, self.recorder)
            self.recorder.start()
            logging.getLogger().addHandler(self.recorder.handler)
            self._handler_installed = True
            set_recorder(self.recorder)
            self.available = True
        except Exception:
            self.failure_code = "diagnostic_unavailable"
            if self.store:
                self.store.close()

    def start(self):
        if not self.available:
            return False
        if self._started:
            return True
        try:
            write_projection(self.store, boot_id=self.boot_id, active=None,
                             now=datetime.now(timezone.utc))
        except Exception:
            self.failure_code = "diagnostic_storage_low"
        try:
            self.sessions.recover(self.boot_id)
        except Exception:
            self.failure_code = "diagnostic_audit_unavailable"
        try:
            self.bundle_queue.recover()
            self._bundle_recovered = True
        except Exception:
            self.failure_code = "diagnostic_audit_unavailable"
        if self._bundle_recovered and self.settings.diagnostics_bundle_enabled:
            self.bundle_queue.start()
        self.maintain_once()
        set_browser_service(self.browser)
        self._stop_event.clear()
        self._maintenance_thread = threading.Thread(target=self._maintenance_loop,
                                                    name="diagnostic-maintenance", daemon=True)
        self._maintenance_thread.start()
        self._started = True
        self.recorder.emit("server_started")
        return True

    def maintain_once(self, now=None):
        if not self.available:
            return
        now = now or datetime.now(timezone.utc)
        for action in (
            lambda: self.sessions.tick(now),
            self.sessions.reconcile_control_events,
            lambda: self.store.sweep(now),
            lambda: self.bundle_queue.sweep(now),
            lambda: self.sessions.prune_metadata(now),
            lambda: self.bundle_queue.prune_metadata(now),
            lambda: sweep_raw_debug(self.settings, now=now),
        ):
            try:
                action()
            except Exception:
                self.failure_code = "diagnostic_maintenance_degraded"
        if not self._bundle_recovered:
            try:
                self.bundle_queue.recover()
                self._bundle_recovered = True
                if self.settings.diagnostics_bundle_enabled:
                    self.bundle_queue.start()
            except Exception:
                self.failure_code = "diagnostic_audit_unavailable"

    def heartbeat_once(self, now=None):
        """Refresh the 10-second frontend lease at a five-second cadence."""
        if not self.available:
            return
        try:
            self.sessions.tick(now or datetime.now(timezone.utc))
        except Exception:
            self.failure_code = "diagnostic_maintenance_degraded"

    def _maintenance_loop(self):
        last_sweep = time.monotonic()
        # Session starts are independent of this scheduler. Poll more often
        # than tick's five-second renewal period so a start just after a loop
        # wake cannot defer the first renewal until the ten-second lease ends.
        # tick still writes the projection only once per five seconds.
        while not self._stop_event.wait(1):
            self.heartbeat_once()
            if time.monotonic() - last_sweep >= self.store.limits.maintenance_seconds:
                self.maintain_once()
                last_sweep = time.monotonic()

    def stop(self):
        if not self.available:
            return
        if self._started:
            self._stop_event.set()
            if self._maintenance_thread:
                self._maintenance_thread.join(timeout=1)
            current = self.sessions.status()["session"]
            if current:
                try:
                    self.recorder.drain_capture(current["id"])
                    self.sessions.stop(current["id"], reason="server_restarted")
                except Exception:
                    pass
            self.bundle_queue.shutdown(timeout_seconds=5)
            self.recorder.emit("server_stopped")
            self._started = False
        if self._handler_installed:
            logging.getLogger().removeHandler(self.recorder.handler)
            self._handler_installed = False
        set_browser_service(None)
        set_recorder(None)
        self.recorder.stop(timeout_seconds=5)


def initialize_diagnostics(settings=None) -> DiagnosticRuntime:
    global _entrypoint_runtime
    if settings is None and _entrypoint_runtime is not None:
        return _entrypoint_runtime
    return DiagnosticRuntime(settings or config.get_settings())


def install_entrypoint_runtime(settings=None) -> DiagnosticRuntime:
    global _entrypoint_runtime
    if _entrypoint_runtime is None:
        _entrypoint_runtime = initialize_diagnostics(settings)
    return _entrypoint_runtime
