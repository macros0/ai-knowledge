"""Bounded CT102 cause diagnosis; numeric host metrics, unchanged app images."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import threading
import time

ROOT = "/opt/okf-diag-levels-20260929"
NAME = "aba-v15-v11-20260930"
STAGING = Path("/var/tmp/okf-diag-aba-20260930")
EXPECTED = {
    "backend": "sha256:2aad41d130c6d877b8bcfd98f3ed7381b4f4acaa2e6a0fe7fec5e0cfdd8bb023",
    "frontend": "sha256:a37ae1505d48528941a849e8ca7c07e568e6c453bdfa59e52626d5fba35b4799",
}


def system_sample():
    value = {"at_monotonic": time.perf_counter(), "at_unix": time.time()}
    ticks = [int(item) for item in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
    value.update(cpu_total_ticks=sum(ticks), cpu_idle_ticks=ticks[3] + ticks[4],
                 cpu_iowait_ticks=ticks[4], cpu_steal_ticks=ticks[7])
    for resource in ("cpu", "io", "memory"):
        try:
            for line in Path("/proc/pressure", resource).read_text().splitlines():
                kind, *fields = line.split()
                values = dict(field.split("=") for field in fields)
                value[resource + "_" + kind] = {
                    "avg10": float(values["avg10"]), "total_usec": int(values["total"])}
        except OSError:
            value[resource + "_unavailable"] = True
    value["disk_counters"] = {}
    for line in Path("/proc/diskstats").read_text().splitlines():
        fields = line.split()
        if fields[2].startswith(("loop", "ram")):
            continue
        value["disk_counters"][fields[0] + ":" + fields[1]] = {
            "sectors_read": int(fields[5]), "sectors_written": int(fields[9]),
            "io_ms": int(fields[12]), "weighted_io_ms": int(fields[13])}
    return value


CT_MONITOR = r'''
import json
from pathlib import Path
import subprocess
import time
root = Path("/opt/okf-diag-levels-20260929")
name = "aba-v15-v11-20260930"
pids = {role: int(subprocess.check_output(["docker", "inspect", "--format", "{{.State.Pid}}",
        "okf-diag-levels-20260929-" + role + "-1"], text=True)) for role in ("backend", "frontend")}
groups = {role: Path("/sys/fs/cgroup") / next(line.split("::", 1)[1].lstrip("/")
          for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines() if line.startswith("0::"))
          for role, pid in pids.items()}
with (root / (name + "-ct-metrics.jsonl")).open("x") as handle:
    while not (root / (name + ".stop")).exists():
        value = system_sample()
        value["containers"] = {}
        for role, group in groups.items():
            metrics = {}
            for line in (group / "cpu.stat").read_text().splitlines():
                key, count = line.split()
                metrics[key] = int(count)
            metrics["memory_current_bytes"] = int((group / "memory.current").read_text())
            metrics["io_counters"] = {}
            for line in (group / "io.stat").read_text().splitlines():
                device, *fields = line.split()
                metrics["io_counters"][device] = {key: int(count) for key, count in
                    (field.split("=") for field in fields)}
            value["containers"][role] = metrics
        handle.write(json.dumps(value, separators=(",", ":")) + "\n")
        handle.flush()
        time.sleep(.25)
'''


def ct_python(code):
    return ["pct", "exec", "102", "--", "python3", "-c", code]


def images():
    return {role: subprocess.check_output(["pct", "exec", "102", "--", "docker", "inspect",
        "--format", "{{.Image}}", "okf-diag-levels-20260929-" + role + "-1"], text=True).strip()
        for role in EXPECTED}


def main():
    if images() != EXPECTED:
        raise RuntimeError("Frozen app images changed")
    subprocess.run(["pct", "push", "102", str(STAGING / "probe.py"),
                    ROOT + "/backend/test_scripts/probe_diagnostics_fixed_input_aba.py"], check=True)
    # Same sampler code on PVE and CT. Both monotonic/unix anchors are persisted.
    import inspect
    sampler_source = inspect.getsource(system_sample)
    monitor_source = "from pathlib import Path\nimport time\n" + sampler_source + CT_MONITOR
    monitor = subprocess.Popen(ct_python(monitor_source), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1)
    if monitor.poll() is not None:
        raise RuntimeError("CT metrics sampler failed before workload; no measurements started")
    stop = threading.Event()
    def monitor_pve():
        with (STAGING / (NAME + "-pve-metrics.jsonl")).open("x") as handle:
            while not stop.is_set():
                handle.write(json.dumps(system_sample(), separators=(",", ":")) + "\n")
                handle.flush()
                stop.wait(.25)
    watcher = threading.Thread(target=monitor_pve, daemon=True)
    watcher.start()
    started = datetime.now(timezone.utc).isoformat()
    command = ["pct", "exec", "102", "--", "docker", "run", "--rm",
        "--name", "okf-diag-aba-private-v15",
        "--security-opt", "apparmor=unconfined", "--network", "host",
        "-e", "PYTHONPATH=/app:/work/backend",
        "-v", ROOT + "/backend/test_scripts:/app/test_scripts:ro",
        "-v", ROOT + "/backend/tests:/work/backend/tests:ro",
        "-v", ROOT + "/tests/fixtures:/work/tests/fixtures:ro",
        "-v", ROOT + "/fixed-probe:/probe", "--workdir", "/work/backend",
        "--entrypoint", "python", "okf-diag-levels-backend:20260930-v15",
        "/app/test_scripts/probe_diagnostics_fixed_input_aba.py",
        "--root", "/probe/" + NAME, "--output", "/probe/" + NAME + ".json",
        "--records", "20000", "--repeats", "1", "--concurrency", "1",
        "--warmup-seconds", "30", "--measurement-seconds", "60", "--min-requests", "1000",
        "--diagnostic-aba"]
    code = None
    try:
        result = subprocess.run(command, timeout=1250)
        code = result.returncode
    finally:
        if code is None:
            subprocess.run(["pct", "exec", "102", "--", "docker", "rm", "-f",
                            "okf-diag-aba-private-v15"], check=False, capture_output=True)
        stop.set()
        watcher.join(timeout=2)
        subprocess.run(ct_python("from pathlib import Path; Path('" + ROOT + "/" + NAME +
                                ".stop').touch()"), check=True)
        monitor_out, monitor_err = monitor.communicate(timeout=15)
        # Preserve safe exit type/count, never arbitrary command/error text.
        status = {"started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
                  "probe_exit_code": code, "ct_monitor_exit_code": monitor.returncode,
                  "ct_monitor_error_bytes": len(monitor_err), "images": images(),
                  "diagnostic_only": True}
        (STAGING / (NAME + "-status.json")).write_text(json.dumps(status, indent=2) + "\n")
        print(json.dumps(status), flush=True)
    if code != 0 or monitor.returncode != 0 or images() != EXPECTED:
        raise RuntimeError("Bounded diagnosis failed; inspect saved evidence")


if __name__ == "__main__":
    main()
