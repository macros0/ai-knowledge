"""Remaining original-version, baseline-off and outcome controls on CT102."""
from datetime import datetime, timezone
import json
import time

if __package__:
    from . import run_diagnostics_final_ct as queue
else:
    import run_diagnostics_final_ct as queue

ROOT = queue.ROOT


def original_python(arguments):
    command = queue.private_python(arguments)
    marker = command.index("--entrypoint")
    command[marker:marker] = ["-v", f"{ROOT}/original-2614bfa/backend/app:/app/app:ro",
                              "-e", "OKF_BUILD_REVISION=original-2614bfa"]
    return command


def compose(env):
    return ["docker", "compose", "--env-file", env, "-p", queue.PROJECT,
            "-f", "docker-compose.yml", "-f", "compose-override.yml",
            "up", "-d", "--wait", "--wait-timeout", "180"]


if __name__ == "__main__":
    queue.STATE = ROOT / "final-acceptance-v13-tail-state.json"
    if queue.STATE.exists():
        raise FileExistsError("Existing tail ledger: inspect before retry")
    print("Waiting for main sequential queue; tail controls never overlap", flush=True)
    target = str(ROOT / "backend/test_scripts/run_diagnostics_final_ct.py").encode()
    while queue.matrix_running(target):
        time.sleep(5)
    ledger = json.loads((ROOT / "final-acceptance-v13-state.json").read_text())
    queue.require_complete_main_ledger(ledger)
    queue.run("original-settings-instrument-smoke", original_python([
        "/app/test_scripts/probe_diagnostics.py", "--root", "/probe/original-settings-smoke",
        "--output", "/probe/original-settings-smoke.json", "--scenario", "settings",
        "--warmup-seconds", "1", "--measurement-seconds", "1", "--requests", "200"]))
    queue.run("original-fixed-input-instrument-smoke", original_python([
        "/app/test_scripts/probe_diagnostics_fixed_input.py", "--root", "/probe/original-fixed-smoke",
        "--output", "/probe/original-fixed-smoke.json", "--records", "1000", "--repeats", "1",
        "--concurrency", "8", "--warmup-seconds", "2", "--measurement-seconds", "10",
        "--min-requests", "80", "--reference-version", "original"]))
    for scenario in ("settings", "search_stub"):
        for repeat in range(1, 4):
            for concurrency in (1, 8):
                stage = f"testclient-{scenario}-legacy-c{concurrency}-r{repeat}-2614bfa"
                queue.run(stage, original_python(["/app/test_scripts/probe_diagnostics.py",
                    "--root", f"/probe/{stage}", "--output", f"/probe/{stage}.json", "--scenario", scenario,
                    "--concurrency", str(concurrency), "--warmup-seconds", "30",
                    "--measurement-seconds", "60", "--requests", "1000"]))
    queue.run("fixed-input-original-full-2614bfa", original_python([
        "/app/test_scripts/probe_diagnostics_fixed_input.py", "--root", "/probe/original-fixed-final",
        "--output", "/probe/fixed-input-original-full-2614bfa.json", "--records", "20000",
        "--repeats", "3", "--reference-version", "original"]))
    contracts = ["tests/test_diagnostics_final_acceptance.py::test_canonical_generation_retry_only_repeats_failed_chunk",
        "tests/test_chat_stream.py::test_stream_delivers_deltas_then_authoritative_result",
        "tests/test_chat_stream.py::test_stream_failure_never_exposes_provider_text",
        "tests/test_chat_stream.py::test_stream_close_signals_cancellation",
        "tests/test_diagnostics_context.py::test_stream_error_after_200_has_request_id",
        "tests/test_generation_pipeline.py::test_success_publishes_new_generation_and_uses_distinct_attachment_paths",
        "tests/test_generation_pipeline.py::test_cancel_during_indexing_prevents_late_publication"]
    for mode in ("baseline", "standard", "detailed"):
        queue.run("native-outcomes-" + mode, queue.private_python(["-m", "pytest", "-q", "-p",
            "test_scripts.diagnostics_acceptance_plugin", "--diagnostics-mode", mode, *contracts,
            "--basetemp", "/tmp/diag-native-outcomes-" + mode]))
    queue.run("error-storm-final-v13", queue.private_python([
        "/app/test_scripts/probe_diagnostics_error_storm.py", "--root", "/probe/storm-final-v13",
        "--output", "/probe/error-storm-final-v13.json", "--seconds", "60", "--target-rate", "10000"]))
    # Only the disposable project's selected runtime env is copied. Contents
    # stay on CT102; no secrets are printed, committed or sent to artifacts.
    on_env = ROOT / "runtime-bench.env"
    off_env = ROOT / "runtime-baseline-off-final.env"
    lines = on_env.read_text().splitlines()
    keys = {"DIAGNOSTICS_BASELINE_ENABLED": "false", "OKF_RUNTIME_ENV_FILE": str(off_env)}
    lines = [f"{key}={keys.pop(key)}" if (key := line.split("=", 1)[0]) in keys else line for line in lines]
    lines.extend(f"{key}={value}" for key, value in keys.items())
    off_env.write_text("\n".join(lines) + "\n")
    off_env.chmod(0o600)
    from probe_diagnostics_http_matrix import measure, _image_ids, _containers
    controls = []
    try:
        for enabled in (True, False):
            if not enabled:
                queue.run("baseline-off-private-test-stack", compose(off_env.name))
            for concurrency in (1, 8):
                row = measure("http://127.0.0.1:18084", concurrency=concurrency,
                    warmup_seconds=30, measurement_seconds=60, min_requests=1000,
                    mode="baseline", repeat=1, corpus_size=100, spool_root=ROOT / "diagnostics-v4")
                row["baseline_enabled"] = enabled
                controls.append(row)
                (ROOT / "baseline-off-control-final-v13-v9.json").write_text(json.dumps({
                    "schema_version": 1, "scope": "instrumentation control, not SLA baseline replacement",
                    "image_ids": _image_ids(_containers(queue.PROJECT)), "rows": controls}, indent=2) + "\n")
                print(json.dumps({"stage": "baseline-off-control", "baseline_enabled": enabled,
                                  "concurrency": concurrency, "p95_ms": row["p95_ms"],
                                  "recorded_at": datetime.now(timezone.utc).isoformat()}), flush=True)
    finally:
        queue.run("restore-baseline-on-private-stack", compose(on_env.name))
    queue.run("lifecycle-final-v13-v9", ["python3", str(ROOT / "backend/test_scripts/probe_diagnostics_lifecycle.py"),
        "--output", str(ROOT / "lifecycle-final-v13-v9.json")])
