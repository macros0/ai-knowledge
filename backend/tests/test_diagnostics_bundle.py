"""The exported archive is a fixed, bounded, revalidated support artifact."""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest

from app.models.diagnostics import BundleRequest
from app.services.diagnostics.bundle import BundleInputChanged, BundleTooLarge, build_bundle
from app.services.diagnostics.schema import DiagnosticLimits
from app.services.diagnostics.snapshot import collect_snapshot
from app.services.diagnostics.store import DiagnosticStore
from app.services.diagnostics.sanitize import encode_event


def test_zip_worker_import_does_not_load_sql_metadata_stack():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import app.services.diagnostics.bundle_worker; "
         "assert 'app.services.diagnostics.snapshot' not in sys.modules; "
         "assert 'sqlalchemy' not in sys.modules"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def limits(**changes):
    values = {"total_bytes": 8 * 1048576, "backend_bytes": 7 * 1048576,
              "frontend_bytes": 1048576, "baseline_bytes": 1048576,
              "session_bytes": 1048576, "segment_bytes": 65536,
              "bundle_bytes": 2 * 1048576, "min_free_bytes": 0}
    values.update(changes)
    return DiagnosticLimits(**values)


def event(now, *, component="backend"):
    return encode_event({
        "schema_version": 1, "event_id": str(uuid4()), "boot_id": str(uuid4()),
        "timestamp_utc": now.isoformat(), "component": component,
        "level": "ERROR", "event_code": ("operation_failed" if component == "backend"
                                              else "browser_error" if component == "browser" else "proxy_failed"),
        "origin": "client_reported" if component == "browser" else "server",
        "error_code": "browser_error" if component == "browser" else "internal_error",
    })


def period(now):
    return BundleRequest(from_utc=now - timedelta(hours=1), to_utc=now + timedelta(seconds=1))


def test_bundle_manifest_and_crc(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        assert store.append(event(now), stream="baseline")
        snapshot = collect_snapshot(period(now), cutoff_at=now + timedelta(seconds=1), store=store,
                                    metadata_provider=lambda _: ({"status": "ok"}, []))
        try:
            built = build_bundle(snapshot, store.root / "bundles" / "test.zip", store.limits)
            with ZipFile(built.path) as zip_file:
                assert zip_file.testzip() is None
                assert zip_file.namelist() == [
                    "summary.txt", "manifest.json", "events/backend.jsonl",
                    "events/frontend.jsonl", "events/browser.jsonl",
                    "snapshots/runtime.json", "snapshots/operations.json",
                ]
                manifest = json.loads(zip_file.read("manifest.json"))
                assert manifest["format_version"] == 2
                assert manifest["backend_schema_version"] == 2
                assert manifest["frontend_build_ids"] == ["unknown"]
                assert manifest["counter_scopes"]["recorder"] == "backend_process"
                assert manifest["counts"]["events"] == 1
                assert json.loads(zip_file.read("events/backend.jsonl"))["event_code"] == "operation_failed"
                assert manifest["checksums"]["events/backend.jsonl"]
                assert b"CANARY_PRIVATE" not in built.path.read_bytes()
            assert built.size_bytes == built.path.stat().st_size
            assert built.sha256 and len(built.sha256) == 64
        finally:
            snapshot.release()


def test_bundle_revalidates_each_component_without_rechecking_other_components(tmp_path, monkeypatch):
    from app.services.diagnostics import bundle as bundle_module

    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        for component in ("backend", "frontend", "browser"):
            for _ in range(20):
                assert store.append(event(now, component=component), stream="baseline")
        snapshot = collect_snapshot(period(now), cutoff_at=now + timedelta(seconds=1), store=store)
        original = bundle_module.sanitize_event
        calls = 0

        def counted(raw):
            nonlocal calls
            calls += 1
            return original(raw)

        monkeypatch.setattr(bundle_module, "sanitize_event", counted)
        try:
            built = build_bundle(snapshot, store.root / "bundles" / "mixed.zip", store.limits)
            with ZipFile(built.path) as archive:
                assert all(archive.read(f"events/{component}.jsonl").count(b"\n") == 20
                           for component in ("backend", "frontend", "browser"))
            assert calls <= 2 * 60 + 10
        finally:
            snapshot.release()


def test_bundle_rejects_snapshot_changed_between_manifest_and_zip(tmp_path, monkeypatch):
    from app.services.diagnostics import bundle as bundle_module

    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        assert store.append(event(now), stream="baseline")
        snapshot = collect_snapshot(period(now), cutoff_at=now + timedelta(seconds=1), store=store)
        original = bundle_module._safe_lines
        calls = 0

        def changed(paths, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                snapshot.paths[0].write_bytes(event(now))
            yield from original(paths, **kwargs)

        monkeypatch.setattr(bundle_module, "_safe_lines", changed)
        target = store.root / "bundles" / "changed.zip"
        try:
            with pytest.raises(BundleInputChanged):
                build_bundle(snapshot, target, store.limits)
            assert not target.exists()
        finally:
            snapshot.release()


def test_bundle_rejects_invalid_line_appended_after_manifest_pass(tmp_path, monkeypatch):
    from app.services.diagnostics import bundle as bundle_module

    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        assert store.append(event(now), stream="baseline")
        snapshot = collect_snapshot(period(now), cutoff_at=now + timedelta(seconds=1), store=store)
        original = bundle_module._safe_lines
        calls = 0

        def changed(paths, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                with snapshot.paths[0].open("ab") as handle:
                    handle.write(b'{"component":"unknown","secret":"CANARY_PRIVATE"}\n')
            yield from original(paths, **kwargs)

        monkeypatch.setattr(bundle_module, "_safe_lines", changed)
        target = store.root / "bundles" / "invalid-late.zip"
        try:
            with pytest.raises(BundleInputChanged):
                build_bundle(snapshot, target, store.limits)
            assert not target.exists()
        finally:
            snapshot.release()


def test_partial_summary_agrees_with_manifest_on_invalid_snapshot_line(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        assert store.append(event(now), stream="baseline")
        snapshot = collect_snapshot(period(now), cutoff_at=now + timedelta(seconds=1), store=store)
        snapshot.partial = False
        snapshot.gaps = ()
        try:
            with snapshot.paths[0].open("ab") as handle:
                handle.write(b'{"component":"unknown","secret":"CANARY_PRIVATE"}\n')
            built = build_bundle(snapshot, store.root / "bundles" / "partial-summary.zip", store.limits)
            with ZipFile(built.path) as archive:
                assert json.loads(archive.read("manifest.json"))["partial"] is True
                assert b"Partial: yes" in archive.read("summary.txt")
                assert b"CANARY_PRIVATE" not in archive.read("summary.txt")
        finally:
            snapshot.release()


def test_safe_snapshot_when_db_unavailable(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        assert store.append(event(now), stream="baseline")
        def unavailable(_):
            raise OSError("CANARY_PRIVATE database password")
        snapshot = collect_snapshot(period(now), cutoff_at=now + timedelta(seconds=1), store=store,
                                    metadata_provider=unavailable)
        try:
            built = build_bundle(snapshot, store.root / "bundles" / "partial.zip", store.limits)
            with ZipFile(built.path) as archive:
                assert json.loads(archive.read("manifest.json"))["partial"] is True
                assert b"CANARY_PRIVATE" not in built.path.read_bytes()
                assert json.loads(archive.read("snapshots/operations.json")) == []
        finally:
            snapshot.release()


def test_recorder_loss_marks_online_bundle_partial(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        snapshot = collect_snapshot(
            period(now), cutoff_at=now, store=store,
            metadata_provider=lambda _: ({"status": "ok"}, []),
            recorder_status=lambda: {"dropped": 2, "expired_queue": 1,
                                     "drain_timeouts": 1, "invalid": 0, "storage_errors": 0},
        )
        try:
            built = build_bundle(snapshot, store.root / "bundles" / "loss.zip", store.limits)
            with ZipFile(built.path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            assert manifest["partial"] is True
            assert "recorder_loss" in manifest["gaps"]
            assert manifest["counts"]["recorder_dropped"] == 2
            assert manifest["counts"]["recorder_expired_queue"] == 1
        finally:
            snapshot.release()


def test_intentional_success_sampling_is_reported_without_loss_gap(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        snapshot = collect_snapshot(
            period(now), cutoff_at=now, store=store,
            metadata_provider=lambda _: ({"status": "ok"}, []),
            recorder_status=lambda: {"sampled_success": 30},
        )
        try:
            assert snapshot.counts["recorder_sampled_success"] == 30
            assert "recorder_loss" not in snapshot.gaps
        finally:
            snapshot.release()


def test_snapshot_flushes_accepted_events_before_copy(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        calls = []

        def barrier():
            calls.append("before_snapshot")
            assert store.append(event(now), stream="baseline")
            return True

        snapshot = collect_snapshot(period(now), cutoff_at=now + timedelta(seconds=1), store=store,
                                    recorder_barrier=barrier, recorder_status=lambda: {"queued": 0})
        try:
            assert calls == ["before_snapshot"]
            assert snapshot.counts["events"] == 1
            assert "recorder_loss" not in snapshot.gaps
        finally:
            snapshot.release()


def test_snapshot_marks_incomplete_when_writer_barrier_cannot_finish(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        snapshot = collect_snapshot(period(now), cutoff_at=now, store=store,
                                    recorder_barrier=lambda: False,
                                    recorder_status=lambda: {"queued": 0})
        try:
            assert snapshot.partial is True
            assert "recorder_loss" in snapshot.gaps
        finally:
            snapshot.release()


def test_unknown_and_corrupt_lines_are_counted(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        assert store.append(event(now), stream="baseline")
        path = next((store.root / "events" / "baseline").glob("*.jsonl"))
        with path.open("ab") as handle:
            handle.write(b'{"schema_version":999,"secret":"CANARY_PRIVATE"}\n')
            handle.write(b'{"broken":"CANARY_PRIVATE"')
        snapshot = collect_snapshot(period(now), cutoff_at=now + timedelta(seconds=1), store=store)
        try:
            built = build_bundle(snapshot, store.root / "bundles" / "corrupt.zip", store.limits)
            with ZipFile(built.path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                assert manifest["counts"]["invalid"] >= 1
                assert manifest["counts"]["truncated"] >= 1
                assert archive.read("events/backend.jsonl").count(b"\n") == 1
                assert b"CANARY_PRIVATE" not in built.path.read_bytes()
        finally:
            snapshot.release()


def test_cutoff_freezes_bundle(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        assert store.append(event(now), stream="baseline")
        snapshot = collect_snapshot(period(now), cutoff_at=now + timedelta(seconds=1), store=store)
        try:
            assert store.append(event(now + timedelta(seconds=2)), stream="baseline")
            built = build_bundle(snapshot, store.root / "bundles" / "cutoff.zip", store.limits)
            with ZipFile(built.path) as archive:
                assert archive.read("events/backend.jsonl").count(b"\n") == 1
        finally:
            snapshot.release()


def test_incompressible_input_reservation(tmp_path):
    now = datetime.now(timezone.utc)
    constrained = limits(bundle_bytes=1024)
    with DiagnosticStore(tmp_path / "spool", constrained) as store:
        for _ in range(6):
            assert store.append(event(now), stream="baseline")
        snapshot = collect_snapshot(period(now), cutoff_at=now + timedelta(seconds=1), store=store)
        try:
            target = store.root / "bundles" / "too-large.zip"
            with pytest.raises(BundleTooLarge):
                build_bundle(snapshot, target, constrained)
            assert not target.exists()
        finally:
            snapshot.release()


def test_snapshot_metadata_is_whitelisted_without_health_probe(tmp_path, monkeypatch):
    from app.services import health
    monkeypatch.setattr(health, "get_health", lambda: pytest.fail("snapshot started a probe"))
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        snapshot = collect_snapshot(period(now), cutoff_at=now, store=store,
                                    metadata_provider=lambda _: (
                                        {"status": "ok", "secret": "CANARY_PRIVATE", "dependencies": {
                                            "llm": {"status": "down", "error": "CANARY_PRIVATE"}}},
                                        [{"kind": "document", "id": "CANARY_PRIVATE", "status": "done",
                                          "filename": "CANARY_PRIVATE"}]))
        try:
            built = build_bundle(snapshot, store.root / "bundles" / "safe.zip", store.limits)
            assert b"CANARY_PRIVATE" not in built.path.read_bytes()
            with ZipFile(built.path) as archive:
                assert json.loads(archive.read("snapshots/operations.json")) == []
                assert json.loads(archive.read("snapshots/runtime.json"))["dependencies"]["llm"] == {"status": "down"}
        finally:
            snapshot.release()


def test_frontend_snapshot_is_copied_and_revalidated(tmp_path):
    now = datetime.now(timezone.utc)
    front = tmp_path / "frontend"
    segment_dir = front / "events" / "baseline"
    segment_dir.mkdir(parents=True)
    source = segment_dir / f"{int(now.timestamp() * 1000)}_{uuid4()}.jsonl"
    source.write_bytes(event(now, component="frontend") + b'{"private":"CANARY_PRIVATE"}\n')
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        snapshot = collect_snapshot(period(now), cutoff_at=now, store=store,
                                    metadata_provider=lambda _: ({"status": "ok"}, []),
                                    frontend_root=front)
        try:
            built = build_bundle(snapshot, store.root / "bundles" / "frontend.zip", store.limits)
            with ZipFile(built.path) as archive:
                assert archive.read("events/frontend.jsonl").count(b"\n") == 1
                manifest = json.loads(archive.read("manifest.json"))
                assert manifest["counts"]["invalid"] >= 1
                assert b"CANARY_PRIVATE" not in built.path.read_bytes()
        finally:
            snapshot.release()


def test_frontend_append_during_snapshot_keeps_initial_boundary(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    front = tmp_path / "frontend"
    segment_dir = front / "events" / "baseline"
    segment_dir.mkdir(parents=True)
    source = segment_dir / f"{int(now.timestamp() * 1000)}_{uuid4()}.jsonl"
    source.write_bytes(event(now, component="frontend"))
    (front / "status.json").write_text(json.dumps({
        "schema_version": 1, "boot_id": str(uuid4()), "running": True,
        "storage_degraded": False, "dropped": 0, "invalid": 0,
        "expired_queue": 0, "queued": 0,
    }))
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        original = store.write_bytes
        appended = False

        def write(path, data, *, append=False):
            nonlocal appended
            if path.name == "frontend.jsonl" and not appended:
                appended = True
                with source.open("ab") as handle:
                    handle.write(event(now, component="frontend"))
            return original(path, data, append=append)

        monkeypatch.setattr(store, "write_bytes", write)
        snapshot = collect_snapshot(period(now), cutoff_at=now, store=store,
                                    metadata_provider=lambda _: ({"status": "ok"}, []),
                                    frontend_root=front)
        try:
            assert "frontend_unavailable" not in snapshot.gaps
            assert snapshot.counts["frontend_events"] == 1
        finally:
            snapshot.release()


def test_frontend_copy_does_not_check_whole_spool_per_event(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    front = tmp_path / "frontend"
    segment_dir = front / "events" / "baseline"
    segment_dir.mkdir(parents=True)
    source = segment_dir / f"{int(now.timestamp() * 1000)}_{uuid4()}.jsonl"
    source.write_bytes(b"".join(event(now, component="frontend") for _ in range(100)))
    (front / "status.json").write_text(json.dumps({
        "schema_version": 1, "boot_id": str(uuid4()), "running": True,
        "storage_degraded": False, "dropped": 0, "invalid": 0,
        "expired_queue": 0, "queued": 0,
    }))
    scans = 0
    original = DiagnosticStore.used_bytes.fget

    def counted(store):
        nonlocal scans
        scans += 1
        return original(store)

    monkeypatch.setattr(DiagnosticStore, "used_bytes", property(counted))
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        snapshot = collect_snapshot(period(now), cutoff_at=now, store=store,
                                    metadata_provider=lambda _: ({"status": "ok"}, []),
                                    frontend_root=front)
        try:
            assert snapshot.counts["frontend_events"] == 100
            assert scans < 10
        finally:
            snapshot.release()


def test_frontend_append_between_stat_and_open_keeps_initial_boundary(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    front = tmp_path / "frontend"
    segment_dir = front / "events" / "baseline"
    segment_dir.mkdir(parents=True)
    source = segment_dir / f"{int(now.timestamp() * 1000)}_{uuid4()}.jsonl"
    source.write_bytes(event(now, component="frontend"))
    (front / "status.json").write_text(json.dumps({
        "schema_version": 1, "boot_id": str(uuid4()), "running": True,
        "storage_degraded": False, "dropped": 0, "invalid": 0,
        "expired_queue": 0, "queued": 0,
    }))
    original_open = Path.open
    appended = False

    def opening(path, mode="r", *args, **kwargs):
        nonlocal appended
        if path == source and mode == "rb" and not appended:
            appended = True
            with original_open(path, "ab") as handle:
                handle.write(event(now, component="frontend"))
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opening)
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        snapshot = collect_snapshot(period(now), cutoff_at=now, store=store,
                                    metadata_provider=lambda _: ({"status": "ok"}, []),
                                    frontend_root=front)
        try:
            assert "frontend_unavailable" not in snapshot.gaps
            assert snapshot.counts["frontend_events"] == 1
        finally:
            snapshot.release()


def test_frontend_spool_loss_and_bad_status_mark_bundle_partial(tmp_path):
    now = datetime.now(timezone.utc)
    front = tmp_path / "frontend"
    (front / "events" / "baseline").mkdir(parents=True)
    status_file = front / "status.json"
    status_file.write_text(json.dumps({
        "schema_version": 1, "boot_id": str(uuid4()), "running": True,
        "storage_degraded": False, "written": 10, "dropped": 2,
        "invalid": 0, "repeats": 0, "expired_queue": 0, "queued": 0,
    }))
    with DiagnosticStore(tmp_path / "spool", limits()) as store:
        for name in ("frontend-loss.zip", "frontend-unknown.zip"):
            snapshot = collect_snapshot(period(now), cutoff_at=now, store=store,
                                        metadata_provider=lambda _: ({"status": "ok"}, []),
                                        frontend_root=front)
            try:
                built = build_bundle(snapshot, store.root / "bundles" / name, store.limits)
                with ZipFile(built.path) as archive:
                    manifest = json.loads(archive.read("manifest.json"))
                assert manifest["partial"] is True
                if name == "frontend-loss.zip":
                    assert "frontend_loss" in manifest["gaps"]
                    assert manifest["counts"]["frontend_dropped"] == 2
                else:
                    assert "frontend_status_unavailable" in manifest["gaps"]
                assert b"CANARY_PRIVATE" not in built.path.read_bytes()
            finally:
                snapshot.release()
            status_file.write_text('{"secret":"CANARY_PRIVATE"}')
