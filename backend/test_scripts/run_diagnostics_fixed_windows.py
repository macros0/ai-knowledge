"""One mandatory immutable-input block on the user-selected Windows stand."""
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
    parser.add_argument("--original-app", type=Path)
    args = parser.parse_args()
    root, repo = args.root.resolve(), args.repo.resolve()
    reference = args.original_app is not None
    stage = ("original" if reference else "current") + "-fixed-input-windows-v15-v11"
    if {role: inspect(role, "Image") for role in EXPECTED} != EXPECTED:
        raise RuntimeError("Frozen live images changed")
    private = ["docker", "run", "--rm", "--network", PROJECT + "_app",
        "-e", "PYTHONPATH=/app:/work/backend", "-v", str(repo / "backend/test_scripts") + ":/app/test_scripts:ro",
        "-v", str(repo / "backend/tests") + ":/work/backend/tests:ro",
        "-v", str(repo / "tests/fixtures") + ":/work/tests/fixtures:ro",
        "-v", PROJECT + "_probe:/probe", "--workdir", "/work/backend", "--entrypoint", "python", IMAGE]
    execute(private + ["-c", "from pathlib import Path; p=Path('/probe/" + stage + "-metrics'); p.mkdir()"])
    monitor_cmd = private.copy()
    marker = monitor_cmd.index("--entrypoint")
    monitor_cmd[marker:marker] = ["--pid", "host", "--cgroupns", "host"]
    monitor = subprocess.Popen(monitor_cmd + ["/app/test_scripts/collect_diagnostics_numeric_metrics.py",
        "--backend-pid", inspect("backend", "State.Pid"), "--frontend-pid", inspect("frontend", "State.Pid"),
        "--root", "/probe/" + stage + "-metrics"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1)
    if monitor.poll() is not None:
        raise RuntimeError("Metrics sampler failed before fixed-input workload")
    done = threading.Event()
    def sample_windows():
        with (root / (stage + "-windows-cpu.jsonl")).open("x") as handle:
            while not done.is_set():
                handle.write(json.dumps(windows_cpu_sample()) + "\n")
                handle.flush()
                done.wait(.25)
    watcher = threading.Thread(target=sample_windows, daemon=True)
    watcher.start()
    probe_cmd = private.copy()
    marker = probe_cmd.index("--entrypoint")
    probe_cmd[marker:marker] = ["--name", PROJECT + "-full-fixed"]
    if reference:
        marker = probe_cmd.index("--entrypoint")
        probe_cmd[marker:marker] = ["-v", str(args.original_app.resolve()) + ":/app/app:ro",
                                   "-e", "OKF_BUILD_REVISION=original-2614bfa"]
    probe_args = ["/app/test_scripts/probe_diagnostics_fixed_input.py", "--root", "/probe/" + stage,
        "--output", "/probe/" + stage + ".json", "--records", "20000", "--repeats", "3",
        "--reference-version", "original" if reference else "current", "--base-url", "http://frontend:3000",
        "--retain-timelines"]
    wrapper = """import json,runpy,sys
from pathlib import Path
sys.path.insert(0,'/app/test_scripts')
sys.argv=json.loads(sys.argv[1])
runpy.run_path(sys.argv[0],run_name='__main__')
from analyze_diagnostics_controls import analyze_fixed
target=Path(sys.argv[sys.argv.index('--output')+1])
data=json.loads(target.read_text())
result=analyze_fixed(data,records=20000,repeats=3,concurrencies=[1,8],smoke=False,
                     reference=sys.argv[sys.argv.index('--reference-version')+1]=='original')
target.with_name(target.stem+'-analysis.json').write_text(json.dumps(result,indent=2)+'\\n')
gates={key:value for key,value in result.items() if key.startswith('all_')}
print(json.dumps({'stage_gates':gates}),flush=True)
if any(value is not True for value in gates.values()):raise RuntimeError('Numerical gate failed; block stopped')
"""
    started = datetime.now(timezone.utc).isoformat()
    code = None
    try:
        code = subprocess.run(probe_cmd + ["-c", wrapper, json.dumps(probe_args)],
                              timeout=2400 if not reference else 1250).returncode
    finally:
        if code is None:
            subprocess.run(["docker", "rm", "-f", PROJECT + "-full-fixed"], check=False, capture_output=True)
        done.set()
        watcher.join(timeout=2)
        execute(private + ["-c", "from pathlib import Path; Path('/probe/" + stage + "-metrics/metrics.stop').touch()"])
        monitor_out, monitor_err = monitor.communicate(timeout=15)
        images = {role: inspect(role, "Image") for role in EXPECTED}
        status = {"stage": stage, "started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
            "probe_exit_code": code, "monitor_exit_code": monitor.returncode, "monitor_error_bytes": len(monitor_err),
            "images": images, "stand": "Windows / Docker Desktop Linux VM", "reference": reference,
            "acceptance_thresholds_unchanged": True}
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
        copy_container = PROJECT + "-full-evidence-copy"
        execute(["docker", "create", "--name", copy_container, "-v", PROJECT + "_probe:/probe", IMAGE],
                stdout=subprocess.DEVNULL)
        try:
            execute(["docker", "cp", copy_container + ":/probe/" + stage + "-safe-results.tar",
                     str(root / (stage + "-safe-results.tar"))])
        finally:
            execute(["docker", "rm", copy_container], stdout=subprocess.DEVNULL)
    if code != 0 or monitor.returncode != 0 or images != EXPECTED:
        raise RuntimeError("Windows fixed-input gate failed; artifacts preserved, no later stage started")


if __name__ == "__main__":
    main()
