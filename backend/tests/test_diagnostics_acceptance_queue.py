"""Acceptance transitions require numerical evidence, even after exit code zero."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_scripts import run_diagnostics_final_ct as queue


def matrix_fixture(*, bundle=False):
    rows = []
    for repeat in (1, 2, 3):
        for concurrency in (1, 8):
            base = {"corpus_size": 1, "repeat": repeat, "concurrency": concurrency,
                    "mode": "baseline", "p95_ms": 10, "warmup_seconds": 30,
                    "elapsed_seconds": 60, "requests": 1000,
                    "peak_rss_bytes": {"backend": 100, "frontend": 100},
                    "recorder_delta": dict.fromkeys(("dropped", "invalid", "expired_queue",
                                                     "storage_errors", "drain_timeouts"), 0),
                    "frontend_delta": dict.fromkeys(
                        ("dropped", "invalid", "expired_queue"), 0),
                    "response_equality_pass": True, "response_fingerprints": {"query": "sha"},
                    "capture_counts_pass": True, "bundle": None}
            if bundle:
                rows.append(base)
            for mode in ("baseline", "standard", "detailed"):
                row = {**base, "mode": mode}
                if bundle:
                    row["bundle"] = {"status": "ready", "size_bytes": 100,
                                     "observed_building": True, "overlapping_requests": 10,
                                     "phase_latencies": {"prepare": {"requests": 5}, "zip": {"requests": 5}}}
                rows.append(row)
    return {"image_ids": {"backend": "image-a", "frontend": "image-b"}, "rows": rows}


def configure(monkeypatch, tmp_path):
    monkeypatch.setattr(queue, "ROOT", tmp_path)
    monkeypatch.setattr(queue, "STATE", tmp_path / "ledger.json")
    monkeypatch.setattr(queue, "history", [])


def test_zero_exit_with_missing_capture_events_stops_next_stage(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    source = matrix_fixture(bundle=True)
    source["rows"][-2]["capture_counts_pass"] = False
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        if "--output" in command:
            Path(command[command.index("--output") + 1]).write_text(json.dumps(source))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(queue.subprocess, "run", execute)
    with pytest.raises(RuntimeError, match="all_capture_counts_pass"):
        queue.matrix("failed-counts", 1, "spool", bundle=True)
        queue.run("must-not-start", ["unexpected"])
    ledger = json.loads(queue.STATE.read_text())
    assert len(calls) == 1
    assert ledger[-1]["status"] == "failed"
    assert ledger[-1]["exit_code"] == 0
    assert ledger[-1]["failed_gates"] == ["all_capture_counts_pass"]
    assert (tmp_path / "failed-counts-analysis.json").exists()


def test_partial_matrix_cannot_pass_even_when_remaining_rows_are_green(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    source = matrix_fixture()
    source["rows"] = source["rows"][:-1]
    output = tmp_path / "partial.json"
    output.write_text(json.dumps(source))
    with pytest.raises(RuntimeError, match="Incomplete matrix"):
        queue.validate_matrix(output, 1)


def test_process_launch_failure_is_recorded_as_failed(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    def fail(*args, **kwargs):
        raise OSError("synthetic launch failure")
    monkeypatch.setattr(queue.subprocess, "run", fail)
    with pytest.raises(OSError):
        queue.run("cannot-launch", ["missing-program"])
    assert json.loads(queue.STATE.read_text())[-1]["status"] == "failed"


def test_tail_rejects_legacy_passed_ledger_without_numeric_proof():
    ledger = [{"stage": "testclient-search_stub-standard-c8-r3-v13", "status": "passed", "exit_code": 0}]
    with pytest.raises(RuntimeError, match="numerical gates"):
        queue.require_complete_main_ledger(ledger)


def test_green_matrix_records_gates_before_next_stage(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    output = tmp_path / "green.json"
    output.write_text(json.dumps(matrix_fixture()))
    queue.run("green", None, validate=lambda: queue.validate_matrix(output, 1))
    assert queue.history[-1]["status"] == "passed"
    assert queue.history[-1]["gates"] == dict.fromkeys(queue.MATRIX_GATES, True)


@pytest.mark.parametrize("options", [[], ["--capture-level", "standard"]])
def test_zero_exit_testclient_numeric_failure_stops_queue(monkeypatch, tmp_path, options):
    configure(monkeypatch, tmp_path)
    series = {"p95_ms": 10, "requests": 1000, "elapsed_seconds": 60,
              "peak_process_tree_rss_bytes": 100}
    output = tmp_path / "control.json"
    output.write_text(json.dumps({"warmup_seconds": 30, "baseline": series,
        "capture": {**series, "p95_ms": 12},
        "capture_build": {**series, "requests_overlapping_build": 10},
        "bundles": {"built": 1, "failed": 0},
        "recorder": dict.fromkeys(("dropped", "invalid", "expired_queue", "storage_errors", "drain_timeouts"), 0)}))
    monkeypatch.setattr(queue.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    with pytest.raises(RuntimeError, match="all_p95_pass"):
        queue.run("testclient-too-slow", ["python", "probe_diagnostics.py", *options, "--output", str(output)])
    assert queue.history[-1]["status"] == "failed"
    assert queue.history[-1]["exit_code"] == 0


def test_immutable_original_records_legacy_losses_without_relaxing_current_gate(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    series = {"p95_ms": 10, "requests": 1000, "elapsed_seconds": 60,
              "peak_process_tree_rss_bytes": 100}
    output = tmp_path / "reference.json"
    output.write_text(json.dumps({"warmup_seconds": 30, "baseline": series, "capture": series,
        "capture_build": {**series, "requests_overlapping_build": 10},
        "bundles": {"built": 1, "failed": 0},
        "recorder": {**dict.fromkeys(("invalid", "expired_queue", "storage_errors", "drain_timeouts"), 0),
                     "dropped": 17889}}))
    command = ["python", "probe_diagnostics.py", "--output", str(output)]
    with pytest.raises(queue.MatrixGateError, match="all_losses_pass"):
        queue.validate_command_result(command)
    original = ["-v", f"{tmp_path}/original-2614bfa/backend/app:/app/app:ro",
                "OKF_BUILD_REVISION=original-2614bfa", *command]
    result = queue.validate_command_result(original)
    assert result["all_losses_pass"] is False
    assert result["all_loss_counters_known_pass"] is True
    assert result["observed_losses"]["dropped"] == 17889
    assert result["performance_acceptance"] is False


def test_live_queue_rejects_passed_control_without_numeric_gates():
    from test_scripts.run_diagnostics_live_ct import require_remaining_controls
    with pytest.raises(RuntimeError, match="controls"):
        require_remaining_controls([{"stage": "native-ttl-quota-final-v13-v10", "status": "passed"}])


def test_live_queue_rejects_sla_exemption_for_current_controls():
    from test_scripts.run_diagnostics_live_ct import require_remaining_controls
    ledger = remaining_fixture()
    next(row for row in ledger if "-standard-" in row["stage"])["gates"]["performance_acceptance"] = False
    with pytest.raises(RuntimeError, match="controls"):
        require_remaining_controls(ledger)


def remaining_fixture():
    from test_scripts.run_diagnostics_live_ct import remaining_stages
    keys = ("all_duration_requests_pass", "all_p95_pass", "all_private_rss_pass",
            "all_losses_pass", "all_loss_counters_known_pass", "all_bundle_pass", "all_complete_pass",
            "all_bundle_integrity_pass", "all_response_equality_pass")
    return [{"stage": name, "status": "passed", "exit_code": 0,
             "gates": {**dict.fromkeys(keys, True), "performance_acceptance": "-original-" not in name}}
            for name in remaining_stages()]


def test_live_queue_accepts_complete_controls_with_explicit_reference_scope():
    from test_scripts.run_diagnostics_live_ct import require_remaining_controls
    ledger = remaining_fixture()
    assert len(ledger) == 39
    require_remaining_controls(ledger)
    ledger[-1]["gates"]["all_bundle_integrity_pass"] = False
    with pytest.raises(RuntimeError, match="controls"):
        require_remaining_controls(ledger)


def test_live_queue_rejects_duplicate_control_rows():
    from test_scripts.run_diagnostics_live_ct import require_remaining_controls
    ledger = remaining_fixture()
    ledger.append(ledger[0])
    with pytest.raises(RuntimeError, match="controls"):
        require_remaining_controls(ledger)


def test_baseline_off_control_rejects_actual_backend_flag_mismatch(monkeypatch):
    from test_scripts import run_diagnostics_live_ct as live
    class Client:
        def __init__(self, *args):
            pass
        def login(self):
            pass
        def call(self, *args):
            return {"capabilities": {"baseline": True}, "runtime": {"available": True}}
    monkeypatch.setattr(live, "Client", Client, raising=False)
    def must_not_measure(*args, **kwargs):
        raise AssertionError("Measurement started before checking the actual baseline flag")
    monkeypatch.setattr(live, "measure", must_not_measure)
    with pytest.raises(RuntimeError, match="baseline"):
        live.baseline_control(False, [])


@pytest.mark.parametrize("frontend_matches", [True, False])
def test_baseline_flags_require_both_components_off(monkeypatch, frontend_matches):
    from test_scripts import run_diagnostics_live_ct as live
    client = SimpleNamespace(call=lambda *args: {
        "capabilities": {"baseline": False}, "runtime": {"available": True}})
    monkeypatch.setattr(live, "_containers", lambda project: {"frontend": "synthetic-container"})
    monkeypatch.setattr(live.subprocess, "check_output", lambda *args, **kwargs: json.dumps(not frontend_matches))
    if frontend_matches:
        assert live.baseline_flags(client, False) == {"backend": False, "frontend": False}
    else:
        with pytest.raises(RuntimeError, match="frontend baseline"):
            live.baseline_flags(client, False)
