"""Remaining live acceptance on frozen disposable images, after private gates."""
import json
import subprocess

if __package__:
    from . import run_diagnostics_final_ct as queue
    from .run_diagnostics_targeted_ct import EXPECTED_IMAGES
    from .probe_diagnostics_http_matrix import _containers, _image_ids, measure
    from .analyze_diagnostics_controls import duration, numeric
    from .analyze_diagnostics_matrix import loss_counters, FRONTEND_LOSS_KEYS
    from .probe_diagnostics_http import Client
else:
    import run_diagnostics_final_ct as queue
    from run_diagnostics_targeted_ct import EXPECTED_IMAGES
    from probe_diagnostics_http_matrix import _containers, _image_ids, measure
    from analyze_diagnostics_controls import duration, numeric
    from analyze_diagnostics_matrix import loss_counters, FRONTEND_LOSS_KEYS
    from probe_diagnostics_http import Client


def remaining_stages():
    stages = {"native-ttl-quota-final-v15-v11": ("native", False)}
    for scenario in ("search_stub", "settings"):
        for repeat in (1, 2, 3):
            for concurrency in (1, 8):
                for mode in ("standard", "detailed", "original"):
                    stages[f"testclient-{scenario}-{mode}-c{concurrency}-r{repeat}-v15-v11"] = (
                        "testclient", mode == "original")
    stages["fixed-input-full-v15-v11"] = ("fixed", False)
    stages["fixed-input-original-full-v15-v11"] = ("fixed", True)
    return stages


def require_remaining_controls(ledger):
    expected = remaining_stages()
    records = {row["stage"]: row for row in ledger}
    if set(records) != set(expected) or len(ledger) != len(expected):
        raise RuntimeError("Remaining controls are incomplete")
    for name, (kind, original) in expected.items():
        row = records[name]
        if row.get("status") != "passed" or row.get("exit_code") != 0:
            raise RuntimeError("Remaining controls contain a failed or unfinished stage")
        if kind == "native":
            continue
        keys = {"all_duration_requests_pass", "all_p95_pass", "all_private_rss_pass"}
        keys |= ({"all_loss_counters_known_pass", "all_bundle_pass"} if kind == "testclient" else
                 {"all_complete_pass", "all_bundle_integrity_pass", "all_response_equality_pass"})
        if kind == "testclient" and not original:
            keys.add("all_losses_pass")
        gates = row.get("gates", {})
        if (any(gates.get(key) is not True for key in keys)
                or gates.get("performance_acceptance") is not (not original)):
            raise RuntimeError("Remaining controls lack required numerical gates")


def frozen():
    if _image_ids(_containers(queue.PROJECT)) != EXPECTED_IMAGES:
        raise RuntimeError("Frozen images changed")


def run(stage, command, *, validate=None):
    frozen()
    def checked():
        result = validate() if validate else queue.validate_command_result(command)
        frozen()
        return result or {}
    queue.run(stage, command, validate=checked)


def compose(env, *, one_doc=False, fresh_app=False):
    command = ["docker", "compose", "--env-file", env, "-p", queue.PROJECT,
               "-f", "docker-compose.yml", "-f", "compose-override.yml"]
    if one_doc:
        command += ["-f", "compose-one-doc.yml"]
    command += ["-f", "compose-review-v15-v11.yml", "up", "-d", "--wait", "--wait-timeout", "180"]
    if fresh_app:
        command += ["--no-deps", "--force-recreate", "backend", "frontend"]
    return command


def matrix(corpus, spool, *, bundle=False, stopped=False):
    suffix = "stopped" if stopped else "zip" if bundle else "normal"
    stage = f"http-matrix-{corpus}doc-{suffix}-v15-v11"
    output = queue.ROOT / f"{stage}.json"
    command = ["python3", str(queue.ROOT / "backend/test_scripts/probe_diagnostics_http_matrix.py"),
               "--corpus-size", str(corpus), "--output", str(output), "--repeats", "3",
               "--spool-root", str(queue.ROOT / spool), "--bundle-poll-interval", "3"]
    if bundle:
        command.append("--with-bundle")
    if stopped:
        command += ["--stopped-bundle", "--admin-prefix", "diag.stop"]
    def validate():
        if json.loads(output.read_text()).get("image_ids") != EXPECTED_IMAGES:
            raise RuntimeError("Matrix artifact uses incompatible images")
        return queue.validate_matrix(output, corpus, bundle=bundle, stopped=stopped)
    run(stage, command, validate=validate)


def baseline_flags(client, enabled):
    status = client.call("GET", "/api/admin/diagnostics/status")
    backend = status.get("capabilities", {}).get("baseline")
    if backend is not enabled or status.get("runtime", {}).get("available") is not True:
        raise RuntimeError("Actual backend baseline flag/runtime differs from the control")
    script = "process.stdout.write(JSON.stringify(!['false','0'].includes((process.env.DIAGNOSTICS_BASELINE_ENABLED||'true').toLowerCase())))"
    frontend = json.loads(subprocess.check_output([
        "docker", "exec", _containers(queue.PROJECT)["frontend"], "node", "-e", script], text=True))
    if frontend is not enabled:
        raise RuntimeError("Actual frontend baseline flag differs from the control")
    return {"backend": backend, "frontend": frontend}


def baseline_control(enabled, rows):
    client = Client("http://127.0.0.1:18084")
    client.login()
    def save():
        (queue.ROOT / "baseline-off-control-final-v15-v11.json").write_text(json.dumps({
            "scope": "instrumentation control; does not replace SLA baseline",
            "image_ids": EXPECTED_IMAGES, "rows": rows}, indent=2) + "\n")
    start = len(rows)
    for concurrency in (1, 8):
        flags = baseline_flags(client, enabled)
        row = measure("http://127.0.0.1:18084", concurrency=concurrency,
                      warmup_seconds=30, measurement_seconds=60, min_requests=1000,
                      mode="baseline", repeat=1, corpus_size=100,
                      spool_root=queue.ROOT / "diagnostics-v4")
        row["baseline_enabled"] = enabled
        row.update(baseline_flags_before=flags, baseline_flags_after=None)
        rows.append(row)
        save()
        row["baseline_flags_after"] = baseline_flags(client, enabled)
        save()
    current = rows[start:]
    losses = [loss_counters(row.get("recorder_delta")) for row in current]
    losses += [loss_counters(row.get("frontend_delta"), FRONTEND_LOSS_KEYS) for row in current]
    gates = {"all_duration_requests_pass": all(duration(row) and row.get("warmup_seconds", 0) >= 30 for row in current),
             "all_metrics_known_pass": all(numeric(row.get("p95_ms")) and row["p95_ms"] > 0
                 and all(numeric(row.get("peak_rss_bytes", {}).get(name)) for name in ("backend", "frontend"))
                 for row in current),
             "all_losses_pass": all(value is not None and not any(value.values()) for value in losses),
             "all_runtime_flags_pass": all(row.get(key) == {"backend": enabled, "frontend": enabled}
                 for row in current for key in ("baseline_flags_before", "baseline_flags_after")),
             "all_response_equality_pass": all(row.get("response_equality_pass") is True for row in current)}
    if not enabled:
        gates["all_on_off_responses_pass"] = all(
            row["response_fingerprints"] == next(base["response_fingerprints"] for base in rows[:start]
                if base["concurrency"] == row["concurrency"]) for row in current)
    failed = [key for key, value in gates.items() if value is not True]
    if failed:
        raise queue.MatrixGateError(failed)
    return gates


def main():
    queue.STATE = queue.ROOT / "live-acceptance-v15-v11-state.json"
    if queue.STATE.exists():
        raise FileExistsError("Inspect the existing live ledger before a retry")
    require_remaining_controls(json.loads((queue.ROOT / "remaining-acceptance-v15-v11-state.json").read_text()))
    for bundle, stopped in ((False, False), (True, False), (True, True)):
        matrix(1, "diagnostics-1doc", bundle=bundle, stopped=stopped)
    run("switch-to-100doc-v15-v11", compose("runtime-bench.env"))
    for bundle, stopped in ((False, False), (True, False), (True, True)):
        matrix(100, "diagnostics-v4", bundle=bundle, stopped=stopped)
    run("error-storm-final-v15-v11", queue.private_python([
        "/app/test_scripts/probe_diagnostics_error_storm.py", "--root", "/probe/storm-final-v15-v11",
        "--output", "/probe/error-storm-final-v15-v11.json", "--seconds", "60", "--target-rate", "10000"]))
    rows = []
    run("fresh-baseline-on-apps-v15-v11", compose("runtime-bench.env", fresh_app=True))
    run("baseline-on-control-v15-v11", None, validate=lambda: baseline_control(True, rows))
    off_env = queue.ROOT / "runtime-baseline-off-v15-v11.env"
    keys = {"DIAGNOSTICS_BASELINE_ENABLED": "false", "OKF_RUNTIME_ENV_FILE": str(off_env)}
    lines = (queue.ROOT / "runtime-bench.env").read_text().splitlines()
    lines = [f"{key}={keys.pop(key)}" if (key := line.split("=", 1)[0]) in keys else line for line in lines]
    lines.extend(f"{key}={value}" for key, value in keys.items())
    off_env.write_text("\n".join(lines) + "\n")
    off_env.chmod(0o600)
    try:
        run("switch-baseline-off-v15-v11", compose(off_env.name, fresh_app=True))
        run("baseline-off-control-v15-v11", None, validate=lambda: baseline_control(False, rows))
    finally:
        run("restore-baseline-on-v15-v11", compose("runtime-bench.env", fresh_app=True))
    run("lifecycle-final-v15-v11", ["python3", str(queue.ROOT / "backend/test_scripts/probe_diagnostics_lifecycle.py"),
        "--output", str(queue.ROOT / "lifecycle-final-v15-v11.json")])
    print("Remaining live acceptance gates passed; final review and authorized CI publication remain", flush=True)


if __name__ == "__main__":
    main()
