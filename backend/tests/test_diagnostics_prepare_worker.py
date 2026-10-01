"""Preparation runs in a child and returns only validated, credited output."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.services.diagnostics.prepare import prepare_in_child
from app.services.diagnostics.schema import DiagnosticLimits, EventFilter
from app.services.diagnostics.sanitize import encode_event
from app.services.diagnostics.store import DiagnosticStore


def test_prepare_worker_uses_spawn_without_parent_fork(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path
    import app.services.diagnostics.prepare as module

    real_popen = subprocess.Popen
    worker_options = []
    posix_spawned = []
    if os.name == "posix":
        original_spawn = real_popen._posix_spawn

        def record_spawn(self, *args, **kwargs):
            if "app.services.diagnostics.prepare_worker" in self.args:
                posix_spawned.append(True)
            return original_spawn(self, *args, **kwargs)

        monkeypatch.setattr(real_popen, "_posix_spawn", record_spawn)

    def observe(command, **options):
        if "app.services.diagnostics.prepare_worker" in command:
            worker_options.append(options)
        return real_popen(command, **options)

    monkeypatch.setattr(module.subprocess, "Popen", observe)
    with DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        view = store.pin_read_view(EventFilter(), datetime.now(timezone.utc))
        job = str(uuid4())
        try:
            result = prepare_in_child(store, view, job)
            assert result.pid != os.getpid()
            assert result.counts["events"] == 0
            assert len(worker_options) == 1
            options = worker_options[0]
            if os.name == "posix":
                assert posix_spawned == [True]
                assert "cwd" not in options
                assert options["close_fds"] is False
                assert str(Path(__file__).resolve().parents[1]) in options["env"]["PYTHONPATH"].split(os.pathsep)
            else:
                assert options["cwd"] == str(Path(__file__).resolve().parents[1])
        finally:
            view.release()
            store.delete_tree(store.root / "snapshots" / job)


def test_prepare_executes_in_child_and_credits_selected_rows(tmp_path):
    now = datetime.now(timezone.utc)
    limits = DiagnosticLimits(min_free_bytes=0)
    with DiagnosticStore(tmp_path / "spool", limits) as store:
        for _ in range(3):
            assert store.append(encode_event({"schema_version": 1, "event_id": str(uuid4()),
                "boot_id": str(uuid4()), "timestamp_utc": now.isoformat(),
                "component": "backend", "origin": "server", "level": "ERROR",
                "event_code": "operation_failed", "error_code": "internal_error"}), stream="baseline")
        view = store.pin_read_view(EventFilter(), now + timedelta(seconds=1))
        before_reservations = store.reserved_bytes
        job = str(uuid4())
        try:
            result = prepare_in_child(store, view, job)
            assert result.pid != os.getpid()
            assert result.counts["events"] == 3
            assert result.paths[0].read_bytes().count(b"\n") == 3
            assert store.reserved_bytes == before_reservations
        finally:
            view.release()
            store.delete_tree(store.root / "snapshots" / job)


def test_prepare_in_child_accepts_unicode_and_spaces_in_root(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "проверка с пробелом", DiagnosticLimits(min_free_bytes=0)) as store:
        assert store.append(encode_event({"schema_version": 1, "event_id": str(uuid4()),
            "boot_id": str(uuid4()), "timestamp_utc": now.isoformat(),
            "component": "backend", "origin": "server", "level": "ERROR",
            "event_code": "operation_failed", "error_code": "internal_error"}), stream="baseline")
        view = store.pin_read_view(EventFilter(), now + timedelta(seconds=1))
        job = str(uuid4())
        try:
            result = prepare_in_child(store, view, job)
            assert result.counts["events"] == 1
            assert result.paths[0].read_bytes().count(b"\n") == 1
        finally:
            view.release()
            store.delete_tree(store.root / "snapshots" / job)


def test_prepare_filters_large_unrelated_prefix_before_requesting_credit(tmp_path):
    now = datetime.now(timezone.utc)
    wanted, other = "a" * 16, "b" * 16
    limits = DiagnosticLimits(min_free_bytes=0)
    with DiagnosticStore(tmp_path / "spool", limits) as store:
        for i in range(300):
            assert store.append(encode_event({"schema_version": 1, "event_id": str(uuid4()),
                "boot_id": str(uuid4()), "timestamp_utc": now.isoformat(),
                "component": "backend", "origin": "server", "level": "ERROR",
                "event_code": "operation_failed", "error_code": "internal_error",
                "doc_id": wanted if i == 0 else other}), stream="baseline")
        before = store.used_bytes
        view = store.pin_read_view(EventFilter(doc_id=wanted), now + timedelta(seconds=1))
        job = str(uuid4())
        try:
            result = prepare_in_child(store, view, job)
            assert result.counts["events"] == 1
            assert store.used_bytes - before < 4096
        finally:
            view.release()
            store.delete_tree(store.root / "snapshots" / job)


def test_near_quota_narrow_bundle_succeeds_and_wide_bundle_fails(tmp_path):
    import pytest
    from app.services.diagnostics.bundle import BundleTooLarge

    now = datetime.now(timezone.utc)
    wanted, other = "a" * 16, "b" * 16
    limits = DiagnosticLimits(total_bytes=512 * 1024, backend_bytes=448 * 1024,
                              frontend_bytes=64 * 1024, baseline_bytes=128 * 1024,
                              session_bytes=128 * 1024, segment_bytes=64 * 1024,
                              bundle_bytes=96 * 1024, min_free_bytes=0)
    with DiagnosticStore(tmp_path / "spool", limits) as store:
        for doc_id in [other] * 500 + [wanted]:
            assert store.append(encode_event({"schema_version": 1, "event_id": str(uuid4()),
                "boot_id": str(uuid4()), "timestamp_utc": now.isoformat(),
                "component": "backend", "origin": "server", "level": "ERROR",
                "event_code": "operation_failed", "error_code": "internal_error",
                "doc_id": doc_id}), stream="baseline")
        assert store.used_bytes >= limits.baseline_bytes * 0.7
        narrow = store.pin_read_view(EventFilter(doc_id=wanted), now + timedelta(seconds=1))
        job = str(uuid4())
        try:
            result = prepare_in_child(store, narrow, job)
            assert result.counts["events"] == 1
            assert not result.gaps
        finally:
            narrow.release()
            store.delete_tree(store.root / "snapshots" / job)
        wide = store.pin_read_view(EventFilter(), now + timedelta(seconds=1))
        try:
            with pytest.raises(BundleTooLarge):
                prepare_in_child(store, wide, str(uuid4()))
        finally:
            wide.release()

def test_prepare_accepts_append_after_pinned_prefix(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        def event():
            return encode_event({"schema_version": 1, "event_id": str(uuid4()),
                "boot_id": str(uuid4()), "timestamp_utc": now.isoformat(),
                "component": "backend", "origin": "server", "level": "ERROR",
                "event_code": "operation_failed", "error_code": "internal_error"})
        assert store.append(event(), stream="baseline")
        view = store.pin_read_view(EventFilter(), now + timedelta(seconds=1))
        assert store.append(event(), stream="baseline")
        job = str(uuid4())
        try:
            result = prepare_in_child(store, view, job)
            assert result.counts["events"] == 1
            assert not result.gaps
        finally:
            view.release()
            store.delete_tree(store.root / "snapshots" / job)


def test_prepare_detects_same_length_prefix_mutation_after_pin(tmp_path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        assert store.append(encode_event({"schema_version": 1, "event_id": str(uuid4()),
            "boot_id": str(uuid4()), "timestamp_utc": now.isoformat(),
            "component": "backend", "origin": "server", "level": "ERROR",
            "event_code": "operation_failed", "error_code": "internal_error"}), stream="baseline")
        view = store.pin_read_view(EventFilter(), now + timedelta(seconds=1))
        source = view.descriptors[0].path
        data = source.read_bytes()
        source.write_bytes(data.replace(b"internal_error", b"network_error "))
        job = str(uuid4())
        try:
            result = prepare_in_child(store, view, job)
            assert result.counts["events"] == 0
            assert "segment_unavailable" in result.gaps
        finally:
            view.release()
            store.delete_tree(store.root / "snapshots" / job)


def test_selected_output_over_bundle_limit_fails_before_publication(tmp_path):
    import pytest
    from app.services.diagnostics.bundle import BundleTooLarge
    now = datetime.now(timezone.utc)
    limits = DiagnosticLimits(total_bytes=8 * 1048576, backend_bytes=7 * 1048576,
        frontend_bytes=1048576, baseline_bytes=1048576, session_bytes=1048576,
        segment_bytes=65536, bundle_bytes=1024, min_free_bytes=0)
    with DiagnosticStore(tmp_path / "spool", limits) as store:
        for _ in range(10):
            assert store.append(encode_event({"schema_version": 1, "event_id": str(uuid4()),
                "boot_id": str(uuid4()), "timestamp_utc": now.isoformat(),
                "component": "backend", "origin": "server", "level": "ERROR",
                "event_code": "operation_failed", "error_code": "internal_error"}), stream="baseline")
        view = store.pin_read_view(EventFilter(), now + timedelta(seconds=1))
        job = str(uuid4())
        before = store.reserved_bytes
        try:
            with pytest.raises(BundleTooLarge):
                prepare_in_child(store, view, job)
            assert not (store.root / "snapshots" / job).exists()
            assert store.reserved_bytes == before
        finally:
            view.release()
