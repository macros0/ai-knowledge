"""Parallel retrieval smoke for the Stage 8 corpus probe.

Runs the existing probe pipeline concurrently. It intentionally stops before
answer generation, so the measurements cover preparation, embedding, Qdrant,
visibility, hydration, merge and chat context filters only.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from statistics import mean, median
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import get_engine
from app.services.glossary.registry import invalidate_snapshot_cache
from app.services.embedder import Embedder
from app.services.llm_client import LLMClient
from app.services.vector_store import VectorStore
from test_scripts.probe_sources import load_cases, run_case


class CountingEmbedder:
    def __init__(self) -> None:
        self._embedder = Embedder()
        self._lock = threading.Lock()
        self.calls = 0

    def embed(self, query):
        with self._lock:
            self.calls += 1
        return self._embedder.embed(query)


def _summary(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "avg_ms": round(mean(ordered), 3),
        "p50_ms": round(median(ordered), 3),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
    }


def run_parallel_retrieval(
    cases: list[dict],
    *,
    state: str,
    clients: int,
    queries_per_client: int,
) -> dict:
    """Run the retrieval probe in-process, without parsing CLI output."""
    invalidate_snapshot_cache()
    embedder = CountingEmbedder()
    vector_store = VectorStore()
    engine = get_engine()
    sql_count = [0]
    sql_lock = threading.Lock()

    def before_cursor(*_args):
        with sql_lock:
            sql_count[0] += 1

    def worker(worker_id: int) -> list[dict]:
        start = worker_id * queries_per_client
        selected = [cases[(start + offset) % len(cases)] for offset in range(queries_per_client)]
        return [
            run_case(
                {**case, "api": "chat", "mode": "hybrid"},
                glossary_mode=state,
                vector_store=vector_store,
                embedder=embedder,
            )
            for case in selected
        ]

    from sqlalchemy import event

    event.listen(engine, "before_cursor_execute", before_cursor)
    try:
        with ThreadPoolExecutor(max_workers=clients) as pool:
            results = [item for batch in pool.map(worker, range(clients)) for item in batch]
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor)

    timings = [case["timings_ms"]["total_ms"] for case in results]
    # Expected canonical labels apply to the glossary-enabled side only;
    # baseline off deliberately has no applied terms.
    failures = (
        [case["id"] for case in results if case["acceptance"]["missing_canonicals"]]
        if state == "on"
        else []
    )
    return {
        "requests": len(results),
        "errors": 0,
        "acceptance_failures": failures,
        "timings": _summary(timings),
        "sql_statements": sql_count[0],
        "embedding_calls": embedder.calls,
    }


def _run_external_matrix(cases: list[dict]) -> dict:
    """Count retrieval-side calls for every API/branch combination.

    The probe intentionally stops before answer generation.  Therefore an
    LLM call in this retrieval path is a failure by construction; the
    explicit zero is kept in the artifact to make that boundary reviewable.
    """
    result: dict[str, dict[str, int]] = {}
    for api in ("search", "chat"):
        for mode in ("dense", "bm25", "hybrid"):
            key = f"{api}/{mode}"
            result[key] = {}
            for state in ("off", "on"):
                embedder = CountingEmbedder()
                vector_store = VectorStore()
                errors = 0
                llm_calls = [0]

                def forbidden_llm_call(*_args, **_kwargs):
                    llm_calls[0] += 1
                    raise AssertionError("answer LLM must not run in retrieval probe")

                with patch.object(LLMClient, "chat", forbidden_llm_call), patch.object(
                    LLMClient, "chat_json", forbidden_llm_call
                ):
                    for case in cases:
                        try:
                            run_case(
                                {**case, "api": api, "mode": mode},
                                glossary_mode=state,
                                vector_store=vector_store,
                                embedder=embedder,
                            )
                        except Exception:
                            errors += 1
                result[key][state] = {
                    "requests": len(cases),
                    "errors": errors,
                    "llm_calls": llm_calls[0],
                    "embedding_calls": embedder.calls,
                }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("glossary-probe-cases.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clients", type=int, default=5)
    parser.add_argument("--queries-per-client", type=int, default=20)
    parser.add_argument(
        "--external-matrix",
        action="store_true",
        help="also count retrieval-side calls for every API/mode combination",
    )
    args = parser.parse_args()

    cases = load_cases(args.cases)
    result = {
        "schema_version": 1,
        "scope": "parallel_retrieval_without_llm",
        "clients": args.clients,
        "queries_per_client": args.queries_per_client,
        "cases": [case["id"] for case in cases],
        "states": {
            state: run_parallel_retrieval(
                cases,
                state=state,
                clients=args.clients,
                queries_per_client=args.queries_per_client,
            )
            for state in ("off", "on")
        },
    }
    if args.external_matrix:
        result["external_call_matrix"] = _run_external_matrix(cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
