"""Batch writes preserve complete JSONL lines and exact byte accounting."""
import errno
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

from app.services.diagnostics.sanitize import encode_event
from app.services.diagnostics.schema import DiagnosticLimits
from app.services.diagnostics.store import DiagnosticStore


def encoded_event():
    return encode_event({
        "schema_version": 2, "event_id": str(uuid4()), "boot_id": str(uuid4()),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "component": "backend", "level": "INFO", "event_code": "request_finished",
        "origin": "server", "route_template": "/api/search", "http_status": 200,
    })


def test_batch_rotates_at_whole_line_boundary(tmp_path):
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0, segment_bytes=1024))
    store.open()
    lines = [encoded_event() for _ in range(12)]
    events = tuple(store.validate_event(line) for line in lines)
    result = store.append_batch(events, stream="baseline")
    assert (result.written_events, result.written_bytes, result.failure_reason) == (12, sum(map(len, lines)), None)
    paths = list((store.root / "events/baseline").glob("*.jsonl"))
    assert len(paths) > 1
    assert all(path.stat().st_size <= 1024 for path in paths)
    recovered = [json.loads(line) for path in paths for line in path.read_bytes().splitlines()]
    assert len(recovered) == 12
    assert store.used_bytes >= sum(map(len, lines))
    store.close()


def test_index_avoids_full_inventory_on_append_and_status(tmp_path, monkeypatch):
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    store.open()
    monkeypatch.setattr(store, "_files", lambda *_: (_ for _ in ()).throw(AssertionError("full scan")))
    assert store.append(encoded_event(), stream="baseline")
    assert store.status()["used_bytes"] > 0
    store.close()


def test_reconcile_detects_external_growth_and_stays_degraded(tmp_path):
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    store.open()
    path = store.root / "control" / "state.json"
    store.write_bytes(path, b"abc")
    path.write_bytes(b"abcdef")  # Simulate an out-of-band mutation.
    report = store.reconcile()
    assert report.changed_files >= 1
    assert store.status()["storage_degraded"]
    assert store.used_bytes >= 6
    store.close()


def test_owned_control_rename_stays_exact_without_reconcile(tmp_path):
    from app.services.diagnostics.control import write_projection
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    store.open()
    write_projection(store, boot_id=str(uuid4()), active=None, now=datetime.now(timezone.utc))
    assert store.reconcile().changed_files == 0
    store.close()


def test_partial_write_is_rolled_back_before_reporting_enospc(tmp_path, monkeypatch):
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    store.open()
    event = store.validate_event(encoded_event())
    real_write = os.write
    attempts = 0

    def fail_after_short_write(fd, data):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return real_write(fd, data[:5])
        raise OSError(errno.ENOSPC, "synthetic disk full")

    monkeypatch.setattr(os, "write", fail_after_short_write)
    result = store.append_batch((event,), stream="baseline")
    assert result.written_events == 0
    assert result.failure_reason == "storage_low"
    assert all(path.read_bytes() == b"" for path in (store.root / "events/baseline").glob("*.jsonl"))
    store.close()


def test_recorder_writes_many_events_in_one_store_batch(tmp_path, monkeypatch):
    from app.services.diagnostics.recorder import DiagnosticRecorder
    from app.services.diagnostics.schema import DiagnosticContext
    session_id = str(uuid4())
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    real_append_batch = store.append_batch
    calls = []
    def observe(events, *, stream):
        calls.append(len(events))
        return real_append_batch(events, stream=stream)
    monkeypatch.setattr(store, "append_batch", observe)
    recorder = DiagnosticRecorder(store, capture_selector=lambda *_: session_id)
    context = DiagnosticContext(operation_id=str(uuid4()), operation_kind="document")
    for _ in range(100):
        assert recorder.emit("stage_finished", context=context, fields={"stage": "parse", "duration_ms": 2})
    recorder.start()
    assert recorder.flush_pending(timeout_seconds=10)
    recorder.stop()
    events = [json.loads(line) for path in (store.root / "events" / session_id).glob("*.jsonl")
              for line in path.read_bytes().splitlines()]
    assert len(events) == 100
    assert sum(calls) == 100
    assert len(calls) <= 2


def test_child_file_growth_transfers_reservation_into_used_bytes(tmp_path):
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    store.open()
    path = store.root / "bundles" / (str(uuid4()) + ".part")
    credit = store.reserve(100)
    before = store.used_bytes + store.reserved_bytes
    path.write_bytes(b"x" * 80)  # A supervised child wrote under the parent's credit.
    store.commit_external_growth(credit, path)
    assert store.used_bytes + store.reserved_bytes == before
    assert credit.remaining == 20
    credit.release()
    assert store.reconcile().changed_files == 0
    store.close()
