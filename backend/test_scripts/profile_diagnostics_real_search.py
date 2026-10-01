"""Measure synthetic real-search stages with and without capture in one process.

Run only against an isolated synthetic PostgreSQL/Qdrant stack. This bypasses
the HTTP proxy to locate backend stage cost; it is not the acceptance matrix.
"""
import argparse
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
import statistics
import sys
import time
from uuid import uuid4

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import search as search_api
from app.auth.models import User
from app.config import get_settings
from app.models.schemas import SearchRequest
from app.services.diagnostics.context import bind_context
from app.services.diagnostics.policy import CaptureRuntimeView, build_policy
from app.services.diagnostics.recorder import DiagnosticRecorder, set_recorder
from app.services.diagnostics.schema import DiagnosticContext
from app.services.diagnostics.store import DiagnosticStore


def p95(values):
    values = sorted(values)
    return values[int(.95 * (len(values) - 1))] if values else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--warmup-seconds", type=int, default=30)
    parser.add_argument("--measurement-seconds", type=int, default=60)
    parser.add_argument("--min-requests", type=int, default=1000)
    args = parser.parse_args()
    settings = get_settings()
    request = SearchRequest(query="Synthetic diagnostic restore drill", locale="en",
                            dense=False, bm25=True, use_glossary=False, top_k=5)
    current = [None]
    durations = defaultdict(list)
    cpu_durations = defaultdict(list)

    def instrument(name, function):
        def measured(*items, **options):
            started = time.perf_counter_ns()
            cpu_started = time.thread_time_ns()
            try:
                return function(*items, **options)
            finally:
                if current[0] is not None:
                    durations[(current[0], name)].append((time.perf_counter_ns() - started) / 1_000_000)
                    cpu_durations[(current[0], name)].append(
                        (time.thread_time_ns() - cpu_started) / 1_000_000)
        return measured

    vector_store = search_api._get_vector_store()
    # This is a local stage profiler, not an HTTP acceptance run. The public
    # rate limit would otherwise stop a 1000-request measurement after 120 calls.
    limiter = search_api.get_rate_limiter()
    original_check_action = limiter.check_action
    limiter.check_action = lambda *_args, **_kwargs: None
    originals = {
        "qdrant": vector_store.search_composite,
        "hydrate": search_api.load_visible_retrieval_hits,
        "merge": search_api.merge_and_format,
        "append": DiagnosticStore.append_batch,
    }
    vector_store.search_composite = instrument("qdrant", originals["qdrant"])
    search_api.load_visible_retrieval_hits = instrument("hydrate", originals["hydrate"])
    search_api.merge_and_format = instrument("merge", originals["merge"])
    DiagnosticStore.append_batch = instrument("append", originals["append"])
    try:
        for mode in ("baseline", "standard", "detailed"):
            root = args.root / mode
            root.mkdir(parents=True, exist_ok=True)
            limits = replace(settings.diagnostics_limits(), min_free_bytes=0)
            store = DiagnosticStore(root, limits)
            store.open()
            view = None if mode == "baseline" else CaptureRuntimeView(
                session_id=str(uuid4()), revision=1, scope="system", doc_id=None,
                deadline_mono=time.monotonic() + 3600, policy=build_policy(mode, settings))
            recorder = DiagnosticRecorder(store,
                                          capture_selector=lambda *_: view.session_id if view else None,
                                          capture_view_selector=lambda *_: view)
            recorder.start()
            set_recorder(recorder)
            user = User(user_id=f"diagprof-{mode}", username=f"diagprof-{mode}", roles=["viewer"])

            def once():
                started = time.perf_counter_ns()
                cpu_started = time.thread_time_ns()
                context = DiagnosticContext(operation_id=str(uuid4()), operation_kind="search_chat")
                with bind_context(context):
                    recorder.begin_trace(context)
                    result = search_api.search(request, current_user=user)
                if not result.hits:
                    raise RuntimeError("Synthetic corpus yielded no real search hits")
                return ((time.perf_counter_ns() - started) / 1_000_000,
                        (time.thread_time_ns() - cpu_started) / 1_000_000)

            try:
                warmup_end = time.monotonic() + args.warmup_seconds
                while time.monotonic() < warmup_end:
                    once()
                current[0] = mode
                started = time.monotonic()
                while (time.monotonic() - started < args.measurement_seconds
                       or len(durations[(mode, "total")]) < args.min_requests):
                    wall_ms, cpu_ms = once()
                    durations[(mode, "total")].append(wall_ms)
                    cpu_durations[(mode, "total")].append(cpu_ms)
                current[0] = None
            finally:
                current[0] = None
                set_recorder(None)
                recorder.stop()
                store.close()
            print(mode, {name: {"count": len(durations[(mode, name)]),
                                "p50_ms": round(statistics.median(durations[(mode, name)]), 3),
                                "p95_ms": round(p95(durations[(mode, name)]), 3),
                                "sum_ms": round(sum(durations[(mode, name)]), 3),
                                "thread_cpu_p95_ms": round(p95(cpu_durations[(mode, name)]), 3),
                                "thread_cpu_sum_ms": round(sum(cpu_durations[(mode, name)]), 3)}
                         for name in ("total", "qdrant", "hydrate", "merge", "append")
                         if durations[(mode, name)]}, flush=True)
    finally:
        limiter.check_action = original_check_action
        vector_store.search_composite = originals["qdrant"]
        search_api.load_visible_retrieval_hits = originals["hydrate"]
        search_api.merge_and_format = originals["merge"]
        DiagnosticStore.append_batch = originals["append"]


if __name__ == "__main__":
    main()
