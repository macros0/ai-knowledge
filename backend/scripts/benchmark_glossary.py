"""Benchmark the query-side glossary on an isolated database.

The script intentionally measures only the glossary/query preparation stages.
Dense embedding and Qdrant timings belong to the corpus probe and are not
silently represented as glossary timings here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from statistics import mean, median, quantiles
from time import perf_counter
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def summarize_samples(samples: Iterable[float]) -> dict[str, float | int]:
    values = list(samples)
    if not values:
        raise ValueError("at least one sample is required")
    p95 = quantiles(values, n=100, method="inclusive")[94] if len(values) > 1 else values[0]
    return {
        "count": len(values),
        "avg_ms": round(mean(values) * 1000, 3),
        "p50_ms": round(median(values) * 1000, 3),
        "p95_ms": round(p95 * 1000, 3),
    }


def build_benchmark_rows(*, term_count: int, alias_count: int) -> tuple[list[dict], list[dict]]:
    """Build exactly ``term_count`` terms and ``alias_count`` aliases.

    Each term receives its canonical alias first. Remaining aliases are spread
    deterministically across terms, which keeps the fixture useful when the
    requested counts are not an exact multiple.
    """
    if term_count < 1 or alias_count < term_count:
        raise ValueError("alias_count must be at least term_count and both must be positive")

    terms: list[dict] = []
    aliases: list[dict] = []
    extra = alias_count - term_count
    base, remainder = divmod(extra, term_count)
    for term_index in range(term_count):
        canonical = f"BMTERM{term_index:04d}"
        terms.append(
            {
                "canonical": canonical,
                "kind": "business_term",
                "original_name": f"Benchmark term {term_index:04d}",
                "original_description": "Isolated Stage 8 glossary benchmark fixture.",
                "canonical_locale": "en",
                "enabled": True,
                "version": 1,
                "source_revision": 1,
            }
        )
        aliases.append(
            {
                "term_index": term_index,
                "alias": canonical,
                "normalized_alias": canonical.lower(),
                "locale": "en",
                "auto_expand": True,
                "search_enabled": True,
            }
        )
        for alias_index in range(base + (1 if term_index < remainder else 0)):
            alias = f"benchmark term {term_index:04d} alias {alias_index:02d}"
            aliases.append(
                {
                    "term_index": term_index,
                    "alias": alias,
                    "normalized_alias": alias.lower(),
                    "locale": "en",
                    "auto_expand": True,
                    "search_enabled": True,
                }
            )
    assert len(terms) == term_count
    assert len(aliases) == alias_count
    return terms, aliases


def _populate_database(engine, *, term_count: int, alias_count: int) -> dict[str, int]:
    from sqlalchemy import func, select

    from app.db import models  # noqa: F401  (register all tables)
    from app.db.base import Base
    from app.db.models import DomainTerm, DomainTermAlias

    Base.metadata.create_all(engine)
    terms, aliases = build_benchmark_rows(term_count=term_count, alias_count=alias_count)
    with engine.begin() as connection:
        existing_terms = connection.execute(select(func.count()).select_from(DomainTerm)).scalar_one()
        existing_aliases = connection.execute(select(func.count()).select_from(DomainTermAlias)).scalar_one()
        if existing_terms or existing_aliases:
            raise RuntimeError(
                "benchmark database must have empty domain_terms/domain_term_aliases; "
                f"found {existing_terms} terms and {existing_aliases} aliases"
            )
        result = connection.execute(
            DomainTerm.__table__.insert().returning(DomainTerm.id, DomainTerm.canonical),
            terms,
        )
        term_ids = {canonical: term_id for term_id, canonical in result}
        alias_rows = []
        for item in aliases:
            canonical = terms[item["term_index"]]["canonical"]
            alias_rows.append(
                {
                    "term_id": term_ids[canonical],
                    "alias": item["alias"],
                    "normalized_alias": item["normalized_alias"],
                    "locale": item["locale"],
                    "auto_expand": item["auto_expand"],
                    "search_enabled": item["search_enabled"],
                }
            )
        connection.execute(DomainTermAlias.__table__.insert(), alias_rows)
    return {"terms": term_count, "aliases": alias_count}


def _measure_once(query: str, *, enabled: bool, settings, engine) -> dict:
    from sqlalchemy import event

    from app.services.glossary.expansion import (
        _collect_candidates,
        _group_candidates,
        load_glossary_snapshot,
        prepare_query,
    )
    from app.services.glossary.query_sparse import build_query_sparse

    sql_count = 0

    def before_cursor_execute(*_args):
        nonlocal sql_count
        sql_count += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        started = perf_counter()
        snapshot = load_glossary_snapshot() if enabled else ()
        snapshot_seconds = perf_counter() - started

        started = perf_counter()
        groups = _group_candidates(_collect_candidates(snapshot, query)) if enabled else ()
        matcher_seconds = perf_counter() - started

        started = perf_counter()
        plan = prepare_query(query, ui_locale="en", enabled=enabled, settings=settings)
        prepare_seconds = perf_counter() - started

        started = perf_counter()
        sparse = build_query_sparse(plan, stopwords=set(), settings=settings)
        sparse_seconds = perf_counter() - started
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    return {
        "snapshot_ms": round(snapshot_seconds * 1000, 3),
        "matcher_ms": round(matcher_seconds * 1000, 3),
        "prepare_ms": round(prepare_seconds * 1000, 3),
        "query_sparse_ms": round(sparse_seconds * 1000, 3),
        "sql_statements": sql_count,
        "matched_groups": len(groups),
        "status": plan.status,
        "sparse_dimensions": len(sparse.indices),
    }


def run_benchmark(*, database_url: str, output: Path, term_count: int, alias_count: int) -> dict:
    os.environ["DATABASE_URL"] = database_url
    os.environ["DATABASE_URL_DEV"] = ""

    from app.config import get_settings
    from app.db.session import get_engine

    get_settings.cache_clear()
    engine = get_engine()
    counts = _populate_database(engine, term_count=term_count, alias_count=alias_count)
    settings = get_settings()
    queries = ["benchmark term 0000 alias 00", "phrase with no registered glossary term"]

    for _ in range(2):
        for enabled in (False, True):
            for query in queries:
                _measure_once(query, enabled=enabled, settings=settings, engine=engine)

    samples: dict[str, dict[str, list[float]]] = {"off": {}, "on": {}}
    raw: list[dict] = []
    for pair in range(5):
        order = (False, True) if pair % 2 == 0 else (True, False)
        for enabled in order:
            label = "on" if enabled else "off"
            for query in queries:
                measured = _measure_once(query, enabled=enabled, settings=settings, engine=engine)
                raw.append({"pair": pair + 1, "enabled": enabled, "query": query, **measured})
                for key in ("snapshot_ms", "matcher_ms", "prepare_ms", "query_sparse_ms"):
                    samples[label].setdefault(key, []).append(measured[key] / 1000)

    result = {
        "schema_version": 1,
        "scope": "glossary_query_preparation_only",
        "database_fixture": counts,
        "warmup_passes": 2,
        "measured_pairs": 5,
        "queries_per_pass": len(queries),
        "summary": {
            label: {key: summarize_samples(values) for key, values in fields.items()}
            for label, fields in samples.items()
        },
        "raw": raw,
        "note": "Dense embedding and Qdrant are intentionally excluded; corpus probe measures those stages separately.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--terms", type=int, default=1000)
    parser.add_argument("--aliases", type=int, default=10000)
    args = parser.parse_args()
    result = run_benchmark(
        database_url=args.database_url,
        output=args.output,
        term_count=args.terms,
        alias_count=args.aliases,
    )
    print(json.dumps({key: value for key, value in result.items() if key != "raw"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
