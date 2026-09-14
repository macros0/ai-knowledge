"""Measure BM25 glossary alias-budget trade-offs without changing defaults."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import get_settings
from app.services.vector_store import VectorStore
from test_scripts.probe_sources import load_cases, run_case


def _percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


def _run_cap(cases: list[dict], cap: int, api: str) -> dict:
    base = get_settings()
    settings = base.model_copy(update={"glossary_max_added_aliases_per_term": cap})
    vector_store = VectorStore()
    results = [
        run_case(
            {**case, "api": api, "mode": "bm25"},
            glossary_mode="on",
            settings=settings,
            vector_store=vector_store,
        )
        for case in cases
    ]
    totals = [case["timings_ms"]["total_ms"] for case in results]
    failures = [
        case["id"]
        for case in results
        if case["acceptance"]["missing_canonicals"]
        or case["acceptance"]["forbidden_applied"]
        or case["acceptance"].get("unexpected_canonicals")
    ]
    return {
        "requests": len(results),
        "acceptance_failures": failures,
        "timings": {
            "avg_ms": round(statistics.mean(totals), 3),
            "p50_ms": round(statistics.median(totals), 3),
            "p95_ms": round(_percentile(totals, 0.95), 3),
        },
        "avg_added_forms": round(
            statistics.mean(len(case["query_plan"]["added_sparse_texts"]) for case in results), 3
        ),
        "results": results,
    }


def _source_key(source: dict) -> tuple[object, object, object]:
    return source.get("doc_id"), source.get("slug"), source.get("chunk_index")


def _raw_source_keys(case: dict) -> set[tuple[object, object, object]]:
    return {
        _source_key(candidate.get("source") or {})
        for candidate in case.get("raw_candidates", [])
    }


def _final_source_keys(case: dict) -> set[tuple[object, object, object]]:
    keys: set[tuple[object, object, object]] = set()
    for block in case.get("final_blocks", []):
        sources = block.get("source_keys") or ([block["source"]] if block.get("source") else [])
        keys.update(_source_key(source) for source in sources)
    return keys


def _source_diff(results: list[dict], baseline: list[dict]) -> dict:
    baseline_by_id = {case["id"]: case for case in baseline}
    changed_cases = 0
    raw_added = raw_lost = final_added = final_lost = 0
    for case in results:
        before = baseline_by_id[case["id"]]
        before_raw, after_raw = _raw_source_keys(before), _raw_source_keys(case)
        before_final, after_final = _final_source_keys(before), _final_source_keys(case)
        raw_added += len(after_raw - before_raw)
        raw_lost += len(before_raw - after_raw)
        final_added += len(after_final - before_final)
        final_lost += len(before_final - after_final)
        if before_raw != after_raw or before_final != after_final:
            changed_cases += 1
    return {
        "changed_cases": changed_cases,
        "raw_sources_added": raw_added,
        "raw_sources_lost": raw_lost,
        "final_sources_added": final_added,
        "final_sources_lost": final_lost,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--caps", type=int, nargs="+", default=[0, 1, 2, 4])
    args = parser.parse_args()

    cases = load_cases()
    default_cap = int(get_settings().glossary_max_added_aliases_per_term)
    requested_caps = list(dict.fromkeys([*args.caps, default_cap]))
    caps = {
        str(cap): {api: _run_cap(cases, cap, api) for api in ("search", "chat")}
        for cap in requested_caps
    }
    default_cap_key = str(default_cap)
    if default_cap_key in caps:
        for cap_result in caps.values():
            for api in ("search", "chat"):
                cap_result[api]["source_diff_vs_default"] = _source_diff(
                    cap_result[api]["results"], caps[default_cap_key][api]["results"]
                )
                del cap_result[api]["results"]
    result = {
        "schema_version": 1,
        "scope": "bm25_alias_budget_tradeoff",
        "cases": len(cases),
        "default_cap": get_settings().glossary_max_added_aliases_per_term,
        "caps": caps,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
