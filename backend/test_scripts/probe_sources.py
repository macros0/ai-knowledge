# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Corpus acceptance probe for the API's composite search path.

The probe deliberately calls the same ``prepare_query``/
``build_query_sparse``/``resolve_branches``/context-filter functions as the
chat and search APIs. It never calls the answer LLM. Results contain both the
raw Qdrant candidates and final post-merge blocks, so an off/on comparison can
distinguish a rank shift from a source disappearing entirely.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.services.context_builder import (
    drop_partial_title_matches,
    drop_unmatched_blocks,
    matched_terms,
    merge_and_format,
    resolve_branches,
)
from app.services.embedder import Embedder
from app.services.glossary.expansion import prepare_query
from app.services.glossary.query_sparse import build_query_sparse
from app.services.retrieval_hydration import load_visible_retrieval_hits
from app.services.search_filter import build_doc_lookup
from app.services.sparse import to_sparse_vector
from app.services.stopwords import KIND_BM25, get_stopwords
from app.services.vector_store import VectorStore


# The legacy set remains available when no cases file is supplied. The checked
# in case file below expands it to the 40-case acceptance matrix.
PROBES = [
    {"id": "legacy-tabular-tag", "q": "Выбор табельного номера", "tags": ["СФР ПР"]},
    {"id": "legacy-tabular", "q": "Выбор табельного номера", "tags": None},
    {"id": "legacy-benefit-codes", "q": "Коды условий расчёта пособий", "tags": None},
    {"id": "legacy-2ndfl", "q": "Подписанты в 2-НДФЛ", "tags": None},
    {"id": "legacy-lk", "q": "Какие интеграции с ЛК есть", "tags": None},
    {"id": "legacy-en-disability", "q": "the certificate of disability", "tags": None},
    {"id": "legacy-en-table", "q": "for all entries in table", "tags": None},
]

DEFAULT_CASES_PATH = Path(__file__).with_name("glossary-probe-cases.json")


def load_cases(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Load and validate the reproducible acceptance cases."""
    source = Path(path) if path is not None else DEFAULT_CASES_PATH
    if not source.exists():
        if path is not None:
            raise FileNotFoundError(source)
        cases = PROBES
    else:
        data = json.loads(source.read_text(encoding="utf-8"))
        cases = data.get("cases", data) if isinstance(data, dict) else data
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty JSON array")

    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(cases, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"case #{index} must be an object")
        case = dict(raw)
        case["id"] = str(case.get("id") or f"case-{index:02d}")
        case["query"] = case.get("query", case.get("q"))
        if not isinstance(case["query"], str) or not case["query"].strip():
            raise ValueError(f"{case['id']}: query must be a non-empty string")
        if case["id"] in ids:
            raise ValueError(f"duplicate case id: {case['id']}")
        ids.add(case["id"])
        case.setdefault("locale", "ru")
        case.setdefault("api", "chat")
        if case["api"] not in {"search", "chat"}:
            raise ValueError(f"{case['id']}: api must be 'search' or 'chat'")
        case.setdefault("mode", "hybrid")
        case.setdefault("top_k", 10)
        case.setdefault("tags", None)
        case.setdefault("source_locales", None)
        case.setdefault("include_unknown_source_locale", False)
        result.append(case)
    return result


def expand_cases(
    cases: list[dict[str, Any]],
    *,
    modes: tuple[str, ...] = ("dense", "bm25", "hybrid"),
    apis: tuple[str, ...] = ("search", "chat"),
) -> list[dict[str, Any]]:
    """Create deterministic mode/API variants from one accepted case set."""
    expanded: list[dict[str, Any]] = []
    for case in cases:
        for mode in modes:
            for api in apis:
                variant = deepcopy(case)
                variant["id"] = f"{case['id']}__{mode}__{api}"
                variant["mode"] = mode
                variant["api"] = api
                expanded.append(variant)
    return expanded


def _payload(hit: Any) -> dict[str, Any]:
    return hit.payload if hasattr(hit, "payload") else hit.get("payload", {})


def _score(hit: Any) -> float:
    return float(hit.score if hasattr(hit, "score") else hit.get("score", 0.0))


def _source_identity(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": payload.get("doc_id"),
        "slug": payload.get("slug"),
        "chunk_index": payload.get("chunk_index"),
    }


def _unique_source_identities(hits: list[Any], *, doc_id: str, chunk_index: Any) -> list[dict[str, Any]]:
    seen: set[tuple[Any, Any, Any]] = set()
    sources: list[dict[str, Any]] = []
    for hit in hits:
        payload = _payload(hit)
        if payload.get("doc_id") != doc_id or payload.get("chunk_index") != chunk_index:
            continue
        source = _source_identity(payload)
        key = (source["doc_id"], source["slug"], source["chunk_index"])
        if key not in seen:
            seen.add(key)
            sources.append(source)
    return sources


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return value


def _value_hash(value: Any) -> str | None:
    if value is None:
        return None
    try:
        encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    except TypeError:
        encoded = repr(value)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _raw_candidate(hit: Any, rank: int) -> dict[str, Any]:
    payload = _payload(hit)
    return {
        "rank": rank,
        "score": _score(hit),
        "point_id": str(getattr(hit, "point_id", "")) if hasattr(hit, "point_id") else None,
        "point_type": payload.get("point_type"),
        "title": payload.get("title", ""),
        "source": _source_identity(payload),
    }


def _final_block(block: dict[str, Any], raw_hits: list[Any], query: str) -> dict[str, Any]:
    # merge_and_format already selected the concept/chunk represented by this
    # block. Looking at every raw hit in the (doc, chunk) group would attach
    # sibling concepts to the wrong block and make source-loss reports lie.
    source_slug = block.get("source_slug", block.get("slug"))
    if source_slug is None and block.get("point_type") == "concept":
        filepath = str(block.get("filepath", ""))
        if filepath:
            source_slug = Path(filepath).stem
    source = {
        "doc_id": block.get("doc_id"),
        "slug": source_slug,
        "chunk_index": block.get("chunk_index"),
    }
    source_keys = [source]
    return {
        "title": block.get("title", ""),
        "score": float(block.get("score", 0.0)),
        "tags": list(block.get("tags", [])),
        "point_type": block.get("point_type"),
        "kind": block.get("kind"),
        "matched_terms": matched_terms(block, query),
        "source": source,
        "source_keys": source_keys,
    }


def run_case(
    case: dict[str, Any],
    *,
    glossary_mode: str,
    settings: Any | None = None,
    vector_store: Any | None = None,
    embedder: Any | None = None,
) -> dict[str, Any]:
    """Run one case through the exact API retrieval pipeline."""
    if glossary_mode not in {"off", "on"}:
        raise ValueError("glossary_mode must be 'off' or 'on'")
    settings = settings or get_settings()
    vector_store = vector_store or VectorStore()
    embedder = embedder or Embedder()
    query = case["query"]
    timings: dict[str, float] = {}
    total_started = perf_counter()

    started = perf_counter()
    plan = prepare_query(
        query,
        ui_locale=case.get("locale", "ru"),
        enabled=glossary_mode == "on",
        settings=settings,
    )
    timings["prepare_ms"] = round((perf_counter() - started) * 1000, 3)

    branches = resolve_branches(
        case.get("mode"), case.get("dense"), case.get("bm25"), settings
    )
    started = perf_counter()
    vector = embedder.embed(plan.dense_query) if "dense" in branches else None
    timings["embed_ms"] = round((perf_counter() - started) * 1000, 3)

    sparse_vec = None
    if "bm25" in branches:
        sparse_vec = build_query_sparse(
            plan,
            stopwords=get_stopwords(KIND_BM25),
            settings=settings,
        )

    started = perf_counter()
    raw_hits = vector_store.search_composite(
        dense_vec=vector,
        sparse_vec=sparse_vec,
        tags=case.get("tags") or None,
        branches=branches,
        source_locales=case.get("source_locales") or None,
        include_unknown_source_locale=case.get("include_unknown_source_locale", False),
        top_k=settings.search_per_branch_top_k,
    )
    timings["qdrant_ms"] = round((perf_counter() - started) * 1000, 3)
    raw_candidates = [_raw_candidate(hit, rank) for rank, hit in enumerate(raw_hits, 1)]

    postfilter_started = perf_counter()
    hydration_kwargs: dict[str, Any] = {"timings": timings}
    if case.get("api", "chat") == "search":
        # Keep the probe equivalent to /search: that API returns only a
        # 300-character snippet and does not need the chat context budget.
        hydration_kwargs.update(max_concept_chars=300, max_chunk_chars=300)
    exact_groups = getattr(plan, "strict_groups", ()) or plan.match_groups
    if exact_groups:
        hydration_kwargs["exact_groups"] = exact_groups
    visible_hits, doc_lookup = load_visible_retrieval_hits(raw_hits, **hydration_kwargs)

    started = perf_counter()
    filename_lookup = {
        doc_id: (doc or {}).get("filename", "") for doc_id, doc in doc_lookup.items()
    }
    merged = merge_and_format(
        visible_hits,
        settings,
        filename_lookup=filename_lookup,
        exact_groups=exact_groups,
    )
    timings["merge_ms"] = round((perf_counter() - started) * 1000, 3)
    merged_before_top_k = merged
    merged = merged[: int(case.get("top_k", 10))]
    merged_before_context_filters = merged
    started = perf_counter()
    if case.get("api", "chat") == "chat":
        # Keep the probe aligned with chat.py: all glossary-aware context
        # filters for one request share bounded, request-local caches.
        domain_cache = {}
        lexical_cache = {}
        merged = drop_unmatched_blocks(
            merged,
            query,
            match_groups=exact_groups,
            domain_cache=domain_cache,
            lexical_cache=lexical_cache,
        )
        merged = drop_partial_title_matches(
            merged,
            query,
            match_groups=exact_groups,
            domain_cache=domain_cache,
        )
    timings["context_filter_ms"] = round((perf_counter() - started) * 1000, 3)
    timings["postfilter_ms"] = round((perf_counter() - postfilter_started) * 1000, 3)
    timings["total_ms"] = round((perf_counter() - total_started) * 1000, 3)

    applied_terms = [_json_value(term) for term in plan.applied_terms]
    final_blocks = [_final_block(block, visible_hits, query) for block in merged]
    actual_canonicals = [term.get("canonical") for term in applied_terms]
    expected = set(case.get("expected_canonicals", []))
    forbidden = set(case.get("forbidden_canonicals", []))
    result = {
        "id": case["id"],
        "category": case.get("category"),
        "relevance": case.get("relevance", {}),
        "api": case.get("api", "chat"),
        "query": query,
        "locale": case.get("locale", "ru"),
        "mode": case.get("mode"),
        "branches": sorted(branches),
        "retrieval": {
            "top_k": int(case.get("top_k", 10)),
            "per_branch_top_k": settings.search_per_branch_top_k,
            "vector_sha256": _value_hash(vector),
            "sparse_sha256": _value_hash(sparse_vec),
        },
        "filters": {
            "tags": case.get("tags"),
            "source_locales": case.get("source_locales"),
            "include_unknown_source_locale": case.get("include_unknown_source_locale", False),
        },
        "query_plan": {
            "status": plan.status,
            "dense_query": plan.dense_query,
            "added_sparse_texts": list(plan.added_sparse_texts),
            "applied_terms": applied_terms,
            "skipped_reasons": [_json_value(reason) for reason in plan.skipped_reasons],
            "rules_version": plan.rules_version,
        },
        "raw_candidates": raw_candidates,
        "visible_candidates": [_raw_candidate(hit, rank) for rank, hit in enumerate(visible_hits, 1)],
        "merged_blocks": [_final_block(block, visible_hits, query) for block in merged_before_top_k],
        "top_k_blocks": [_final_block(block, visible_hits, query) for block in merged_before_context_filters],
        "final_blocks": final_blocks,
        "timings_ms": timings,
        "acceptance": {
            "expected_canonicals": sorted(expected),
            "missing_canonicals": sorted(expected - set(actual_canonicals)),
            "forbidden_applied": sorted(forbidden & set(actual_canonicals)),
            "unexpected_canonicals": sorted(set(actual_canonicals) - expected),
        },
    }
    # Keep unannotated cases out of the quality applicability test.  The
    # annotations are optional case metadata, not output fields with a null
    # value that would look like an explicit empty annotation.
    if "expected_documents" in case:
        result["expected_documents"] = case["expected_documents"]
    if "mandatory_sources" in case:
        result["mandatory_sources"] = case["mandatory_sources"]
    return result


def _summary(cases: list[dict[str, Any]], *, glossary_mode: str = "on") -> dict[str, Any]:
    timing_names = ("prepare_ms", "embed_ms", "qdrant_ms", "postfilter_ms", "total_ms")
    timings = {
        name: [case["timings_ms"][name] for case in cases if name in case.get("timings_ms", {})]
        for name in timing_names
    }

    def percentile(values: list[float], p: float) -> float | None:
        if not values:
            return None
        return round(sorted(values)[min(len(values) - 1, int(len(values) * p))], 3)

    return {
        "case_count": len(cases),
        "acceptance_failures": [
            {
                "id": case["id"],
                "missing_canonicals": case["acceptance"]["missing_canonicals"],
                "forbidden_applied": case["acceptance"]["forbidden_applied"],
                "unexpected_canonicals": case["acceptance"].get("unexpected_canonicals", []),
            }
            for case in cases
            if glossary_mode == "on"
            and (case["acceptance"]["missing_canonicals"]
            or case["acceptance"]["forbidden_applied"]
            or case["acceptance"].get("unexpected_canonicals")
            )
        ],
        "timings_ms": {
            name: {
                "avg": round(statistics.mean(values), 3) if values else None,
                "p95": percentile(values, 0.95),
            }
            for name, values in timings.items()
        },
    }


def run(
    cases: list[dict[str, Any]] | None = None,
    *,
    glossary_mode: str = "off",
    settings: Any | None = None,
    vector_store: Any | None = None,
    embedder: Any | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    cases = cases or load_cases()
    vector_store = vector_store or VectorStore()
    embedder = embedder or Embedder()
    results = [
        run_case(
            case,
            glossary_mode=glossary_mode,
            settings=settings,
            vector_store=vector_store,
            embedder=embedder,
        )
        for case in cases
    ]
    return {
        "schema_version": 3,
        "glossary_mode": glossary_mode,
        "manifest": {
            "cases_sha256": hashlib.sha256(
                json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "case_ids": [case["id"] for case in cases],
        },
        "cases": results,
        "summary": _summary(results, glossary_mode=glossary_mode),
    }


def _case_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if "cases" in result:
        return {str(case["id"]): case for case in result["cases"]}
    # Read old probe-baseline.json files for diagnostics. They do not have
    # enough identity data for a full source-loss comparison, so use their
    # visible legacy title as a stable fallback only.
    return {
        key: {
            "id": key,
            "final_blocks": [
                {
                    "source": {
                        "doc_id": item.get("doc"),
                        "slug": None,
                        "chunk_index": None,
                    },
                    "title": item.get("title"),
                }
                for item in blocks
            ],
        }
        for key, blocks in result.items()
        if isinstance(blocks, list)
    }


def _block_sources(case: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for block in case.get("final_blocks", []):
        candidates = block.get("source_keys") or ([block["source"]] if block.get("source") else [])
        for source in candidates:
            key = (source.get("doc_id"), source.get("slug"), source.get("chunk_index"))
            if key not in seen:
                seen.add(key)
                sources.append(
                    {
                        "doc_id": source.get("doc_id"),
                        "slug": source.get("slug"),
                        "chunk_index": source.get("chunk_index"),
                    }
                )
    return sources


def _raw_sources(case: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = case.get("raw_candidates")
    if not candidates:
        return []
    sources: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for candidate in candidates:
        source = candidate.get("source") or {}
        normalized = {
            "doc_id": source.get("doc_id"),
            "slug": source.get("slug"),
            "chunk_index": source.get("chunk_index"),
        }
        key = (normalized["doc_id"], normalized["slug"], normalized["chunk_index"])
        if key not in seen:
            seen.add(key)
            sources.append(normalized)
    return sources


def _source_group(source: dict[str, Any]) -> tuple[Any, Any]:
    """Return the merged retrieval source key (document, chunk)."""
    return source.get("doc_id"), source.get("chunk_index")


def _group_representatives(sources: list[dict[str, Any]]) -> dict[tuple[Any, Any], dict[str, Any]]:
    return {_source_group(source): source for source in sources}


def _source_key(source: dict[str, Any]) -> tuple[Any, Any, Any]:
    return source.get("doc_id"), source.get("slug"), source.get("chunk_index")


def _validate_compatible(current: dict[str, Any], baseline: dict[str, Any]) -> None:
    if current.get("schema_version") != 3 or baseline.get("schema_version") != 3:
        raise ValueError("incompatible probe artifacts: schema_version must be 3")
    current_manifest = current.get("manifest") or {}
    baseline_manifest = baseline.get("manifest") or {}
    current_cases = _case_map(current)
    baseline_cases = _case_map(baseline)
    current_ids = current_manifest.get("case_ids") or sorted(current_cases)
    baseline_ids = baseline_manifest.get("case_ids") or sorted(baseline_cases)
    if current_ids != baseline_ids:
        raise ValueError("incompatible probe artifacts: case ids differ")
    if current_manifest.get("cases_sha256") and baseline_manifest.get("cases_sha256"):
        if current_manifest["cases_sha256"] != baseline_manifest["cases_sha256"]:
            raise ValueError("incompatible probe artifacts: cases manifest differs")
    for case_id in current_ids:
        before = baseline_cases[case_id]
        after = current_cases[case_id]
        for field in ("query", "api", "mode", "locale", "filters"):
            if field in before and field in after and before[field] != after[field]:
                raise ValueError(f"incompatible probe artifacts: {case_id}.{field} differs")


def compare_results(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Compare exact raw source identities; rank/order changes are informational.

    A source that leaves the final top-k but remains among raw candidates is a
    legal rank shift, not a full disappearance. The latter is the only loss
    that makes the CLI fail; final-only drops are retained for review.
    """
    _validate_compatible(current, baseline)
    current_cases = _case_map(current)
    baseline_cases = _case_map(baseline)
    losses: dict[str, list[dict[str, Any]]] = {}
    new_sources: dict[str, list[dict[str, Any]]] = {}
    final_only_losses: dict[str, list[dict[str, Any]]] = {}
    order_changes: list[str] = []
    group_losses: dict[str, list[dict[str, Any]]] = {}
    empty_raw_cases: list[str] = []
    for case_id in sorted(set(current_cases) | set(baseline_cases)):
        before = _raw_sources(baseline_cases.get(case_id, {}))
        after = _raw_sources(current_cases.get(case_id, {}))
        if not before or not after:
            empty_raw_cases.append(case_id)
        before_groups = _group_representatives(before)
        after_groups = _group_representatives(after)
        after_keys = set(after_groups)
        before_source_keys = {_source_key(source) for source in before}
        after_source_keys = {_source_key(source) for source in after}
        lost = [source for source in before if _source_key(source) not in after_source_keys]
        added = [source for source in after if _source_key(source) not in before_source_keys]
        group_lost = [before_groups[key] for key in before_groups if key not in after_keys]
        if lost:
            losses[case_id] = lost
        if group_lost:
            group_losses[case_id] = group_lost
        if added:
            new_sources[case_id] = added
        before_final = _block_sources(baseline_cases.get(case_id, {}))
        after_final = _block_sources(current_cases.get(case_id, {}))
        final_lost = [
            source
            for source in before_final
            if _source_key(source) not in {_source_key(x) for x in after_final}
            and _source_key(source) in after_source_keys
        ]
        if final_lost:
            final_only_losses[case_id] = final_lost
        if before_final == after_final and before != after:
            order_changes.append(case_id)
    return {
        "cases_compared": len(set(current_cases) & set(baseline_cases)),
        "full_source_losses": losses,
        "group_losses": group_losses,
        "new_sources": new_sources,
        "final_only_losses": final_only_losses,
        "order_changes_only": order_changes,
        "diagnostics": {"empty_raw_cases": sorted(empty_raw_cases)},
    }


def _collect_locales(vs, emb, q, tags, source_locales, include_unknown):
    """doc_id -> source_locale из БД для хитов под заданным фильтром языка."""
    plan = prepare_query(q, ui_locale="ru", enabled=False)
    vec = emb.embed(plan.dense_query)
    sv = to_sparse_vector(q, stopwords=get_stopwords(KIND_BM25))
    hits = vs.search_composite(
        dense_vec=vec,
        sparse_vec=sv,
        tags=tags,
        branches={"dense", "bm25"},
        top_k=get_settings().search_per_branch_top_k,
        source_locales=source_locales,
        include_unknown_source_locale=include_unknown,
    )
    lookup = build_doc_lookup(hits)
    return {did: (d or {}).get("source_locale") for did, d in lookup.items()}


def run_locale_checks() -> int:
    """Проверить инварианты фильтра по языку документа на реальном корпусе."""
    vs = VectorStore()
    emb = Embedder()
    failures = 0
    for probe in PROBES:
        q, tags = probe["q"], probe["tags"]
        ru = _collect_locales(vs, emb, q, tags, ["ru"], False)
        ruen = _collect_locales(vs, emb, q, tags, ["ru", "en"], False)
        unk = _collect_locales(vs, emb, q, tags, [], True)
        ru_unk = _collect_locales(vs, emb, q, tags, ["ru"], True)
        bad_ru = {did: loc for did, loc in ru.items() if loc != "ru"}
        bad_ruen = {did: loc for did, loc in ruen.items() if loc not in ("ru", "en")}
        bad_unk = {did: loc for did, loc in unk.items() if loc is not None}
        bad_ru_unk = {did: loc for did, loc in ru_unk.items() if loc not in ("ru", None)}
        label = q if not tags else f"{q} [tags={tags[0]}]"
        print(f"\n=== {label} ===")
        print(f"  ru: {len(ru)} док., нарушения: {bad_ru or 'нет'}")
        print(f"  ru+en: {len(ruen)} док., нарушения: {bad_ruen or 'нет'}")
        print(f"  unknown-only: {len(unk)} док., нарушения: {bad_unk or 'нет'}")
        print(f"  ru+unknown: {len(ru_unk)} док., нарушения: {bad_ru_unk or 'нет'}")
        failures += len(bad_ru) + len(bad_ruen) + len(bad_unk) + len(bad_ru_unk)
    print(f"\nИтог locale-проверки: {failures} нарушений")
    return failures


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true", help="Сохранить baseline; с существующим default-файлом нужен --output")
    parser.add_argument("--check-locales", action="store_true", help="Проверить инварианты source_locale")
    parser.add_argument("--glossary-mode", choices=("off", "on"), default="off")
    parser.add_argument("--cases", type=Path, help="JSON-файл размеченных кейсов")
    parser.add_argument("--all-modes", action="store_true", help="Развернуть каждый кейс в dense/bm25/hybrid")
    parser.add_argument("--all-apis", action="store_true", help="Развернуть каждый кейс в search/chat")
    parser.add_argument("--output", type=Path, help="JSON-файл полного результата")
    parser.add_argument("--compare", type=Path, help="Результат предыдущего off/on-прогона")
    args = parser.parse_args(argv)

    if args.check_locales:
        return 1 if run_locale_checks() else 0

    cases = load_cases(args.cases)
    if args.all_modes or args.all_apis:
        cases = expand_cases(
            cases,
            modes=("dense", "bm25", "hybrid") if args.all_modes else ("hybrid",),
            apis=("search", "chat") if args.all_apis else ("chat",),
        )
    result = run(cases, glossary_mode=args.glossary_mode)
    output = args.output
    if args.baseline and output is None:
        output = Path(__file__).resolve().parents[2] / "tests" / "artifacts" / "stage8" / "probe-baseline.json"
        if output.exists():
            parser.error(f"{output} exists; copy it or pass --output explicitly")
    if output:
        _write_json(output, result)
        print("result saved:", output, "cases:", len(result["cases"]))

    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    exit_code = 1 if result["summary"]["acceptance_failures"] else 0
    if args.compare:
        baseline = json.loads(args.compare.read_text(encoding="utf-8"))
        comparison = compare_results(result, baseline)
        print("comparison:")
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
        if comparison["full_source_losses"]:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
