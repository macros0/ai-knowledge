"""One full live HTTP matrix on the private Windows Docker stand."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import threading
import time

from run_diagnostics_aba_pve import EXPECTED
from run_diagnostics_aba_windows import IMAGE, PROJECT, execute, inspect, windows_cpu_sample


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--corpus-size", type=int, choices=(1, 100), required=True)
    parser.add_argument("--scenario", choices=("normal", "active", "stopped"), required=True)
    parser.add_argument("--attempt", type=int, choices=(1, 2), default=1)
    parser.add_argument("--windows-process-metrics", action="store_true")
    args = parser.parse_args()
    root, repo = args.root.resolve(), args.repo.resolve()
    stage = f"http-{args.corpus_size}doc-{args.scenario}-windows-v15-v11"
    if args.attempt != 1:
        stage += f"-r{args.attempt}"
    if {role: inspect(role, "Image") for role in EXPECTED} != EXPECTED:
        raise RuntimeError("Frozen live images changed")
    pids = {role: int(inspect(role, "State.Pid")) for role in EXPECTED}
    private = ["docker", "run", "--rm", "--network", PROJECT + "_app",
        "--pid", "host", "--cgroupns", "host", "-e", "PYTHONPATH=/app:/work/backend",
        "-v", str(repo / "backend/test_scripts") + ":/app/test_scripts:ro",
        "-v", PROJECT + "_probe:/probe",
        "-v", PROJECT + "_backend_spool:/diag/backend:ro",
        "-v", PROJECT + "_frontend_spool:/diag/frontend:ro",
        "--workdir", "/app", "--entrypoint", "python", IMAGE]
    execute(private + ["-c", "from pathlib import Path; Path('/probe/" + stage + "-metrics').mkdir()"])
    monitor = subprocess.Popen(private + ["/app/test_scripts/collect_diagnostics_numeric_metrics.py",
        "--backend-pid", str(pids["backend"]), "--frontend-pid", str(pids["frontend"]),
        "--root", "/probe/" + stage + "-metrics"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1)
    if monitor.poll() is not None:
        raise RuntimeError("Numeric sampler failed before live matrix")
    done = threading.Event()

    def sample_windows():
        with (root / (stage + "-windows-cpu.jsonl")).open("x") as handle:
            while not done.is_set():
                handle.write(json.dumps(windows_cpu_sample()) + "\n")
                handle.flush()
                done.wait(.25)

    watcher = threading.Thread(target=sample_windows, daemon=True)
    watcher.start()
    process_monitor = None
    process_stop = root / (stage + "-process-metrics.stop")
    if args.windows_process_metrics:
        process_monitor = subprocess.Popen(["powershell.exe", "-NoProfile", "-File",
            str(repo / "backend/test_scripts/collect_diagnostics_windows_processes.ps1"),
            "-OutputPath", str(root / (stage + "-windows-processes.jsonl")), "-StopPath", str(process_stop)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    probe_cmd = private.copy()
    marker = probe_cmd.index("--entrypoint")
    probe_cmd[marker:marker] = ["--name", PROJECT + "-live-matrix"]
    probe_args = ["/app/test_scripts/probe_diagnostics_http_matrix.py", "--base-url", "http://frontend:3000",
        "--corpus-size", str(args.corpus_size), "--output", "/probe/" + stage + ".json",
        "--spool-root", "/diag", "--repeats", "3", "--project", PROJECT]
    if args.attempt != 1:
        probe_args.append("--retain-timelines")
    if args.scenario != "normal":
        probe_args += ["--with-bundle", "--admin-prefix", "diag.bench" if args.scenario == "active" else "diag.stop"]
    if args.scenario == "stopped":
        probe_args.append("--stopped-bundle")
    wrapper = """import json,sys
from pathlib import Path
sys.path.insert(0,'/app/test_scripts')
import probe_diagnostics_http_matrix as probe
from run_diagnostics_final_ct import validate_matrix
config=json.loads(sys.argv[1]);sys.argv=config['args']
probe._container_pid=lambda name: config['pids'][name.rsplit('-',2)[-2]]
probe._image_ids=lambda containers: config['images']
for pid in config['pids'].values():
 if not Path('/proc/'+str(pid)+'/status').is_file():raise RuntimeError('Live PID unavailable')
probe.main()
target=Path(sys.argv[sys.argv.index('--output')+1])
gates=validate_matrix(target,config['corpus'],bundle=config['scenario']!='normal',stopped=config['scenario']=='stopped')
print(json.dumps({'stage_gates':gates}),flush=True)
"""
    config = {"args": probe_args, "pids": pids, "images": EXPECTED,
              "corpus": args.corpus_size, "scenario": args.scenario}
    started = datetime.now(timezone.utc).isoformat()
    code = None
    try:
        code = subprocess.run(probe_cmd + ["-c", wrapper, json.dumps(config)],
                              timeout=2500 if args.scenario == "active" else 1900).returncode
    finally:
        if code is None:
            subprocess.run(["docker", "rm", "-f", PROJECT + "-live-matrix"], check=False, capture_output=True)
        done.set()
        watcher.join(timeout=2)
        process_error = b""
        if process_monitor is not None:
            process_stop.touch()
            _, process_error = process_monitor.communicate(timeout=15)
        execute(private + ["-c", "from pathlib import Path; Path('/probe/" + stage + "-metrics/metrics.stop').touch()"])
        _, monitor_err = monitor.communicate(timeout=15)
        images = {role: inspect(role, "Image") for role in EXPECTED}
        status = {"stage": stage, "started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
            "probe_exit_code": code, "monitor_exit_code": monitor.returncode,
            "monitor_error_bytes": len(monitor_err), "images": images,
            "process_monitor_exit_code": process_monitor.returncode if process_monitor is not None else None,
            "process_monitor_error_bytes": len(process_error), "attempt": args.attempt,
            "stand": "Windows / Docker Desktop Linux VM", "acceptance_thresholds_unchanged": True}
        (root / (stage + "-status.json")).write_text(json.dumps(status, indent=2) + "\n")
        print(json.dumps(status), flush=True)
        archive_script = """import tarfile,sys
from pathlib import Path
p=Path('/probe');stage=sys.argv[1]
with tarfile.open(p/(stage+'-safe-results.tar'),'w') as t:
 for name in [stage+'.json',stage+'-analysis.json',stage+'-metrics/linux-vm-metrics.jsonl']:
  if (p/name).exists():t.add(p/name,arcname=Path(name).name)
"""
        execute(private + ["-c", archive_script, stage])
        copy_container = PROJECT + "-live-evidence-copy"
        execute(["docker", "create", "--name", copy_container, "-v", PROJECT + "_probe:/probe", IMAGE],
                stdout=subprocess.DEVNULL)
        try:
            execute(["docker", "cp", copy_container + ":/probe/" + stage + "-safe-results.tar",
                     str(root / (stage + "-safe-results.tar"))])
        finally:
            execute(["docker", "rm", copy_container], stdout=subprocess.DEVNULL)
    if (code != 0 or monitor.returncode != 0 or images != EXPECTED
            or (process_monitor is not None and process_monitor.returncode != 0)):
        raise RuntimeError("Live matrix failed; evidence preserved, no later stage started")


if __name__ == "__main__":
    main()
