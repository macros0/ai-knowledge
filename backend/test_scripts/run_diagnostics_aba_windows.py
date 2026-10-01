"""Same frozen Linux app images on local Windows Docker Desktop; synthetic only."""
import argparse
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import subprocess
import threading
import time

from run_diagnostics_aba_pve import EXPECTED

PROJECT = "okf-diag-win-aba-20260930"
IMAGE = "okf-diag-levels-backend:20260930-v15"
FRONTEND = "okf-diag-levels-frontend:20260930-v11"


def execute(command, **options):
    return subprocess.run(command, check=True, **options)


def inspect(role, field):
    return subprocess.check_output(["docker", "inspect", "--format", "{{." + field + "}}",
                                    PROJECT + "-" + role + "-1"], text=True).strip()


class FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]


def windows_cpu_sample():
    values = [FileTime(), FileTime(), FileTime()]
    if not ctypes.windll.kernel32.GetSystemTimes(*(ctypes.byref(value) for value in values)):
        raise ctypes.WinError()
    return {"at_unix": time.time(), "at_monotonic": time.perf_counter(),
            **{key: (value.high << 32) | value.low for key, value in
               zip(("idle_100ns", "kernel_100ns", "user_100ns"), values)}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    root, repo = args.root.resolve(), args.repo.resolve()
    envfile = root / "runtime.env"
    if envfile.exists():
        raise FileExistsError("Preserve the existing Windows experiment")
    password = secrets.token_hex(24)
    config = {
        "STORAGE_MODE": "bundled", "ENVIRONMENT": "development", "POSTGRES_PASSWORD": password,
        "DATABASE_URL": f"postgresql+psycopg://okf:{password}@postgres:5432/okf_knowledge",
        "QDRANT_URL": "http://qdrant:6333", "QDRANT_COLLECTION": "okf_diag_levels_synthetic",
        "AUTH_PROVIDER": "simulation", "APP_SECRET_KEY": secrets.token_hex(32),
        "AUTH_SESSION_HTTPS_ONLY": "false", "EMBEDDING_PROVIDER": "fake",
        "AUTH_SIM_USERS": json.dumps([{"user_id": "sim-admin", "username": "demo.admin",
            "email": "admin@demo.local", "groups": ["KB_Admin"]}], separators=(",", ":")),
        "SEARCH_RATE_LIMIT_PER_MINUTE": "10000", "LLM_API_KEY": "test-unused",
        "DIAGNOSTICS_BASELINE_ENABLED": "true", "DIAGNOSTICS_CAPTURE_ENABLED": "true",
        "DIAGNOSTICS_BUNDLE_ENABLED": "true", "DIAGNOSTICS_DOWNLOAD_ENABLED": "true",
        "DIAGNOSTICS_MIN_FREE_MB": "0", "DIAGNOSTICS_TRACE_LIMIT_PER_SECOND": "1",
        "DIAGNOSTICS_SUCCESS_LIMIT_PER_SECOND": "10", "DATA_DIR": "/data",
        "DIAGNOSTICS_DIR": "/diagnostics/backend",
        "OKF_FRONTEND_DIAGNOSTICS_DIR": "/diagnostics/frontend",
        "OKF_BUILD_REVISION": "948e78990ea3d2efc033273328c487e9a5960fa"}
    with envfile.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(f"{key}={value}" for key, value in config.items()) + "\n")
    logging = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
    backend_volumes = ["data:/data", "backend_spool:/diagnostics/backend",
                       "frontend_spool:/diagnostics/frontend:ro"]
    compose = {"services": {
        "postgres": {"image": "postgres:17@sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675",
            "environment": {"POSTGRES_USER": "okf", "POSTGRES_DB": "okf_knowledge", "POSTGRES_PASSWORD": "${POSTGRES_PASSWORD}"},
            "volumes": ["postgres_data:/var/lib/postgresql/data"], "networks": ["storage"], "logging": logging,
            "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U okf -d okf_knowledge"], "interval": "2s", "retries": 30}},
        "qdrant": {"image": "qdrant/qdrant:v1.19.0@sha256:057ee3a8da769fe7310dd3537b4dc7583bf87a95ce8ac43c0af5a46bc580d1fc",
            "volumes": ["qdrant_data:/qdrant/storage"], "networks": ["storage"], "logging": logging},
        "migrate": {"image": IMAGE, "env_file": [str(envfile)], "volumes": ["data:/data"],
            "networks": ["storage"], "logging": logging,
            "command": ["sh", "-ceu", "python scripts/validate_storage_mode.py && python scripts/wait_for_qdrant.py --url http://qdrant:6333 --timeout-seconds 60 && alembic upgrade head"],
            "depends_on": {"postgres": {"condition": "service_healthy"}, "qdrant": {"condition": "service_started"}}},
        "backend": {"image": IMAGE, "env_file": [str(envfile)], "volumes": backend_volumes,
            "networks": ["app", "storage"], "logging": logging,
            "depends_on": {"migrate": {"condition": "service_completed_successfully"}},
            "healthcheck": {"test": ["CMD-SHELL", "python -c 'import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8000/health/ready\", timeout=5)'"],
                "interval": "3s", "timeout": "6s", "retries": 40, "start_period": "10s"}},
        "frontend": {"image": FRONTEND,
            "environment": {"BACKEND_URL": "http://backend:8000", "OKF_FRONTEND_DIAGNOSTICS_DIR": "/diagnostics/frontend",
                "OKF_DIAGNOSTICS_CONTROL_PATH": "/diagnostics/backend/control/capture.json",
                "OKF_BUILD_REVISION": config["OKF_BUILD_REVISION"]},
            "volumes": ["frontend_spool:/diagnostics/frontend", "backend_spool:/diagnostics/backend:ro"],
            "networks": ["app"], "logging": logging, "ports": ["127.0.0.1:18484:3000"],
            "depends_on": {"backend": {"condition": "service_healthy"}}}},
        "networks": {"app": {}, "storage": {"internal": True}},
        "volumes": {name: {} for name in ("data", "postgres_data", "qdrant_data", "backend_spool", "frontend_spool", "probe")}}
    composefile = root / "compose.json"
    composefile.write_text(json.dumps(compose, indent=2) + "\n")
    base = ["docker", "compose", "--env-file", str(envfile), "-p", PROJECT, "-f", str(composefile)]
    # Allocate only this project's volumes; align fresh spool permissions before app startup.
    execute(base + ["create", "frontend"], stdout=subprocess.DEVNULL)
    execute(["docker", "run", "--rm", "-v", PROJECT + "_backend_spool:/diagnostics/backend",
             "-v", PROJECT + "_frontend_spool:/diagnostics/frontend", "--entrypoint", "chown", IMAGE,
             "1000:1000", "/diagnostics/backend", "/diagnostics/frontend"])
    execute(base + ["up", "-d", "--wait", "--wait-timeout", "180"])
    if {role: inspect(role, "Image") for role in EXPECTED} != EXPECTED:
        raise RuntimeError("Frozen app images differ")
    seed = (repo / "backend/test_scripts/seed_diagnostics_performance.py").read_text()
    execute(["docker", "exec", "-i", PROJECT + "-backend-1", "python", "-c",
             "import sys; __file__='/app/test_scripts/seed_diagnostics_performance.py'; sys.argv=[__file__,'--count','1']; exec(compile(sys.stdin.read(),__file__,'exec'))"],
            input=seed, text=True)
    # Preflight Linux volume and frontend connectivity before the bounded workload.
    private = ["docker", "run", "--rm", "--network", PROJECT + "_app",
        "-e", "PYTHONPATH=/app:/work/backend", "-v", str(repo / "backend/test_scripts") + ":/app/test_scripts:ro",
        "-v", str(repo / "backend/tests") + ":/work/backend/tests:ro",
        "-v", str(repo / "tests/fixtures") + ":/work/tests/fixtures:ro",
        "-v", PROJECT + "_probe:/probe", "--workdir", "/work/backend", "--entrypoint", "python", IMAGE]
    execute(private + ["-c", "import sys; sys.path.insert(0,'/app/test_scripts'); from probe_diagnostics_http import Client; c=Client('http://frontend:3000'); c.login(); s=c.call('GET','/api/admin/diagnostics/status'); assert s['runtime']['available'] and s['session']['session'] is None; print('preflight: ready, capture off')"])
    monitor_command = private.copy()
    marker = monitor_command.index("--entrypoint")
    monitor_command[marker:marker] = ["--pid", "host", "--cgroupns", "host"]
    monitor = subprocess.Popen(monitor_command + ["/app/test_scripts/collect_diagnostics_numeric_metrics.py",
        "--backend-pid", inspect("backend", "State.Pid"), "--frontend-pid", inspect("frontend", "State.Pid"),
        "--root", "/probe"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1)
    if monitor.poll() is not None:
        monitor_out, monitor_err = monitor.communicate()
        print("monitor startup error type/count:", monitor.returncode, len(monitor_err), flush=True)
        raise RuntimeError("Linux VM metrics sampler failed before measurements")
    done = threading.Event()
    def sample_windows():
        with (root / "windows-cpu-metrics.jsonl").open("x") as handle:
            while not done.is_set():
                handle.write(json.dumps(windows_cpu_sample()) + "\n")
                handle.flush()
                done.wait(.25)
    watcher = threading.Thread(target=sample_windows, daemon=True)
    watcher.start()
    started = datetime.now(timezone.utc).isoformat()
    code = None
    try:
        probe_command = private.copy()
        probe_command[probe_command.index("--entrypoint"):probe_command.index("--entrypoint")] = [
            "--name", PROJECT + "-private-probe"]
        result = subprocess.run(probe_command + ["/app/test_scripts/probe_diagnostics_fixed_input.py",
            "--root", "/probe/aba-input", "--output", "/probe/aba-windows.json", "--base-url", "http://frontend:3000",
            "--records", "20000", "--repeats", "1", "--concurrency", "1", "--diagnostic-aba"], timeout=1250)
        code = result.returncode
    finally:
        if code is None:
            subprocess.run(["docker", "rm", "-f", PROJECT + "-private-probe"], capture_output=True, check=False)
        done.set()
        watcher.join(timeout=2)
        execute(private + ["-c", "from pathlib import Path; Path('/probe/metrics.stop').touch()"])
        monitor_out, monitor_err = monitor.communicate(timeout=15)
        images = {role: inspect(role, "Image") for role in EXPECTED}
        status = {"started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
                  "probe_exit_code": code, "monitor_exit_code": monitor.returncode,
                  "monitor_error_bytes": len(monitor_err), "images": images, "diagnostic_only": True,
                  "platform": "Windows host / local Docker Desktop Linux VM; named Linux volumes",
                  "windows_host_cpu_sample_seconds": .25, "linux_metrics_sample_seconds": .25,
                  "frontend_local_url": "http://127.0.0.1:18484"}
        (root / "status.json").write_text(json.dumps(status, indent=2) + "\n")
        print(json.dumps(status), flush=True)
        execute(private + ["-c", "import tarfile; from pathlib import Path; p=Path('/probe'); t=tarfile.open('/probe/safe-results.tar','w'); [t.add(p/name,arcname=name) for name in ('aba-windows.json','linux-vm-metrics.jsonl') if (p/name).exists()]; t.close()"])
        copy_container = PROJECT + "-evidence-copy"
        execute(["docker", "create", "--name", copy_container, "-v", PROJECT + "_probe:/probe", IMAGE], stdout=subprocess.DEVNULL)
        try:
            execute(["docker", "cp", copy_container + ":/probe/safe-results.tar", str(root / "safe-results.tar")])
        finally:
            execute(["docker", "rm", copy_container], stdout=subprocess.DEVNULL)
    if code != 0 or monitor.returncode != 0 or images != EXPECTED:
        raise RuntimeError("Windows bounded diagnosis failed; inspect preserved evidence")


if __name__ == "__main__":
    main()
