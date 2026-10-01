"""Read views pin existing segments without allocating a copied spool."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.services.diagnostics.schema import DiagnosticLimits, EventFilter
from app.services.diagnostics.sanitize import encode_event
from app.services.diagnostics.store import DiagnosticStore


def event(stamp):
    return encode_event({"schema_version": 1, "event_id": str(uuid4()), "boot_id": str(uuid4()),
                         "timestamp_utc": stamp.isoformat(), "component": "backend",
                         "origin": "server", "level": "ERROR", "event_code": "operation_failed",
                         "error_code": "internal_error"})


def test_read_view_freezes_prefix_and_never_creates_snapshot(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        assert store.append(event(now), stream="baseline")
        before = list((store.root / "snapshots").iterdir())
        view = store.pin_read_view(EventFilter(), now + timedelta(seconds=1))
        try:
            assert list((store.root / "snapshots").iterdir()) == before
            assert store.append(event(now), stream="baseline")
            page = store.read_event_page(view, offset=0, limit=50)
            assert len(page.events) == 1
            assert page.next_offset is None
        finally:
            view.release()
            view.release()


def test_read_view_reports_same_length_prefix_mutation(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        assert store.append(event(now), stream="baseline")
        view = store.pin_read_view(EventFilter(), now + timedelta(seconds=1))
        source = view.descriptors[0].path
        original = source.read_bytes()
        changed = original.replace(b"internal_error", b"network_error ")
        assert len(changed) == len(original) and changed != original
        source.write_bytes(changed)
        changed_ns = view.descriptors[0].mtime_ns + 1_000_000_000
        os.utime(source, ns=(changed_ns, changed_ns))
        try:
            page = store.read_event_page(view, offset=0, limit=50)
            assert page.events == ()
            assert page.partial
            assert "segment_unavailable" in page.gaps
        finally:
            view.release()


def test_pinned_expired_segment_survives_sweep_until_release(tmp_path):
    now = datetime.now(timezone.utc)
    limits = DiagnosticLimits(min_free_bytes=0, baseline_seconds=1)
    with DiagnosticStore(tmp_path / "spool", limits) as store:
        assert store.append(event(now), stream="baseline")
        view = store.pin_read_view(EventFilter(), now)
        path = next((store.root / "events" / "baseline").glob("*.jsonl"))
        try:
            store.sweep(now + timedelta(seconds=2))
            assert path.exists()
        finally:
            view.release()
        store.sweep(now + timedelta(seconds=2))
        assert not path.exists()


def test_pinned_expired_capture_does_not_abort_other_cleanup(tmp_path):
    now = datetime.now(timezone.utc)
    first, second = str(uuid4()), str(uuid4())
    with DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        assert store.append(event(now), stream=first)
        assert store.append(event(now), stream=second)
        view = store.pin_read_view(EventFilter(session_id=first), now)
        first_path = next((store.root / "events" / first).glob("*.jsonl"))
        second_path = next((store.root / "events" / second).glob("*.jsonl"))
        store.retire_capture(first, now - timedelta(days=2))
        store.retire_capture(second, now - timedelta(days=2))
        try:
            store.sweep(now)
            assert first_path.exists()
            assert not second_path.exists()
        finally:
            view.release()
        store.sweep(now)
        assert not first_path.exists()


def test_frontend_segment_is_read_without_copy(tmp_path):
    import json
    from app.services.diagnostics.read_view import attach_frontend
    now = datetime.now(timezone.utc)
    front = tmp_path / "frontend"
    directory = front / "events" / "baseline"
    directory.mkdir(parents=True)
    payload = {"schema_version": 1, "event_id": str(uuid4()), "boot_id": str(uuid4()),
               "timestamp_utc": now.isoformat(), "component": "frontend",
               "origin": "server", "level": "ERROR", "event_code": "proxy_failed",
               "error_code": "network_error"}
    source = directory / f"{int(now.timestamp() * 1000)}_{uuid4()}.jsonl"
    source.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        view = store.pin_read_view(EventFilter(), now + timedelta(seconds=1))
        try:
            attach_frontend(view, front)
            assert len(store.read_event_page(view, offset=0, limit=50).events) == 1
            assert list((store.root / "snapshots").iterdir()) == []
        finally:
            view.release()


def test_slow_segment_read_does_not_block_append(tmp_path, monkeypatch):
    import os
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        assert store.append(event(now), stream="baseline")
        view = store.pin_read_view(EventFilter(), now + timedelta(seconds=1))
        blocked = view.descriptors[0].path
        entered, release = Event(), Event()
        original = os.open
        def slow_open(path, flags, *args, **kwargs):
            result = original(path, flags, *args, **kwargs)
            if path == blocked and not flags & (os.O_WRONLY | os.O_RDWR):
                entered.set()
                assert release.wait(5)
            return result
        monkeypatch.setattr(os, "open", slow_open)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                reading = pool.submit(store.read_event_page, view, offset=0, limit=50)
                assert entered.wait(5)
                try:
                    assert pool.submit(store.append, event(now), stream="baseline").result(timeout=0.5)
                finally:
                    release.set()
                assert len(reading.result(timeout=5).events) == 1
        finally:
            release.set()
            view.release()
