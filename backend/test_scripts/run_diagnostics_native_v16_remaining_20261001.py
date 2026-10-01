"""Remaining native live evidence; serial stages and unchanged numerical gates."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path("/opt/okf-diag-levels-20260929")
PROJECT = "okf-diag-levels-20260929"
EXPECTED = {
    "backend": "sha256:0a805ae89c3d42360981e170d845d150445912bb277946a52d5b75d9506de388",
    "frontend": "sha256:a37ae1505d48528941a849e8ca7c07e568e6c453bdfa59e52626d5fba35b4799",
}
sys.path.insert(0, str(ROOT / "backend/test_scripts"))
from probe_diagnostics_http_matrix import _containers, _image_ids, _container_pid
from probe_diagnostics_http import Client
from run_diagnostics_final_ct import validate_matrix, MatrixGateError

STATE = ROOT / "native-v16-remaining-live-20261001-state.json"
history = []
ORIGINAL_COMMAND = ["node", "diagnostics-runner.mjs"]
PERFORMANCE_GATES = {"all_p95_pass", "all_rss_pass"}
POLICY = "User selected remaining acceptance without new optimizations; preserve failed p95/RSS"


def frozen():
    if _image_ids(_containers(PROJECT)) != EXPECTED:
        raise RuntimeError("Frozen images changed")


def save():
    STATE.write_text(json.dumps({"images": EXPECTED, "thresholds_unchanged": True,
                                 "history": history, "runtime_command": ORIGINAL_COMMAND,
                                 "authorization": POLICY, "complete": False}, indent=2) + "\n")


def run(stage, command, validate=None, *, env=None, input_text=None, performance_output=None):
    frozen()
    row = {"stage": stage, "status": "running", "started_utc": datetime.now(timezone.utc).isoformat()}
    history.append(row)
    save()
    print(json.dumps(row), flush=True)
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, input=input_text,
                                text=input_text is not None, check=False)
        row["exit_code"] = result.returncode
        if result.returncode:
            raise RuntimeError("Stage command failed")
        frozen()
        row["gates"] = validate() if validate else {}
        row["status"] = "passed"
    except MatrixGateError as exc:
        row.update(status="failed", failure_type=type(exc).__name__, failed_gates=exc.failed_gates)
        if performance_output is None or not exc.failed_gates or not set(exc.failed_gates) <= PERFORMANCE_GATES:
            raise
        analysis_path = performance_output.with_name(performance_output.stem + "-analysis.json")
        analysis = json.loads(analysis_path.read_text())
        row["gates"] = {key: value for key, value in analysis.items() if key.startswith("all_")}
        row["continuation"] = "authorized: completed matrix with performance failures only"
    except BaseException as exc:
        row.update(status="failed", failure_type=type(exc).__name__,
                   failed_gates=getattr(exc, "failed_gates", []))
        raise
    finally:
        row["finished_utc"] = datetime.now(timezone.utc).isoformat()
        save()
        print(json.dumps(row), flush=True)


def matrix(corpus, scenario, spool):
    current = json.loads(subprocess.check_output(["docker", "inspect", _containers(PROJECT)["frontend"]]))[0]
    if current["Config"]["Cmd"] != ORIGINAL_COMMAND:
        raise RuntimeError("Measured runtime command drifted")
    stage = f"http-{corpus}doc-{scenario}-pve-v16-v11-final-20261001"
    output = ROOT / (stage + ".json")
    if output.exists():
        raise FileExistsError("Preserve previous matrix evidence")
    command = ["python3", str(ROOT / "backend/test_scripts/probe_diagnostics_http_matrix.py"),
               "--base-url", "http://127.0.0.1:18084", "--corpus-size", str(corpus),
               "--output", str(output), "--spool-root", str(spool), "--repeats", "3",
               "--warmup-seconds", "30", "--measurement-seconds", "60", "--min-requests", "1000"]
    if scenario != "normal":
        command += ["--with-bundle", "--bundle-poll-interval", "3", "--admin-prefix", "diag.bench"]
    if scenario == "stopped":
        command += ["--stopped-bundle", "--admin-prefix", "diag.stop"]
    def validate():
        if json.loads(output.read_text())["image_ids"] != EXPECTED:
            raise RuntimeError("Matrix image mismatch")
        return validate_matrix(output, corpus, bundle=scenario != "normal", stopped=scenario == "stopped")
    run(stage, command, validate, performance_output=output)


def main():
    if STATE.exists():
        raise FileExistsError("Inspect existing ledger; never overwrite or resume silently")
    frozen()
    for role, container in _containers(PROJECT).items():
        current = json.loads(subprocess.check_output(["docker", "inspect", container]))[0]
        labels = current["Config"]["Labels"]
        if labels["com.docker.compose.project"] != PROJECT:
            raise RuntimeError("Wrong Compose project")
        envfile = ROOT / "runtime-1doc-bench.env"
        if labels["com.docker.compose.project.environment_file"] != str(envfile):
            raise RuntimeError("Different selected runtime env")
        if "DIAGNOSTICS_BASELINE_ENABLED=true" not in current["Config"]["Env"]:
            raise RuntimeError("Baseline must be enabled in both components")
        if role == "frontend" and current["Config"]["Cmd"] != ORIGINAL_COMMAND:
            raise RuntimeError("Original Node command must already be restored")
        if any(m["Destination"].startswith("/app") for m in current["Mounts"]):
            raise RuntimeError("Unexpected runtime source mount")
        for filename in labels["com.docker.compose.project.config_files"].split(","):
            if not Path(filename).resolve().is_relative_to(ROOT.resolve()):
                raise ValueError("Compose file outside disposable root")
    save()
    import time
    for attempt in range(80):
        try:
            client = Client("http://127.0.0.1:18084")
            client.login()
            break
        except Exception:
            if attempt == 79:
                raise
            time.sleep(0.5)
    if client.call("GET", "/api/admin/diagnostics/status")["session"]["session"] is not None:
        raise RuntimeError("Capture active before queue")
    matrix(1, "normal", ROOT / "diagnostics-1doc")
    matrix(1, "active", ROOT / "diagnostics-1doc")
    # Fresh synthetic 100-document storage preserves every older data volume.
    override = ROOT / "compose-native-v16-100-20261001.json"
    data_path = ROOT / "data-native-v16-100-20261001"
    data = str(data_path)
    spool = ROOT / "diagnostics-native-v16-100-20261001"
    # Match actual runtime owners; create only fresh test directories.
    owners = {}
    for role, name in _containers(PROJECT).items():
        status = Path(f"/proc/{_container_pid(name)}/status").read_text().splitlines()
        owners[role] = tuple(int(next(x.split()[1] for x in status if x.startswith(key)))
                             for key in ("Uid:", "Gid:"))
    for directory, role in ((data_path, "backend"), (spool, None),
                            (spool / "backend", "backend"),
                            (spool / "backend/control", "backend"),
                            (spool / "frontend", "frontend")):
        if not directory.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError("Fresh directory outside disposable root")
        directory.mkdir(mode=0o700)
        if role:
            os.chown(directory, *owners[role])
    services = {
        "backend": {"image": "okf-diag-levels-backend:20261001-v16", "volumes": [
            f"{data}:/data", f"{spool}/backend:/diagnostics/backend", f"{spool}/frontend:/diagnostics/frontend:ro"]},
        "frontend": {"image": "okf-diag-levels-frontend:20260930-v11",
            "command": ORIGINAL_COMMAND, "volumes": [
            f"{spool}/frontend:/diagnostics/frontend", f"{spool}/backend/control:/diagnostics/control:ro"]},
        "migrate": {"image": "okf-diag-levels-backend:20261001-v16", "volumes": [f"{data}:/data"]},
    }
    if override.exists():
        raise FileExistsError("100-doc override already exists")
    override.write_text(json.dumps({"services": services, "volumes": {
        "postgres_data": {"name": "okf-diag-native-v16-100-postgres-20261001"},
        "qdrant_data": {"name": "okf-diag-native-v16-100-qdrant-20261001"}}}, indent=2))
    envfile = ROOT / "runtime-1doc-bench.env"
    env = {**os.environ, "OKF_RUNTIME_ENV_FILE": str(envfile)}
    compose = ["docker", "compose", "--env-file", str(envfile), "-p", PROJECT,
               "-f", "docker-compose.yml", "-f", "compose-override.yml",
               "-f", "compose-heartbeat-v15-v11.yml", "-f", str(override)]
    run("switch-fresh-100doc-v16-20261001", compose + ["up", "-d", "--wait", "--wait-timeout", "180"], env=env)
    seed = (ROOT / "backend/test_scripts/seed_diagnostics_performance.py").read_text()
    run("seed-synthetic-100doc-v16-20261001", ["docker", "exec", "-i", _containers(PROJECT)["backend"],
        "python", "-c", "import sys; __file__='/app/test_scripts/seed_diagnostics_performance.py'; "
        "sys.argv=[__file__,'--count','100']; exec(compile(sys.stdin.read(),__file__,'exec'))"], input_text=seed)
    check = "from app.db.session import session_scope; from app.db.models import Document; " \
            "from sqlalchemy import select; " \
            "dbctx=session_scope(); db=dbctx.__enter__(); ids=set(db.scalars(select(Document.id))); " \
            "assert ids=={f'diagperf{i:08x}' for i in range(100)}; dbctx.__exit__(None,None,None); " \
            "print('verified=100 synthetic_docs_only=true')"
    run("verify-synthetic-100doc-v16-20261001", ["docker", "exec", _containers(PROJECT)["backend"], "python", "-c", check])
    for scenario in ("normal", "active", "stopped"):
        matrix(100, scenario, spool)
    frozen()
    client = Client("http://127.0.0.1:18084")
    client.login()
    status = client.call("GET", "/api/admin/diagnostics/status")
    if status["session"]["session"] is not None:
        raise RuntimeError("Capture active after queue")
    final = json.loads(STATE.read_text())
    final.update(complete=True, acceptance="failed" if any(r["status"] == "failed" for r in history) else "passed",
                 known_prior_performance_failures_preserved=True, capture_off=True,
                 finished_utc=datetime.now(timezone.utc).isoformat())
    STATE.write_text(json.dumps(final, indent=2) + "\n")
    print(json.dumps({"complete": True, "acceptance": final["acceptance"],
                      "known_prior_performance_failures_preserved": True}), flush=True)


if __name__ == "__main__":
    main()
