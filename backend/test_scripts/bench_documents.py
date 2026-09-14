"""Бенчмарк эндпоинта GET /api/documents (список документов).

Меряет ровно тот код, что исполняет эндпоинт (без HTTP-overhead, который
константен и не зависит от N): registry.list() + сортировка по created_at +
сериализация DocumentListOut. Для каждого N ресидит БД, делает прогрев и
R прогонов, выводя p50/p95 латентности и медианный размер payload.

Запуск (из backend/, требуется поднятый Postgres):
    python test_scripts/bench_documents.py --counts 100,500,1000 --runs 10
    python test_scripts/bench_documents.py --csv ..\tests\artifacts\documents\bench.csv
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.schemas import DocumentListOut
from app.services.registry import DocumentRegistry

from scripts.seed_documents import clean, seed


def _percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def measure(scope: str, uploaded_by: str | None) -> tuple[float, float, int, int]:
    """Один прогон: list() + sort + сериализация. Возвращает (total_ms, sql_ms, bytes, n)."""
    registry = DocumentRegistry()
    t0 = time.perf_counter()
    docs = registry.list(uploaded_by=uploaded_by)
    t1 = time.perf_counter()
    docs.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    payload = DocumentListOut(documents=docs).model_dump_json()
    t2 = time.perf_counter()
    return (t2 - t0) * 1000.0, (t1 - t0) * 1000.0, len(payload.encode("utf-8")), len(docs)


def run(count: int, owner: str, scope: str, runs: int) -> dict:
    clean()
    seed(count, owner)
    uploaded_by = owner if scope == "mine" else None
    # Прогрев: холодный кэш БД не должен попадать в p95.
    measure(scope, uploaded_by)

    totals: list[float] = []
    sqls: list[float] = []
    sizes: list[int] = []
    n = 0
    for _ in range(runs):
        total, sql, size, n = measure(scope, uploaded_by)
        totals.append(total)
        sqls.append(sql)
        sizes.append(size)

    return {
        "n": n,
        "count": count,
        "scope": scope,
        "p50_ms": round(statistics.median(totals), 2),
        "p95_ms": round(_percentile(totals, 0.95), 2),
        "sql_ms": round(statistics.median(sqls), 2),
        "payload_bytes": int(statistics.median(sizes)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Бенчмарк списка документов.")
    parser.add_argument("--counts", type=str, default="100,500,1000", help="N через запятую")
    parser.add_argument("--runs", type=int, default=10, help="Прогонов на каждое N")
    parser.add_argument("--owner", type=str, default="loadtest.owner")
    parser.add_argument("--scopes", type=str, default="all,mine")
    parser.add_argument("--csv", type=str, default=None, help="Путь к CSV-отчёту")
    args = parser.parse_args()

    counts = [int(x) for x in args.counts.split(",")]
    scopes = args.scopes.split(",")

    rows: list[dict] = []
    header = f"{'N':>5}  {'scope':<4}  {'p50 ms':>8}  {'p95 ms':>8}  {'sql ms':>7}  {'payload B':>9}"
    print(header)
    print("-" * len(header))
    try:
        for count in counts:
            for scope in scopes:
                row = run(count, args.owner, scope, args.runs)
                rows.append(row)
                print(
                    f"{row['n']:>5}  {scope:<4}  {row['p50_ms']:>8}  "
                    f"{row['p95_ms']:>8}  {row['sql_ms']:>7}  {row['payload_bytes']:>9}"
                )
    finally:
        removed = clean()
        if removed:
            print(f"\nУдалено сиднутых документов после замера: {removed}")

    if args.csv:
        fieldnames = ["n", "count", "scope", "p50_ms", "p95_ms", "sql_ms", "payload_bytes"]
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"CSV: {args.csv}")


if __name__ == "__main__":
    main()
