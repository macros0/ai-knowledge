"""Run the six required live matrices once, serially, on the private stand."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from prepare_diagnostics_bench_env import prepare
from run_diagnostics_aba_pve import EXPECTED
from run_diagnostics_aba_windows import PROJECT, execute, inspect
from run_diagnostics_final_ct import MATRIX_GATES


def export_safe(root, repo, stage):
    destination = repo / "tests/artifacts/diagnostics" / stage
    destination.mkdir()
    allowed = {stage + ".json", stage + "-analysis.json", "linux-vm-metrics.jsonl"}
    with tarfile.open(root / (stage + "-safe-results.tar")) as archive:
        for member in archive.getmembers():
            if member.name not in allowed or not member.isfile():
                raise ValueError("Unexpected evidence member")
            with archive.extractfile(member) as source, (destination / member.name).open("wb") as target:
                shutil.copyfileobj(source, target)
    for suffix in ("-status.json", "-windows-cpu.jsonl"):
        shutil.copyfile(root / (stage + suffix), destination / (stage + suffix))
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    root, repo = args.root.resolve(), args.repo.resolve()
    state = root / "live-windows-v15-v11-state.json"
    if state.exists():
        raise FileExistsError("Inspect the existing queue ledger before a retry")
    for version in ("current", "original"):
        stage = version + "-fixed-input-windows-v15-v11"
        with tarfile.open(root / (stage + "-safe-results.tar")) as archive:
            result = json.load(archive.extractfile(stage + "-analysis.json"))
        gates = {key: value for key, value in result.items() if key.startswith("all_")}
        status = json.loads((root / (stage + "-status.json")).read_text())
        if (len(gates) != 6 or any(value is not True for value in gates.values())
                or status.get("probe_exit_code") != 0 or status.get("monitor_exit_code") != 0
                or status.get("images") != EXPECTED):
            raise RuntimeError("Fixed-input controls must pass before live matrices")
    images = {role: inspect(role, "Image") for role in EXPECTED}
    if images != EXPECTED:
        raise RuntimeError("Frozen images changed")
    envfile = root / "runtime-live.env"
    prepare(root / "runtime.env", envfile)
    composefile = root / "compose.json"
    compose = json.loads(composefile.read_text())
    compose["services"]["backend"]["env_file"] = [str(envfile)]
    live_compose = root / "compose-live.json"
    live_compose.write_text(json.dumps(compose, indent=2) + "\n")
    execute(["docker", "compose", "--env-file", str(envfile), "-p", PROJECT,
        "-f", str(live_compose), "up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", "backend"])
    history = []

    def save():
        value = {"stand": "Windows / Docker Desktop Linux VM", "images": EXPECTED,
                 "thresholds_unchanged": True, "history": history}
        state.write_text(json.dumps(value, indent=2) + "\n")
        destination = repo / "tests/artifacts/diagnostics/live-windows-v15-v11-state.json"
        destination.write_text(json.dumps(value, indent=2) + "\n")

    for corpus in (1, 100):
        if corpus == 100:
            seed = (repo / "backend/test_scripts/seed_diagnostics_performance.py").read_text()
            execute(["docker", "exec", "-i", PROJECT + "-backend-1", "python", "-c",
                "import sys; __file__='/app/test_scripts/seed_diagnostics_performance.py'; "
                "sys.argv=[__file__,'--count','100']; exec(compile(sys.stdin.read(),__file__,'exec'))"],
                input=seed, text=True)
        for scenario in ("normal", "active", "stopped"):
            if {role: inspect(role, "Image") for role in EXPECTED} != EXPECTED:
                raise RuntimeError("Frozen images changed before live stage")
            stage = f"http-{corpus}doc-{scenario}-windows-v15-v11"
            record = {"stage": stage, "status": "running", "started_utc": datetime.now(timezone.utc).isoformat()}
            history.append(record)
            save()
            print(json.dumps(record), flush=True)
            code = subprocess.run([sys.executable, str(repo / "backend/test_scripts/run_diagnostics_http_windows.py"),
                "--root", str(root), "--repo", str(repo), "--corpus-size", str(corpus),
                "--scenario", scenario], check=False).returncode
            record["exit_code"] = code
            record["finished_utc"] = datetime.now(timezone.utc).isoformat()
            try:
                destination = export_safe(root, repo, stage)
                analysis = json.loads((destination / (stage + "-analysis.json")).read_text())
                record["gates"] = {key: value for key, value in analysis.items() if key.startswith("all_")}
                record["status"] = ("passed" if code == 0
                    and all(record["gates"].get(gate) is True for gate in MATRIX_GATES) else "failed")
            except (OSError, ValueError, tarfile.TarError) as exc:
                record.update(status="failed", export_error_type=type(exc).__name__)
            save()
            print(json.dumps(record), flush=True)
            if record["status"] != "passed":
                raise RuntimeError("Live numerical gate failed; no next stage started")
    print("Six live matrices passed; storm/baseline-off/final lifecycle remain required", flush=True)


if __name__ == "__main__":
    main()
