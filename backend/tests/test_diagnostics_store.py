"""Storage behavior, not implementation shape: bytes, lifetime and safe boundaries."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.services.diagnostics.schema import DiagnosticLimits, EventFilter
from app.services.diagnostics.sanitize import encode_event
from app.services.diagnostics.store import DiagnosticStore, StorageQuotaError, WriterActiveError


def limits(**changes):
    return DiagnosticLimits(
        **{ "total_bytes": 40960, "backend_bytes": 32768, "frontend_bytes": 8192,
            "baseline_bytes": 8192, "session_bytes": 8192, "segment_bytes": 1024,
            "bundle_bytes": 16384, "min_free_bytes": 0, "control_bytes": 512, **changes})


def encoded(stamp=None):
    return encode_event({
        "schema_version": 1, "event_id": str(uuid4()), "boot_id": str(uuid4()),
        "timestamp_utc": (stamp or datetime.now(timezone.utc)).isoformat(),
        "component": "backend", "level": "ERROR", "event_code": "operation_failed",
        "origin": "server", "error_code": "internal_error",
    })


def test_rotate_before_segment_limit(tmp_path):
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        for _ in range(100):
            assert store.append(encoded(), stream="baseline")
        paths = list((tmp_path / "spool/events/baseline").glob("*.jsonl"))
        assert len(paths) > 1
        assert all(path.stat().st_size <= 1024 for path in paths)
        assert sum(path.stat().st_size for path in paths) <= 8192
        assert store.used_bytes + store.reserved_bytes <= 32768


def test_reservations_count_together(tmp_path):
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        first = store.reserve(20000)
        with pytest.raises(StorageQuotaError):
            store.reserve(20000)
        first.release()
        first.release()
        assert store.reserved_bytes == 0
        second = store.reserve(20000)
        second.release()


def test_snapshot_copies_count_against_quota(tmp_path):
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        for _ in range(3):
            assert store.append(encoded(), stream="baseline")
        before = store.used_bytes
        snapshot = store.snapshot(EventFilter(), datetime.now(timezone.utc))
        assert store.used_bytes > before
        assert sum(len(p.read_bytes().splitlines()) for p in snapshot.paths) == 3
        snapshot.release()
        snapshot.release()
        assert store.used_bytes == before


def test_second_writer_refused_and_lock_released(tmp_path):
    with DiagnosticStore(tmp_path / "spool", limits()):
        with pytest.raises(WriterActiveError):
            with DiagnosticStore(tmp_path / "spool", limits()):
                pass
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        assert store.append(encoded(), stream="baseline")


def test_partial_last_line_ignored(tmp_path):
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        store.append(encoded(), stream="baseline")
        path = next((tmp_path / "spool/events/baseline").glob("*.jsonl"))
        with path.open("ab") as handle:
            handle.write(b'{"unclosed":')
        snapshot = store.snapshot(EventFilter(), datetime.now(timezone.utc))
        assert snapshot.counts["truncated"] == 1
        assert sum(len(p.read_bytes().splitlines()) for p in snapshot.paths) == 1
        snapshot.release()


def test_snapshot_skips_unreadable_segment_and_preserves_other_events(tmp_path, monkeypatch):
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        assert store.append(encoded(), stream="baseline")
        assert store.append(encoded(), stream=str(uuid4()))
        blocked = next((store.root / "events" / "baseline").glob("*.jsonl"))
        original_open = Path.open

        def deny_one_segment(path, mode="r", *args, **kwargs):
            if path == blocked and mode == "rb":
                raise PermissionError("segment belongs to another owner")
            return original_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", deny_one_segment)
        snapshot = store.snapshot(EventFilter(), datetime.now(timezone.utc))
        try:
            assert snapshot.counts["invalid"] == 1
            assert snapshot.counts["events"] == 1
            assert sum(len(path.read_bytes().splitlines()) for path in snapshot.paths) == 1
        finally:
            snapshot.release()


def test_retention_excludes_expired_events_before_sweep(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        store.append(encoded(now - timedelta(days=8)), stream="baseline")
        store.append(encoded(now), stream="baseline")
        snapshot = store.snapshot(EventFilter(), now)
        assert sum(len(p.read_bytes().splitlines()) for p in snapshot.paths) == 1
        assert snapshot.counts["expired"] == 1
        snapshot.release()


def test_symlink_junction_and_path_escape_rejected(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    sentinel = outside / "private.txt"
    sentinel.write_text("PRIVATE")
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        with pytest.raises(ValueError):
            store.write_bytes(outside / "new.txt", b"new")
        with pytest.raises(ValueError):
            store.append(encoded(), stream="../../outside")
        if hasattr(Path, "is_junction"):
            import os
            if os.name == "nt":
                import subprocess
                subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"New-Item -ItemType Junction -Path '{tmp_path / 'spool/events/evil'}' -Target '{outside}' | Out-Null"], check=True)
            else:
                (tmp_path / "spool/events/evil").symlink_to(outside, target_is_directory=True)
            with pytest.raises(ValueError):
                store.write_bytes(tmp_path / "spool/events/evil/new.txt", b"new")
        store.sweep(datetime.now(timezone.utc))
        assert sentinel.read_text() == "PRIVATE"


def test_disk_full_and_permission_error_do_not_escape(tmp_path, monkeypatch):
    import shutil
    with DiagnosticStore(tmp_path / "spool", limits(min_free_bytes=1)) as store:
        monkeypatch.setattr(shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(100, 100, 0))
        assert not store.append(encoded(), stream="baseline")
        assert store.status()["storage_degraded"]


def test_corrupt_and_old_schema_lines_rejected(tmp_path):
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        store.append(encoded(), stream="baseline")
        path = next((tmp_path / "spool/events/baseline").glob("*.jsonl"))
        old = json.loads(encoded())
        old["schema_version"] = 2
        with path.open("ab") as handle:
            handle.write(b"garbage\n" + json.dumps(old).encode() + b"\n")
        snapshot = store.snapshot(EventFilter(), datetime.now(timezone.utc))
        assert snapshot.counts["invalid"] == 2
        snapshot.release()


def test_capture_size_limit_preserves_first_events(tmp_path):
    stream = str(uuid4())
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        first = encoded()
        assert store.append(first, stream=stream)
        for _ in range(100):
            if not store.append(encoded(), stream=stream):
                break
        else:
            pytest.fail("Capture must stop at its size limit, not rotate indefinitely")
        paths = sorted((store.root / "events" / stream).glob("*.jsonl"))
        assert paths[0].read_bytes().startswith(first)
        assert store.status()["last_failure"] == "size_limit"


def test_append_does_not_rescan_entire_spool_for_every_event(tmp_path, monkeypatch):
    scans = 0
    original = DiagnosticStore.used_bytes.fget

    def counted(store):
        nonlocal scans
        scans += 1
        return original(store)

    monkeypatch.setattr(DiagnosticStore, "used_bytes", property(counted))
    with DiagnosticStore(tmp_path / "spool", limits(total_bytes=(1 << 20) + 8192,
                                                     backend_bytes=1 << 20,
                                                     baseline_bytes=1 << 19,
                                                     session_bytes=1 << 16,
                                                     segment_bytes=1 << 16)) as store:
        for _ in range(100):
            assert store.append(encoded(), stream="baseline")
    assert scans < 10, "The writer must not walk the whole spool for each event"


def test_append_space_credit_never_exceeds_total_quota(tmp_path):
    cap = 1024
    with DiagnosticStore(tmp_path / "spool", limits(total_bytes=cap + 8192,
                                                    backend_bytes=cap,
                                                    baseline_bytes=cap, session_bytes=cap,
                                                    bundle_bytes=cap, segment_bytes=512)) as store:
        stream = str(uuid4())
        accepted = 0
        for index in range(20):
            if not store.append(encoded(), stream="baseline" if index % 2 else stream):
                break
            accepted += 1
        assert accepted > 0
        assert accepted < 20
        assert store.used_bytes <= cap


def test_snapshot_copy_does_not_check_whole_spool_per_event(tmp_path, monkeypatch):
    scans = 0
    original = DiagnosticStore.used_bytes.fget

    def counted(store):
        nonlocal scans
        scans += 1
        return original(store)

    monkeypatch.setattr(DiagnosticStore, "used_bytes", property(counted))
    with DiagnosticStore(tmp_path / "spool", limits(total_bytes=(1 << 20) + 8192,
                                                     backend_bytes=1 << 20,
                                                     baseline_bytes=1 << 19,
                                                     session_bytes=1 << 16,
                                                     segment_bytes=1 << 16)) as store:
        for _ in range(100):
            assert store.append(encoded(), stream="baseline")
        before = scans
        snapshot = store.snapshot(EventFilter(), datetime.now(timezone.utc))
        try:
            assert snapshot.counts["events"] == 100
            assert scans - before < 10
        finally:
            snapshot.release()
