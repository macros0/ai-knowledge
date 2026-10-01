"""Sequential disposable CT102 acceptance queue; no production operations."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

if __package__:
    from .analyze_diagnostics_matrix import BaselineEvidenceError, analyze
    from .analyze_diagnostics_controls import analyze_testclient, analyze_fixed, analyze_storm
else:
    from analyze_diagnostics_matrix import BaselineEvidenceError, analyze
    from analyze_diagnostics_controls import analyze_testclient, analyze_fixed, analyze_storm

ROOT = Path("/opt/okf-diag-levels-20260929")
PROJECT = "okf-diag-levels-20260929"
IMAGE = "okf-diag-levels-backend:20260930-v15"
STATE = ROOT / "final-acceptance-v13-state.json"
history = []
MATRIX_GATES = ("all_p95_pass", "all_rss_pass", "all_duration_requests_pass", "all_losses_pass",
                "all_zip_overlap_pass", "all_response_equality_pass", "all_capture_counts_pass",
                "all_phase_distributions_pass")


class MatrixGateError(RuntimeError):
    def __init__(self, failed_gates):
        self.failed_gates = failed_gates
        super().__init__("Matrix gates failed: " + ", ".join(failed_gates))


def validate_command_result(command):
    probes = {Path(str(arg)).name for arg in command}
    known = {"probe_diagnostics.py", "probe_diagnostics_fixed_input.py", "probe_diagnostics_error_storm.py"}
    if not probes & known:
        if "probe_diagnostics_http_matrix.py" in probes:
            raise RuntimeError("HTTP matrix requires an explicit scenario validator")
        return None
    def option(name, default=None):
        return command[command.index(name) + 1] if name in command else default
    target = option("--output")
    if target is None:
        raise RuntimeError("Numerical probe lacks output artifact")
    output = ROOT / "fixed-probe" / target[len("/probe/"):] if target.startswith("/probe/") else Path(target)
    data = json.loads(output.read_text())
    smoke = float(option("--warmup-seconds", 30)) < 30 or float(option("--measurement-seconds", 60)) < 60
    original = ("OKF_BUILD_REVISION=original-2614bfa" in command
                and any(str(arg).endswith("/original-2614bfa/backend/app:/app/app:ro") for arg in command))
    if "probe_diagnostics.py" in probes:
        result = analyze_testclient(data, smoke=smoke, reference=original)
    elif "probe_diagnostics_fixed_input.py" in probes:
        concurrencies = [int(command[i + 1]) for i, value in enumerate(command) if value == "--concurrency"] or [1, 8]
        if option("--reference-version", "current") == "original" and not original:
            raise RuntimeError("Original control requires the immutable original app mount")
        result = analyze_fixed(data, records=int(option("--records", 20000)),
            repeats=int(option("--repeats", 3)), concurrencies=concurrencies, smoke=smoke,
            reference=original)
    else:
        result = analyze_storm(data, seconds=int(option("--seconds", 60)),
                               target_rate=int(option("--target-rate", 10000)))
    output.with_name(output.stem + "-analysis.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    # Immutable legacy is measured as a reference, including its observed losses.
    # It must have complete metrics; current builds still require zero losses.
    failed = [key for key, value in result.items() if key.startswith("all_") and value is not True
              and not (original and key == "all_losses_pass")]
    if failed:
        raise MatrixGateError(failed)
    return result


def validate_matrix(output, corpus, *, bundle=False, stopped=False):
    data = json.loads(output.read_text())
    expected = {(repeat, concurrency, mode, False)
                for repeat in (1, 2, 3) for concurrency in (1, 8)
                for mode in (("baseline",) if bundle else ("baseline", "standard", "detailed"))}
    if bundle:
        expected |= {(repeat, concurrency, mode, True)
                     for repeat in (1, 2, 3) for concurrency in (1, 8)
                     for mode in (("standard", "detailed") if stopped else ("baseline", "standard", "detailed"))}
    rows = data["rows"]
    actual = {(row["repeat"], row["concurrency"], row["mode"], bool(row.get("bundle"))) for row in rows}
    if (actual != expected or len(rows) != len(expected)
            or any(row["corpus_size"] != corpus for row in rows)
            or any(bool(row.get("stopped_before_bundle")) != stopped
                   for row in rows if row.get("bundle"))):
        raise RuntimeError("Incomplete matrix or incompatible scenario")
    images = data.get("image_ids", {})
    if (set(images) != {"backend", "frontend"}
            or any(not isinstance(value, str) or not value or value == "unknown" for value in images.values())):
        raise RuntimeError("Unknown matrix image IDs")
    reference = {**data, "rows": [row for row in rows if not row.get("bundle")]}
    try:
        result = analyze(reference, bundle=data) if bundle else analyze(data)
    except BaselineEvidenceError as exc:
        raise MatrixGateError(exc.failed_gates) from exc
    output.with_name(output.stem + "-analysis.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n")
    failed = [key for key in MATRIX_GATES if result.get(key) is not True]
    if failed:
        raise MatrixGateError(failed)
    return {key: value for key, value in result.items() if key.startswith("all_")}


def require_complete_main_ledger(ledger):
    required = {"validate-initial-active-matrix", "http-matrix-1doc-stopped-v13-v9",
                "http-matrix-100doc-v13-v9", "http-matrix-100doc-zip-v13-v9",
                "http-matrix-100doc-stopped-v13-v9"}
    matrix_stages = required.copy()
    required |= {"switch-to-100doc-v13-v9", "fixed-input-full-v13-v9", "native-ttl-quota-v13"}
    required |= {f"testclient-{scenario}-{mode}-c{concurrency}-r{repeat}-v13"
                 for scenario in ("settings", "search_stub") for mode in ("standard", "detailed")
                 for concurrency in (1, 8) for repeat in (1, 2, 3)}
    stages = {record["stage"]: record for record in ledger}
    if (not ledger or any(record["status"] != "passed" for record in ledger)
            or ledger[-1]["stage"] != "testclient-search_stub-detailed-c8-r3-v13"
            or not required <= stages.keys()
            or any(stages[stage].get("gates", {}).get(gate) is not True
                   for stage in matrix_stages for gate in MATRIX_GATES)):
        raise RuntimeError("Main queue is incomplete or lacks numerical gates; tail was not started")


def run(stage, command, *, validate=None):
    if command is None and validate is None:
        raise ValueError("A stage must execute a command or validate existing evidence")
    record = {"stage": stage, "started_at": datetime.now(timezone.utc).isoformat(), "status": "running"}
    history.append(record)
    STATE.write_text(json.dumps(history, indent=2) + "\n")
    print(json.dumps(record), flush=True)
    try:
        result = subprocess.run(command, cwd=ROOT, check=False) if command is not None else None
        record["exit_code"] = result.returncode if result is not None else None
        if result is not None and result.returncode:
            raise RuntimeError(f"Stage failed: {stage}")
        if validate:
            record["gates"] = validate()
        elif command is not None:
            gates = validate_command_result(command)
            if gates is not None:
                record["gates"] = gates
        record["status"] = "passed"
    except BaseException as exc:
        record.update(status="failed", error_type=type(exc).__name__)
        if isinstance(exc, MatrixGateError):
            record["failed_gates"] = exc.failed_gates
        raise
    finally:
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        STATE.write_text(json.dumps(history, indent=2) + "\n")


def matrix(stage, corpus, spool, *, bundle=False, stopped=False):
    command = ["python3", str(ROOT / "backend/test_scripts/probe_diagnostics_http_matrix.py"),
               "--corpus-size", str(corpus), "--output", str(ROOT / f"{stage}.json"),
               "--spool-root", str(ROOT / spool), "--repeats", "3"]
    if bundle:
        command.append("--with-bundle")
    if stopped:
        command += ["--stopped-bundle", "--admin-prefix", "diag.stop"]
    run(stage, command, validate=lambda: validate_matrix(
        ROOT / f"{stage}.json", corpus, bundle=bundle, stopped=stopped))


def private_python(arguments):
    return ["docker", "run", "--rm", "--security-opt", "apparmor=unconfined", "--network", "host",
            "-e", "PYTHONPATH=/app:/work/backend",
            "-v", f"{ROOT}/backend/test_scripts:/app/test_scripts:ro",
            "-v", f"{ROOT}/backend/tests:/work/backend/tests:ro",
            "-v", f"{ROOT}/tests/fixtures:/work/tests/fixtures:ro",
            "-v", f"{ROOT}/fixed-probe:/probe", "--workdir", "/work/backend",
            "--entrypoint", "python", IMAGE, *arguments]


def matrix_running(target):
    for path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if target in path.read_bytes():
                return True
        except OSError:
            continue
    return False


if __name__ == "__main__":
    if STATE.exists():
        raise FileExistsError("Existing queue ledger: inspect before retry")
    # Wait for the already running active 1-doc matrix, never overlap workloads.
    target = str(ROOT / "http-matrix-1doc-zip-v13-v9.json").encode()
    print("Waiting for active 1-doc matrix; subsequent stages are sequential", flush=True)
    while matrix_running(target):
        time.sleep(5)
    run("validate-initial-active-matrix", None, validate=lambda: validate_matrix(
        ROOT / "http-matrix-1doc-zip-v13-v9.json", 1, bundle=True))
    matrix("http-matrix-1doc-stopped-v13-v9", 1, "diagnostics-1doc", bundle=True, stopped=True)
    run("switch-to-100doc-v13-v9", ["docker", "compose", "--env-file", "runtime-bench.env", "-p", PROJECT,
         "-f", "docker-compose.yml", "-f", "compose-override.yml", "up", "-d", "--wait", "--wait-timeout", "180"])
    matrix("http-matrix-100doc-v13-v9", 100, "diagnostics-v4")
    matrix("http-matrix-100doc-zip-v13-v9", 100, "diagnostics-v4", bundle=True)
    matrix("http-matrix-100doc-stopped-v13-v9", 100, "diagnostics-v4", bundle=True, stopped=True)
    run("fixed-input-full-v13-v9", private_python([
        "/app/test_scripts/probe_diagnostics_fixed_input.py", "--root", "/probe/spool-final-v13",
        "--output", "/probe/fixed-input-full-v13-v9.json", "--records", "20000", "--repeats", "3"]))
    run("native-ttl-quota-v13", private_python(["-m", "pytest", "-q",
        "tests/test_diagnostics_final_acceptance.py", "--basetemp", "/tmp/diag-final-native-v13"]))
    for scenario in ("settings", "search_stub"):
        for repeat in range(1, 4):
            for concurrency in (1, 8):
                modes = ("standard", "detailed") if (repeat + concurrency) % 2 else ("detailed", "standard")
                for mode in modes:
                    stage = f"testclient-{scenario}-{mode}-c{concurrency}-r{repeat}-v13"
                    run(stage, private_python(["/app/test_scripts/probe_diagnostics.py",
                        "--root", f"/probe/{stage}", "--output", f"/probe/{stage}.json",
                        "--scenario", scenario, "--capture-level", mode,
                        "--concurrency", str(concurrency), "--warmup-seconds", "30",
                        "--measurement-seconds", "60", "--requests", "1000"]))
