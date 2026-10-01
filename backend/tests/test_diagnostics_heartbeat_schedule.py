"""A capture starting between scheduler ticks retains frontend lease headroom."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from app.services.diagnostics.runtime import DiagnosticRuntime
from tests.test_diagnostics_sessions import actor, service as _service_fixture


service = _service_fixture


@pytest.mark.parametrize("start_offset", [0.01, 0.99, 4.99])
def test_scheduled_heartbeat_renews_before_frontend_poll_can_miss_lease(
    service, monkeypatch, start_offset
):
    import app.services.diagnostics.runtime as module

    clock = [0.0]
    epoch = datetime(2026, 9, 30, tzinfo=timezone.utc)
    service.monotonic = lambda: clock[0]
    service.utcnow = lambda: epoch + timedelta(seconds=clock[0])
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    projection = service.store.root / "control/capture.json"
    renewed_at = []
    previous_lease = [None]

    class Scheduler:
        started = False

        def wait(self, delay):
            finish = clock[0] + delay
            if not self.started and finish >= start_offset:
                clock[0] = start_offset
                service.start("interface", 5, actor())
                renewed_at.append(clock[0])
                previous_lease[0] = json.loads(projection.read_text())["lease_until"]
                self.started = True
            clock[0] = finish
            return clock[0] > 25

    runtime = object.__new__(DiagnosticRuntime)
    runtime.available = True
    runtime.failure_code = None
    runtime.sessions = service
    runtime.store = service.store
    runtime._stop_event = Scheduler()

    heartbeat_once = runtime.heartbeat_once
    monkeypatch.setattr(runtime, "heartbeat_once", lambda: heartbeat_once(service.utcnow()))
    # Observe actual tick writes, while keeping the real session renewal logic.
    original_tick = service.tick

    def tick(now):
        original_tick(now)
        if projection.exists():
            lease = json.loads(projection.read_text())["lease_until"]
            if lease != previous_lease[0]:
                renewed_at.append(clock[0])
                previous_lease[0] = lease

    monkeypatch.setattr(service, "tick", tick)
    runtime._maintenance_loop()
    gaps = [right - left for left, right in zip(renewed_at, renewed_at[1:])]
    assert len(gaps) >= 2
    # Ten-second lease minus a one-second frontend poll and three seconds margin.
    assert max(gaps) <= 6, renewed_at
