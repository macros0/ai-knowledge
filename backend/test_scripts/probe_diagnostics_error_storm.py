"""Paced synthetic recorder error storm in a disposable Linux container.

Uses a private quota-limited spool, unique request IDs, and no exception text.
Checks the live test stack's health and indexed search while the storm runs.
"""

import argparse
import json
from pathlib import Path
import threading
import time
import urllib.request
from uuid import uuid4

from probe_diagnostics_http import Client
from app.services.diagnostics.recorder import DiagnosticRecorder
from app.services.diagnostics.schema import DiagnosticContext, DiagnosticLimits, MIB
from app.services.diagnostics.store import DiagnosticStore


def _rss():
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return 0


def run(root, *, seconds, target_rate, health_url):
    limits = DiagnosticLimits(total_bytes=16 * MIB, backend_bytes=15 * MIB,
                              frontend_bytes=MIB, baseline_bytes=8 * MIB,
                              session_bytes=8 * MIB, segment_bytes=MIB,
                              bundle_bytes=8 * MIB, min_free_bytes=0)
    recorder = DiagnosticRecorder(DiagnosticStore(root, limits))
    recorder.start()
    stop = threading.Event()
    health = {"ok": 0, "failed": 0, "max_ms": 0.0}
    search = {"ok": 0, "failed": 0, "max_ms": 0.0}
    peak_rss = [0]
    observer_ready = threading.Event()

    def sample_rss():
        while not stop.wait(0.05):
            peak_rss[0] = max(peak_rss[0], _rss())

    def observer():
        client = Client("http://127.0.0.1:8000")
        client.login()
        observer_ready.set()
        while not stop.wait(1):
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(health_url, timeout=2) as response:
                    assert response.status == 200
                health["ok"] += 1
            except (OSError, AssertionError):
                health["failed"] += 1
            health["max_ms"] = max(health["max_ms"], (time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            try:
                result = client.call("POST", "/api/search", {
                    "query": "Synthetic diagnostic restore drill", "locale": "en",
                    "dense": False, "bm25": True, "use_glossary": False, "top_k": 5})
                assert result.get("hits")
                search["ok"] += 1
            except (OSError, AssertionError, ValueError):
                search["failed"] += 1
            search["max_ms"] = max(search["max_ms"], (time.perf_counter() - started) * 1000)

    thread = threading.Thread(target=observer, daemon=True)
    sampler = threading.Thread(target=sample_rss, daemon=True)
    thread.start()
    sampler.start()
    if not observer_ready.wait(5):
        stop.set()
        thread.join(timeout=3)
        sampler.join(timeout=3)
        recorder.stop()
        raise RuntimeError("Live-stack observer could not authenticate")
    attempted = accepted = 0
    starting_rss = _rss()
    peak_rss[0] = max(peak_rss[0], starting_rss)
    started = time.perf_counter()
    try:
        while time.perf_counter() - started < seconds:
            # The schedule describes the intended arrival rate; falling behind
            # is reported as achieved rate, never hidden by a burst at the end.
            due = min(int((time.perf_counter() - started) * target_rate), int(seconds * target_rate))
            if attempted >= due:
                time.sleep(0.0001)
                continue
            attempted += 1
            accepted += bool(recorder.emit(
                "operation_failed", context=DiagnosticContext(request_id=str(uuid4())),
                fields={"stage": "search", "error_code": "internal_error"}))
    finally:
        elapsed = time.perf_counter() - started
        stop.set()
        thread.join(timeout=3)
        sampler.join(timeout=3)
        recorder.stop()
    status = recorder.status()
    return {"duration_seconds": elapsed, "target_rate_per_second": target_rate,
            "attempted": attempted, "accepted": accepted,
            "achieved_rate_per_second": attempted / elapsed,
            "starting_process_rss_bytes": starting_rss,
            "peak_process_rss_bytes": max(peak_rss[0], _rss()),
            "health": health, "search": search,
            "recorder": {key: status[key] for key in
                         ("written", "dropped", "invalid", "repeats", "queued",
                          "used_bytes", "reserved_bytes", "storage_degraded")},
            "quota_bytes": limits.total_bytes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--target-rate", type=int, default=10000)
    parser.add_argument("--health-url", default="http://127.0.0.1:18084/health")
    args = parser.parse_args()
    result = run(args.root, seconds=args.seconds, target_rate=args.target_rate,
                 health_url=args.health_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: result[key] for key in ("attempted", "accepted", "health", "search")},
                     sort_keys=True))
