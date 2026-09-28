"""Isolated repeatable TestClient probe; synthetic requests, no customer corpus."""
import argparse
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


def _rss_bytes():
    if os.name == "nt":
        result = subprocess.run(["powershell", "-NoProfile", "-Command",
                                 f"(Get-Process -Id {os.getpid()}).WorkingSet64"],
                                capture_output=True, text=True, timeout=10, check=False)
        try:
            return int(result.stdout.strip())
        except ValueError:
            return None
    try:
        pages = int(Path("/proc/self/statm").read_text().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def _series(request_fn, warmup_seconds, requests, *, on_samples_start=None):
    def once():
        start = time.perf_counter()
        response = request_fn()
        if response.status_code != 200:
            raise RuntimeError(f"Synthetic API request failed: HTTP {response.status_code}")
        return (time.perf_counter() - start) * 1000

    deadline = time.monotonic() + warmup_seconds
    while time.monotonic() < deadline:
        once()
    if on_samples_start is not None:
        on_samples_start()
    samples = [once() for _ in range(requests)]
    return {"requests": len(samples), "p50_ms": statistics.median(samples),
            "p95_ms": sorted(samples)[int(0.95 * (len(samples) - 1))],
            "max_ms": max(samples), "rss_bytes": _rss_bytes()}


def _build_once(queue, counts):
    now = datetime.now(timezone.utc)
    request = BundleRequest(from_utc=now - timedelta(minutes=2), to_utc=now)
    actor = User(user_id="probe-admin", username="probe-admin", roles=["admin"])
    started = time.perf_counter()
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
        counts["elapsed_seconds"] = time.perf_counter() - started


def run(root, *, warmup_seconds=30, requests=200, scenario="settings"):
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
    recorder = DiagnosticRecorder(store, capture_selector=sessions.active_for,
                                  on_capture_written=sessions.record_written,
                                  on_capture_failed=sessions.recording_failed)
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
        baseline = _series(request_fn, warmup_seconds, requests)
        started = client.post("/api/admin/diagnostics/sessions", json={"scope": "system", "minutes": 5})
        if started.status_code != 201:
            raise RuntimeError(f"Synthetic capture could not start: HTTP {started.status_code}")
        capture = _series(request_fn, warmup_seconds, requests)
        counts = {"built": 0, "failed": 0}
        builder = threading.Thread(target=_build_once, args=(queue, counts), daemon=True)
        try:
            capture_build = _series(request_fn, warmup_seconds, requests, on_samples_start=builder.start)
        finally:
            if builder.ident is not None:
                builder.join(timeout=300)
        if builder.is_alive() or counts["built"] != 1:
            raise RuntimeError("Synthetic isolated ZIP build did not complete")
        stopped = client.post(f"/api/admin/diagnostics/sessions/{started.json()['id']}/stop")
        if stopped.status_code != 200:
            raise RuntimeError(f"Synthetic capture could not stop: HTTP {stopped.status_code}")
        result = {"environment": f"isolated TestClient; {scenario}; no external LLM/Qdrant",
                  "schema_version": 1, "warmup_seconds": warmup_seconds,
                  "request_count": requests, "scenario": scenario,
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--warmup-seconds", type=int, default=30)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--scenario", choices=("settings", "search_stub"), default="settings")
    args = parser.parse_args()
    if args.warmup_seconds < 0 or args.requests < 200:
        parser.error("Use nonnegative warmup and at least 200 requests")
    result = run(args.root, warmup_seconds=args.warmup_seconds, requests=args.requests,
                 scenario=args.scenario)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("p95_capture_overhead_pct", "p95_build_overhead_pct", "bundles")},
                     sort_keys=True))


if __name__ == "__main__":
    main()
