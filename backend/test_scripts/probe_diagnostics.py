"""Isolated repeatable TestClient probe; synthetic requests, no customer corpus."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from anyio.from_thread import start_blocking_portal

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__:
    from .probe_diagnostics_http_matrix import _tree_rss_bytes
else:
    from probe_diagnostics_http_matrix import _tree_rss_bytes

from app import config
from app.auth.models import User
from app.db.models import DiagnosticBundle
from app.db.session import configure_for_tests, init_db, session_scope
from app.models.diagnostics import BundleRequest
from app.services.diagnostics.bundle_queue import DiagnosticBundleQueue
from app.services.diagnostics.recorder import DiagnosticRecorder, set_recorder
from app.services.diagnostics.sessions import DiagnosticSessionService
from app.services.diagnostics.store import DiagnosticStore
from tests.test_authz import login, make_client


def _rss_bytes(pid=None):
    pid = pid or os.getpid()
    if os.name == "nt":
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-Command",
                                     f"(Get-Process -Id {pid}).WorkingSet64"],
                                    capture_output=True, text=True, timeout=10, check=False)
            return int(result.stdout.strip())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
    try:
        pages = int(Path(f"/proc/{pid}/statm").read_text().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


class PeakRssTracker:
    """Keep the highest observed RSS for each process role."""

    def __init__(self):
        self._peaks = {}

    def observe(self, role, rss_bytes):
        if rss_bytes is not None:
            self._peaks[role] = max(self._peaks.get(role, 0), rss_bytes)

    def peak(self, role):
        return self._peaks.get(role)


def phase_overlap(sample, phase):
    """Half-open monotonic intervals overlap only when both have duration."""
    return sample[0] < phase[1] and phase[0] < sample[1]


def _series(request_fn, warmup_seconds, requests, *, measurement_seconds=0,
            concurrency=1, on_samples_start=None, child_pid=None):
    if concurrency not in (1, 8):
        raise ValueError("Unsupported probe concurrency")

    def once():
        started = time.perf_counter()
        response = request_fn()
        finished = time.perf_counter()
        if response.status_code != 200:
            raise RuntimeError(f"Synthetic API request failed: HTTP {response.status_code}")
        return started, finished, (finished - started) * 1000

    peaks = PeakRssTracker()
    done = threading.Event()

    def sample_rss():
        while not done.is_set():
            if os.name != "nt":
                total, children = _tree_rss_bytes(os.getpid())
                peaks.observe("tree", total)
                peaks.observe("parent", total - children)
                peaks.observe("child", children)
            else:
                parent = _rss_bytes()
                child = 0
                pid = child_pid() if child_pid else None
                if pid:
                    child = _rss_bytes(pid)
                peaks.observe("parent", parent)
                peaks.observe("child", child)
                if parent is not None and child is not None:
                    peaks.observe("tree", parent + child)
            done.wait(0.25 if os.name == "nt" else 0.02)

    sampler = threading.Thread(target=sample_rss, daemon=True)
    sampler.start()
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            warmup_deadline = time.monotonic() + warmup_seconds
            while time.monotonic() < warmup_deadline:
                list(pool.map(lambda _: once(), range(concurrency)))
            if on_samples_start is not None:
                on_samples_start()
            started = time.perf_counter()
            cpu_started = time.process_time()
            if os.name != "nt":
                import resource
                usage = resource.getrusage(resource.RUSAGE_CHILDREN)
                child_cpu_started = usage.ru_utime + usage.ru_stime
            intervals = []
            while len(intervals) < requests or time.perf_counter() - started < measurement_seconds:
                intervals.extend(pool.map(lambda _: once(), range(concurrency)))
            finished = time.perf_counter()
            cpu_seconds = time.process_time() - cpu_started
            if os.name != "nt":
                usage = resource.getrusage(resource.RUSAGE_CHILDREN)
                child_cpu_seconds = usage.ru_utime + usage.ru_stime - child_cpu_started
            else:
                child_cpu_seconds = None
    finally:
        done.set()
        sampler.join(timeout=2)
    samples = sorted(interval[2] for interval in intervals)
    elapsed = finished - started
    return {"requests": len(samples), "p50_ms": statistics.median(samples),
            "p95_ms": samples[int(0.95 * (len(samples) - 1))],
            "p99_ms": samples[int(0.99 * (len(samples) - 1))],
            "max_ms": samples[-1], "elapsed_seconds": elapsed,
            "throughput_per_second": len(samples) / elapsed,
            "cpu_seconds": cpu_seconds,
            "child_cpu_seconds": child_cpu_seconds,
            "peak_parent_rss_bytes": peaks.peak("parent"),
            "peak_child_rss_bytes": peaks.peak("child"),
            "peak_process_tree_rss_bytes": peaks.peak("tree"),
            "_intervals": [(item[0], item[1]) for item in intervals]}


def _build_once(queue, counts):
    now = datetime.now(timezone.utc)
    request = BundleRequest(from_utc=now - timedelta(minutes=2), to_utc=now)
    actor = User(user_id="probe-admin", username="probe-admin", roles=["admin"])
    started = time.perf_counter()
    counts["started_monotonic"] = started
    try:
        submitted = queue.submit(request, actor)
        queue.start()
        if not queue.wait_idle(300):
            raise RuntimeError("Synthetic bundle did not finish")
        with session_scope() as db:
            row = db.get(DiagnosticBundle, submitted.id)
            if row is None or row.status != "ready":
                raise RuntimeError("Synthetic bundle failed")
        counts["built"] += 1
    except Exception:
        counts["failed"] += 1
    finally:
        counts["finished_monotonic"] = time.perf_counter()
        counts["elapsed_seconds"] = counts["finished_monotonic"] - started


def run(root, *, warmup_seconds=30, requests=200, measurement_seconds=0,
        concurrency=1, scenario="settings", capture_level=None):
    if capture_level not in (None, "standard", "detailed"):
        raise ValueError("Unknown capture level")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    configure_for_tests(f"sqlite:///{(root / 'probe.db').as_posix()}")
    init_db()
    monkeypatch = pytest.MonkeyPatch()
    client = make_client(root / "app", monkeypatch, diagnostics_dir=root / "spool",
                         diagnostics_capture_enabled=True, diagnostics_bundle_enabled=True,
                         diagnostics_download_enabled=True, diagnostics_min_free_mb=0)
    settings = config.get_settings()
    store = DiagnosticStore(settings.diagnostics_dir, settings.diagnostics_limits())
    store.open()
    sessions = DiagnosticSessionService(store, settings)
    recorder_options = {"capture_selector": sessions.active_for,
                        "on_capture_written": sessions.record_written,
                        "on_capture_failed": sessions.recording_failed}
    # The same measurement harness can run the original, immutable app tree.
    # This is a probe compatibility path, never a product policy switch.
    view_selector = getattr(sessions, "runtime_view_for", None)
    if view_selector is not None:
        recorder_options["capture_view_selector"] = view_selector
    recorder = DiagnosticRecorder(store, **recorder_options)
    recorder.start()
    set_recorder(recorder)
    queue = DiagnosticBundleQueue(store)
    client.app.state.diagnostics = SimpleNamespace(settings=settings, store=store, sessions=sessions,
                                                    recorder=recorder, bundle_queue=queue)
    portal_stack = ExitStack()
    try:
        # TestClient otherwise creates a Windows asyncio socketpair for every
        # request; long warmups can stall in socket.accept before reaching the app.
        client.portal = portal_stack.enter_context(start_blocking_portal())
        login(client, "demo.admin")
        if scenario == "search_stub":
            from app.api import search as search_api

            monkeypatch.setattr(search_api, "_get_embedder", lambda: SimpleNamespace(embed=lambda _: [0.1]))
            monkeypatch.setattr(search_api, "_get_vector_store", lambda: SimpleNamespace(
                search_composite=lambda **_: (time.sleep(0.01), [])[1]))
            monkeypatch.setattr(search_api, "get_rate_limiter", lambda: SimpleNamespace(
                check_action=lambda *_, **__: None))
            request_fn = lambda: client.post("/api/search", json={
                "query": "synthetic search", "dense": True, "bm25": False, "use_glossary": False,
            })
        elif scenario == "settings":
            request_fn = lambda: client.get("/api/settings")
        else:
            raise ValueError("Unknown synthetic scenario")
        options = {"measurement_seconds": measurement_seconds, "concurrency": concurrency,
                   "child_pid": lambda: getattr(queue._child, "pid", None)}
        baseline = _series(request_fn, warmup_seconds, requests, **options)
        start_payload = {"scope": "system", "minutes": 5}
        if capture_level is not None:
            start_payload["capture_level"] = capture_level
        started = client.post("/api/admin/diagnostics/sessions", json=start_payload)
        if started.status_code != 201:
            raise RuntimeError(f"Synthetic capture could not start: HTTP {started.status_code}")
        capture = _series(request_fn, warmup_seconds, requests, **options)
        counts = {"built": 0, "failed": 0}
        builder = threading.Thread(target=_build_once, args=(queue, counts), daemon=True)
        try:
            capture_build = _series(request_fn, warmup_seconds, requests,
                                    on_samples_start=builder.start, **options)
        finally:
            if builder.ident is not None:
                builder.join(timeout=300)
        if builder.is_alive() or counts["built"] != 1:
            raise RuntimeError("Synthetic isolated ZIP build did not complete")
        phase = (counts["started_monotonic"], counts["finished_monotonic"])
        capture_build["requests_overlapping_build"] = sum(
            phase_overlap(interval, phase) for interval in capture_build.pop("_intervals"))
        baseline.pop("_intervals")
        capture.pop("_intervals")
        stopped = client.post(f"/api/admin/diagnostics/sessions/{started.json()['id']}/stop")
        if stopped.status_code != 200:
            raise RuntimeError(f"Synthetic capture could not stop: HTTP {stopped.status_code}")
        result = {"environment": f"isolated TestClient; {scenario}; no external LLM/Qdrant",
                  "schema_version": 2, "warmup_seconds": warmup_seconds,
                  "minimum_measurement_seconds": measurement_seconds,
                  "concurrency": concurrency, "minimum_request_count": requests,
                  "scenario": scenario,
                  "baseline": baseline, "capture": capture,
                  "capture_build": capture_build, "bundles": counts,
                  "recorder": recorder.status(), "backend_pid": os.getpid()}
        result["p95_capture_overhead_pct"] = 100 * (capture["p95_ms"] / baseline["p95_ms"] - 1)
        result["p95_build_overhead_pct"] = 100 * (capture_build["p95_ms"] / baseline["p95_ms"] - 1)
        return result
    finally:
        client.portal = None
        portal_stack.close()
        set_recorder(None)
        queue.shutdown()
        recorder.stop()
        store.close()
        monkeypatch.undo()


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported probe value: {type(value).__name__}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--warmup-seconds", type=int, default=30)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--measurement-seconds", type=int, default=0)
    parser.add_argument("--concurrency", type=int, choices=(1, 8), default=1)
    parser.add_argument("--scenario", choices=("settings", "search_stub"), default="settings")
    parser.add_argument("--capture-level", choices=("standard", "detailed"))
    args = parser.parse_args()
    if args.warmup_seconds < 0 or args.measurement_seconds < 0 or args.requests < 200:
        parser.error("Use nonnegative durations and at least 200 requests")
    result = run(args.root, warmup_seconds=args.warmup_seconds, requests=args.requests,
                 measurement_seconds=args.measurement_seconds, concurrency=args.concurrency,
                 scenario=args.scenario, capture_level=args.capture_level)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True,
                                      default=_json_default) + "\n",
                           encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("p95_capture_overhead_pct", "p95_build_overhead_pct", "bundles")},
                     sort_keys=True))


if __name__ == "__main__":
    main()
