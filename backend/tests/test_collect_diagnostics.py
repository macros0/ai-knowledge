"""Offline support export remains useful when the application database is down."""
from datetime import datetime, timedelta, timezone
import json
import io
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from zipfile import ZipFile

from app.services.diagnostics.sanitize import encode_event
from app.services.diagnostics.schema import DiagnosticLimits
from app.services.diagnostics.store import DiagnosticStore
from scripts import collect_diagnostics


def _seed(root: Path):
    now = datetime.now(timezone.utc)
    with DiagnosticStore(root, DiagnosticLimits()) as store:
        assert store.append(encode_event({
            "schema_version": 1, "event_id": str(uuid4()), "boot_id": str(uuid4()),
            "timestamp_utc": now.isoformat(), "component": "backend", "level": "ERROR",
            "event_code": "operation_failed", "origin": "server", "error_code": "internal_error",
        }), stream="baseline")
    return now


def _args(root, output, now):
    return ["--root", str(root), "--since", (now - timedelta(minutes=1)).isoformat(),
            "--until", (now + timedelta(minutes=1)).isoformat(), "--output", str(output)]


def test_cli_works_without_database_and_never_exports_raw_files(tmp_path):
    root, output = tmp_path / "safe spool", tmp_path / "support.zip"
    now = _seed(root)
    (root / ".env").write_text("PASSWORD=CANARY_PRIVATE", encoding="utf-8")
    (root / "raw.log").write_text("CANARY_PRIVATE", encoding="utf-8")
    assert collect_diagnostics.main(_args(root, output, now)) == 0
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == [
            "summary.txt", "manifest.json", "events/backend.jsonl", "events/frontend.jsonl",
            "events/browser.jsonl", "snapshots/runtime.json", "snapshots/operations.json",
        ]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["collection_mode"] == "offline"
        assert manifest["audit_reconciliation"] == "pending_sql"
        assert manifest["partial"] is True
        assert manifest["counts"]["events"] >= 1
    assert b"CANARY_PRIVATE" not in output.read_bytes()
    assert any(b"offline_export_requested" in path.read_bytes()
               for path in (root / "events" / "baseline").glob("*.jsonl"))


def test_cli_refuses_live_backend_writer_and_existing_output(tmp_path):
    root, output = tmp_path / "spool", tmp_path / "support.zip"
    now = _seed(root)
    with DiagnosticStore(root, DiagnosticLimits()):
        assert collect_diagnostics.main(_args(root, output, now)) == 3
        assert not output.exists()
    output.write_bytes(b"KEEP")
    assert collect_diagnostics.main(_args(root, output, now)) == 2
    assert output.read_bytes() == b"KEEP"


def test_cli_invalid_input_and_corrupt_event_are_bounded(tmp_path):
    root, output = tmp_path / "spool", tmp_path / "support.zip"
    now = _seed(root)
    assert collect_diagnostics.main(_args(root, output, now)[:-1]) == 2
    source = next((root / "events" / "baseline").glob("*.jsonl"))
    with source.open("ab") as handle:
        handle.write(b'{"schema_version":1,"component":"database","secret":"CANARY_PRIVATE"}\n')
    assert collect_diagnostics.main(_args(root, output, now)) == 0
    with ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["partial"] is True
        assert manifest["counts"]["invalid"] >= 1
    assert b"CANARY_PRIVATE" not in output.read_bytes()


def test_output_quota_and_partial_copy_leave_no_archive(tmp_path, monkeypatch):
    root, output = tmp_path / "spool", tmp_path / "support.zip"
    now = _seed(root)
    actual_disk_usage = collect_diagnostics.shutil.disk_usage
    monkeypatch.setattr(collect_diagnostics.shutil, "disk_usage", lambda _: type("Usage", (), {"free": 0})())
    assert collect_diagnostics.main(_args(root, output, now)) == 4
    assert not output.exists()
    monkeypatch.setattr(collect_diagnostics.shutil, "disk_usage", actual_disk_usage)

    def interrupted(source, destination, *, length):
        destination.write(source.read(16))
        raise OSError("CANARY_PRIVATE interrupted copy")

    monkeypatch.setattr(collect_diagnostics.shutil, "copyfileobj", interrupted)
    assert collect_diagnostics.main(_args(root, output, now)) == 4
    assert not output.exists()


def test_help_and_dry_run_do_not_import_application(tmp_path):
    script = Path(collect_diagnostics.__file__)
    now = datetime.now(timezone.utc)
    for args in (["--help"], [*_args(tmp_path, tmp_path / "support.zip", now), "--dry-run"]):
        code = ("import runpy,sys; sys.argv=[sys.argv[1]]+sys.argv[2:]; "
                "\ntry: runpy.run_path(sys.argv[0],run_name='__main__')\n"
                "except SystemExit: pass\n"
                "print('APP_IMPORTED='+str(any(x.startswith('app.') for x in sys.modules)))")
        result = subprocess.run([sys.executable, "-c", code, str(script), *args],
                                capture_output=True, text=True, timeout=10, check=False)
        assert result.returncode == 0
        assert "APP_IMPORTED=False" in result.stdout


def test_offline_bundle_includes_only_allowlisted_container_state(tmp_path, monkeypatch):
    root, output = tmp_path / "spool", tmp_path / "support.zip"
    now = _seed(root)
    state = {"backend": {
        "status": "exited", "exit_code": 137, "oom_killed": True,
        "started_at": "2026-09-27T00:00:00Z", "finished_at": "2026-09-27T00:01:00Z",
        "image_id": "sha256:" + "a" * 64,
    }, "migrate": {
        "status": "exited", "exit_code": 1, "oom_killed": False,
        "started_at": "2026-09-27T00:00:00Z", "finished_at": "2026-09-27T00:01:00Z",
        "image_id": "sha256:" + "b" * 64,
    }}
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(state).encode())))
    assert collect_diagnostics.main([*_args(root, output, now), "--container-state-stdin"]) == 0
    with ZipFile(output) as archive:
        runtime = json.loads(archive.read("snapshots/runtime.json"))
        assert runtime["containers"] == state
        assert "container_state_unavailable" not in json.loads(archive.read("manifest.json"))["gaps"]
    assert b"CANARY_PRIVATE" not in output.read_bytes()


def test_offline_rejects_unallowlisted_container_state(tmp_path, monkeypatch):
    root, output = tmp_path / "spool", tmp_path / "support.zip"
    now = _seed(root)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"backend":{"env":"CANARY_PRIVATE"}}')))
    assert collect_diagnostics.main([*_args(root, output, now), "--container-state-stdin"]) == 2
    assert not output.exists()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps({"backend": {
        "status": [], "exit_code": 0, "oom_killed": False,
        "started_at": "2026-09-27T00:00:00Z", "finished_at": "2026-09-27T00:01:00Z",
        "image_id": "sha256:" + "a" * 64,
    }}).encode())))
    assert collect_diagnostics.main([*_args(root, output, now), "--container-state-stdin"]) == 2


def test_offline_container_state_gap_is_explicit(tmp_path, monkeypatch):
    root, output = tmp_path / "spool", tmp_path / "support.zip"
    now = _seed(root)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"{}")))
    assert collect_diagnostics.main([*_args(root, output, now), "--container-state-stdin",
                                     "--container-state-incomplete"]) == 0
    with ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["partial"] is True
        assert "container_state_unavailable" in manifest["gaps"]


def test_offline_reads_safe_policy_projection_without_database(tmp_path):
    from app.services.diagnostics.control import write_projection
    root, output = tmp_path / "spool", tmp_path / "support.zip"
    now = _seed(root)
    session_id = str(uuid4())
    policy = {"level": "standard", "version": 1, "aggregate_interval_ms": 5000,
              "success_limit_per_second": 20, "trace_limit_per_second": 20,
              "slow_limit_per_second": 20, "max_inflight_traces": 512,
              "slow_thresholds_ms": {"http_search": 1000, "qdrant_db": 250,
                                     "embeddings_proxy": 1000, "llm_chat": 30000, "pdf": 5000}}
    with DiagnosticStore(root, DiagnosticLimits()) as store:
        write_projection(store, boot_id=str(uuid4()), active={"id": session_id,
            "scope": "system", "expires_at": now + timedelta(minutes=5),
            "policy_snapshot": policy}, now=now, revision=1)
    assert collect_diagnostics.main(_args(root, output, now)) == 0
    with ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["capture_policies"][0]["session_id"] == session_id
    assert manifest["coverage_modes"]["sessions"][session_id] == "aggregated"
