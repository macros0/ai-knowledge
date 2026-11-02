"""RPO health uses authenticated recovery time, not a green Job or copy mtime."""
from datetime import datetime, timedelta, timezone
import importlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_replica_backup import KEY, bound_writer, fixture_plan


NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
INSTALLATION = "a" * 32
STORE = "independent-backup"


def health():
    name = "app.deployment.replica_backup_health"
    assert importlib.util.find_spec(name) is not None, "Authenticated backup health and attempt tracking are missing"
    return importlib.import_module(name)


def archive(tmp_path, *, age=60):
    plan, payloads = fixture_plan(consistent_at=NOW - timedelta(seconds=age))
    root = tmp_path / "archive"
    with bound_writer(root, plan, KEY, source_guard=lambda: plan.source) as writer:
        for entry_id, data in payloads.items():
            writer.add(entry_id, BytesIO(data))
        writer.finish(now=NOW + timedelta(seconds=max(0, -age)))
    return root


def assess(root, **kwargs):
    return health().assess_backup(root, KEY, installation_id=INSTALLATION,
        destination_store_id=STORE, now=NOW, **kwargs)


@pytest.mark.parametrize("age,alerts", [(60, []), (86400, []), (86401, ["backup_recovery_point_stale"])])
def test_rpo_uses_consistent_at_even_when_copy_just_completed(tmp_path, age, alerts):
    root = archive(tmp_path, age=age)
    result = assess(root)
    assert result["alerts"] == alerts
    assert result["recovery_point_age_seconds"] == age
    assert result["state"] == ("alert" if alerts else "healthy")
    assert result["archive_verified"] is True
    assert result["restore_verified"] is False and result["rpo_assured"] is False


@pytest.mark.parametrize("fault", ["missing", "wrong_key", "ciphertext", "partial", "foreign_installation", "foreign_store", "future"])
def test_unusable_or_foreign_copy_never_becomes_healthy(tmp_path, fault):
    root = archive(tmp_path, age=-60 if fault == "future" else 60)
    key, installation, store = KEY, INSTALLATION, STORE
    if fault == "missing":
        root = tmp_path / "absent"
    elif fault == "wrong_key":
        key = b"x" * 32
    elif fault == "ciphertext":
        path = next((root / "objects").glob("*.bin"))
        data = bytearray(path.read_bytes()); data[-1] ^= 1; path.write_bytes(data)
    elif fault == "partial":
        # A tampered or partial manifest is not a usable recovery point.
        path = root / "manifest.json"
        value = json.loads(path.read_bytes()); value["status"] = "incomplete"
        path.write_text(json.dumps(value))
    elif fault == "foreign_installation":
        installation = "c" * 32
    elif fault == "foreign_store":
        store = "other-store"
    result = health().assess_backup(root, key, installation_id=installation, destination_store_id=store, now=NOW)
    assert result["state"] == "alert" and result["alerts"]
    assert result["rpo_assured"] is False
    assert "PRIVATE_SOURCE_CONTENT" not in json.dumps(result)


def test_attempt_cannot_be_overwritten_or_finished_by_another_owner(tmp_path):
    module = health(); path = tmp_path / "attempt.json"
    attempt = module.start_attempt(path, installation_id=INSTALLATION, now=NOW - timedelta(seconds=30))
    assert module.load_attempt(path) == attempt
    with pytest.raises(ValueError):
        module.start_attempt(path, installation_id=INSTALLATION, now=NOW)
    with pytest.raises(ValueError):
        module.finish_attempt(path, attempt.model_copy(update={"attempt_id": "f" * 32}), failed=True, now=NOW)
    assert module.load_attempt(path) == attempt
    finished = module.finish_attempt(path, attempt, failed=True, now=NOW)
    assert finished.state == "failed" and finished.error_code == "backup_failed"
    assert module.start_attempt(path, installation_id=INSTALLATION, now=NOW).attempt_id != attempt.attempt_id


@pytest.mark.parametrize("state,alerts,status", [
    ("running", [], "pending"),
    ("overdue", ["backup_attempt_overdue"], "alert"),
    ("failed", ["backup_last_attempt_failed"], "alert"),
    ("complete", [], "healthy"),
    ("other_backup", ["backup_selection_stale"], "alert"),
    ("foreign", ["backup_attempt_unverifiable"], "alert"),
    ("tampered", ["backup_attempt_unverifiable"], "alert"),
    ("missing", ["backup_attempt_unverifiable"], "alert"),
])
def test_latest_attempt_failure_or_uncertainty_cannot_hide_behind_old_good_copy(tmp_path, state, alerts, status):
    module = health(); root = archive(tmp_path); path = tmp_path / "attempt.json"
    if state != "missing":
        attempt = module.start_attempt(path, installation_id="c" * 32 if state == "foreign" else INSTALLATION,
            now=NOW - timedelta(seconds=3601 if state == "overdue" else 30))
        if state == "failed":
            module.finish_attempt(path, attempt, failed=True, now=NOW)
        elif state in {"complete", "other_backup"}:
            module.finish_attempt(path, attempt, backup_id=("b" if state == "complete" else "e") * 32, now=NOW)
        elif state == "tampered":
            path.write_text('{"DATABASE_URL":"PRIVATE_SECRET_CANARY"}')
    result = assess(root, attempt_path=path)
    assert result["alerts"] == alerts and result["state"] == status
    assert "PRIVATE_SECRET_CANARY" not in json.dumps(result)


def test_attempt_rejects_clock_regression_foreign_installation_and_incomplete_success(tmp_path):
    module = health(); path = tmp_path / "attempt.json"
    attempt = module.start_attempt(path, installation_id=INSTALLATION, now=NOW)
    for kwargs in ({"failed": True, "now": NOW - timedelta(seconds=1)}, {"now": NOW},
                   {"failed": True, "backup_id": "b" * 32, "now": NOW}):
        with pytest.raises(ValueError):
            module.finish_attempt(path, attempt, **kwargs)
    module.finish_attempt(path, attempt, failed=True, now=NOW)
    with pytest.raises(ValueError):
        module.start_attempt(path, installation_id="f" * 32, now=NOW)


def test_health_rejects_naive_time_or_unbounded_policy(tmp_path):
    root = archive(tmp_path)
    for kwargs in ({"now": NOW.replace(tzinfo=None)}, {"max_age_seconds": 0}, {"attempt_timeout_seconds": -1}):
        values = {"installation_id": INSTALLATION, "destination_store_id": STORE, "now": NOW, **kwargs}
        with pytest.raises(ValueError):
            health().assess_backup(root, KEY, **values)


def test_oversized_attempt_is_rejected_without_reading_provider_inputs(tmp_path):
    root = archive(tmp_path); path = tmp_path / "attempt.json"
    path.write_bytes(b"PRIVATE_SECRET_CANARY" * 2000)
    result = assess(root, attempt_path=path)
    assert result["alerts"] == ["backup_attempt_unverifiable"]
    assert "PRIVATE_SECRET_CANARY" not in json.dumps(result)


@pytest.mark.parametrize("failure", [False, True])
def test_backup_cli_persists_attempt_before_provider_work_and_records_only_fixed_status(tmp_path, monkeypatch, capsys, failure):
    from tests.test_replica_backup_coordinator import config, coordinator
    module = coordinator(); health_module = health()
    inputs = config(); inputs["installation_id"] = INSTALLATION
    config_path = tmp_path / "config.json"; config_path.write_text(json.dumps(inputs))
    attempt_path = tmp_path / "attempt.json"
    manifest_root = archive(tmp_path)
    from app.deployment.replica_backup import verify_backup
    manifest = verify_backup(manifest_root, KEY)
    observed = []
    def run(*args, **kwargs):
        observed.append(health_module.load_attempt(attempt_path).state)
        if failure:
            raise RuntimeError("PRIVATE_SECRET_CANARY")
        return manifest
    monkeypatch.setattr(module, "run_backup", run)
    argv = ["backup", "--config", str(config_path), "--namespace-uid", "owned", "--attempt-journal", str(attempt_path)]
    for option in ("evidence-file", "archive", "key-file", "pg-dump", "report"):
        argv += ["--" + option, str(tmp_path / option)]
    monkeypatch.setattr(sys, "argv", argv)
    assert module.main() == (2 if failure else 0)
    assert observed == ["running"]
    final = health_module.load_attempt(attempt_path)
    assert final.state == ("failed" if failure else "complete")
    assert final.backup_id == (None if failure else manifest.plan.backup_id)
    assert "PRIVATE_SECRET_CANARY" not in attempt_path.read_text()
    printed = capsys.readouterr()
    assert "PRIVATE_SECRET_CANARY" not in printed.out + printed.err


def test_backup_cli_refuses_attempt_journal_outside_protected_config_directory_before_provider_work(tmp_path, monkeypatch):
    from tests.test_replica_backup_coordinator import config, coordinator
    module = coordinator(); health()
    private = tmp_path / "private"; private.mkdir()
    config_path = private / "config.json"; config_path.write_text(json.dumps(config()))
    called = []
    monkeypatch.setattr(module, "run_backup", lambda *args, **kwargs: called.append(True))
    argv = ["backup", "--config", str(config_path), "--namespace-uid", "owned", "--attempt-journal", str(tmp_path / "outside.json")]
    for option in ("evidence-file", "archive", "key-file", "pg-dump", "report"):
        argv += ["--" + option, str(tmp_path / option)]
    monkeypatch.setattr(sys, "argv", argv)
    assert module.main() == 2 and called == []
    assert not (tmp_path / "outside.json").exists()


def test_health_cli_exits_nonzero_on_stale_point_and_never_echoes_key_or_path(tmp_path, capsys):
    root = archive(tmp_path, age=86401)
    key = tmp_path / "PRIVATE_PATH_CANARY.key"; key.write_bytes(KEY)
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_backup_health.py"
    assert path.is_file(), "Backup health CLI is missing"
    spec = importlib.util.spec_from_file_location("health_cli", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    args = ["--archive", str(root), "--key-file", str(key), "--installation-id", INSTALLATION,
            "--destination-store-id", STORE, "--now", NOW.isoformat()]
    assert module.main(args) == 2
    result = capsys.readouterr()
    assert json.loads(result.out)["alerts"] == ["backup_recovery_point_stale"]
    assert "PRIVATE_PATH_CANARY" not in result.out + result.err


def test_backup_cli_refuses_success_receipt_from_another_installation(tmp_path, monkeypatch):
    from tests.test_replica_backup_coordinator import config, coordinator
    from app.deployment.replica_backup import verify_backup
    module = coordinator(); health_module = health()
    config_path = tmp_path / "config.json"; config_path.write_text(json.dumps(config()))
    path = tmp_path / "attempt.json"
    manifest = verify_backup(archive(tmp_path), KEY)
    assert manifest.plan.source.installation_id != config()["installation_id"]
    monkeypatch.setattr(module, "run_backup", lambda *args, **kwargs: manifest)
    args = ["backup", "--config", str(config_path), "--namespace-uid", "owned", "--attempt-journal", str(path)]
    for option in ("evidence-file", "archive", "key-file", "pg-dump", "report"):
        args += ["--" + option, str(tmp_path / option)]
    monkeypatch.setattr(sys, "argv", args)
    assert module.main() == 2
    assert health_module.load_attempt(path).state == "failed"


def test_only_one_concurrent_attempt_can_start(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    module = health(); path = tmp_path / "attempt.json"; barrier = Barrier(2)
    def start(_):
        barrier.wait(timeout=5)
        try:
            return module.start_attempt(path, installation_id=INSTALLATION, now=NOW).attempt_id
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(start, range(2)))
    assert len([v for v in results if v]) == 1
    assert module.load_attempt(path).attempt_id in results


def test_failed_atomic_status_write_preserves_running_attempt_and_removes_only_own_temp(tmp_path, monkeypatch):
    module = health(); path = tmp_path / "attempt.json"
    attempt = module.start_attempt(path, installation_id=INSTALLATION, now=NOW)
    foreign = tmp_path / "foreign.pending"; foreign.write_text("retained")
    def fail_replace(*_):
        raise OSError("PRIVATE_SECRET_CANARY")
    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError):
        module.finish_attempt(path, attempt, failed=True, now=NOW)
    assert json.loads(path.read_bytes())["state"] == "running"
    with pytest.raises(ValueError):
        module.load_attempt(path)
    assert list(tmp_path.glob("*.pending")) == [foreign]


def test_status_directory_sync_failure_cannot_leave_a_readable_complete_attempt(tmp_path, monkeypatch):
    module = health(); path = tmp_path / "attempt.json"
    attempt = module.start_attempt(path, installation_id=INSTALLATION, now=NOW)
    original_sync = module._sync_directory
    calls = []
    def fail_sync(*_):
        calls.append(True)
        if len(calls) == 2:
            raise OSError("PRIVATE_SECRET_CANARY")
        original_sync(path.parent)
    monkeypatch.setattr(module, "_sync_directory", fail_sync)
    with pytest.raises(OSError):
        module.finish_attempt(path, attempt, backup_id="b" * 32, now=NOW)
    with pytest.raises(ValueError):
        module.load_attempt(path)
    with pytest.raises(ValueError):
        module.start_attempt(path, installation_id=INSTALLATION, now=NOW)


def test_attempt_link_is_refused_without_touching_target(tmp_path):
    module = health(); target = tmp_path / "target.json"
    attempt = module.start_attempt(target, installation_id=INSTALLATION, now=NOW)
    link = tmp_path / "attempt.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Host does not permit symlink creation; required Linux cohort exercises it")
    with pytest.raises(ValueError):
        module.load_attempt(link)
    with pytest.raises(ValueError):
        module.start_attempt(link, installation_id=INSTALLATION, now=NOW)
    assert module.load_attempt(target) == attempt


@pytest.mark.parametrize("age,failed,code", [(60, False, 0), (86401, False, 2), (60, True, 2)])
def test_real_health_cli_process_reads_only_local_fixture_and_emits_rpo_or_error_alert(tmp_path, age, failed, code):
    root = archive(tmp_path, age=age)
    key = tmp_path / "key.bin"; key.write_bytes(KEY)
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_backup_health.py"
    command = [sys.executable, str(path), "--archive", str(root), "--key-file", str(key),
        "--installation-id", INSTALLATION, "--destination-store-id", STORE, "--now", NOW.isoformat()]
    if failed:
        attempt_path = tmp_path / "attempt.json"
        attempt = health().start_attempt(attempt_path, installation_id=INSTALLATION, now=NOW)
        health().finish_attempt(attempt_path, attempt, failed=True, now=NOW)
        command += ["--attempt-journal", str(attempt_path)]
    process = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert process.returncode == code
    result = json.loads(process.stdout)
    assert result["state"] == ("healthy" if code == 0 else "alert")
    assert result["recovery_point_age_seconds"] == age
    assert result["alerts"] == (["backup_last_attempt_failed"] if failed else ["backup_recovery_point_stale"] if age > 86400 else [])
    assert result["rpo_assured"] is False


def test_real_backup_preflight_failure_is_durable_and_detected_with_old_good_archive(tmp_path):
    from tests.test_replica_backup_coordinator import config
    root = archive(tmp_path); key = tmp_path / "key.bin"; key.write_bytes(KEY)
    inputs = config(); inputs["installation_id"] = INSTALLATION
    config_path = tmp_path / "config.json"; config_path.write_text(json.dumps(inputs))
    attempt_path = tmp_path / "attempt.json"
    scripts = Path(__file__).resolve().parents[2] / "scripts/kubernetes"
    command = [sys.executable, str(scripts / "replica_backup.py"), "--config", str(config_path),
        "--namespace-uid", "11111111-1111-1111-1111-111111111111", "--attempt-journal", str(attempt_path),
        "--evidence-file", str(tmp_path / "INTENTIONALLY_ABSENT.json"), "--archive", str(tmp_path / "new-copy"),
        "--key-file", str(key), "--pg-dump", str(tmp_path / "unused-pg-dump"), "--report", str(tmp_path / "unused-report")]
    process = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert process.returncode == 2
    attempt = health().load_attempt(attempt_path)
    assert attempt.state == "failed" and attempt.error_code == "backup_failed"
    # No Kubernetes or provider preflight can run before the missing local evidence.
    assert "FileNotFoundError" in process.stderr and not (tmp_path / "new-copy").exists()
    monitored = subprocess.run([sys.executable, str(scripts / "replica_backup_health.py"),
        "--archive", str(root), "--key-file", str(key), "--installation-id", INSTALLATION,
        "--destination-store-id", STORE, "--attempt-journal", str(attempt_path)],
        capture_output=True, text=True, timeout=30)
    assert monitored.returncode == 2
    result = json.loads(monitored.stdout)
    assert result["archive_verified"] is True
    assert "backup_last_attempt_failed" in result["alerts"]
    assert "INTENTIONALLY_ABSENT" not in monitored.stdout + monitored.stderr
