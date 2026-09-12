"""Validate a Stage 8 performance summary against its strict SLA recheck."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


_MEASURE_FIELDS = (
    "n_per_side",
    "off_avg_ms",
    "on_avg_ms",
    "avg_delta_pct",
    "off_p95_ms",
    "on_p95_ms",
    "p95_delta_pct",
)
_EXPECTED_AVERAGE_MAX_RATIO = 1.1
_EXPECTED_P95_MAX_RATIO = 1.1
_DELTA_TOLERANCE_PCT = 0.2
_EXPECTED_WARMUP_PASSES = 2
_EXPECTED_MEASURED_PAIRS = 5
_EXPECTED_OBSERVATIONS_PER_SIDE = 210
_EXPECTED_PAIRS = {
    "search/dense",
    "search/bm25",
    "search/hybrid",
    "chat/dense",
    "chat/bm25",
    "chat/hybrid",
}


def _pair(row: dict[str, Any]) -> str:
    return f"{row.get('api')}/{row.get('mode')}"


def _strict_failures(
    row: dict[str, Any], *, average_max_ratio: float, p95_max_ratio: float
) -> list[str]:
    failures: list[str] = []
    off_avg = row.get("off_avg_ms")
    on_avg = row.get("on_avg_ms")
    off_p95 = row.get("off_p95_ms")
    on_p95 = row.get("on_p95_ms")
    if (
        not _is_positive_number(off_avg)
        or not _is_positive_number(on_avg)
        or on_avg > off_avg * average_max_ratio
    ):
        failures.append("average")
    if (
        not _is_positive_number(off_p95)
        or not _is_positive_number(on_p95)
        or on_p95 > off_p95 * p95_max_ratio
    ):
        failures.append("p95")
    return failures


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_positive_number(value: Any) -> bool:
    return _is_finite_number(value) and value > 0


def _measurement_errors(row: dict[str, Any], pair: str) -> list[str]:
    errors: list[str] = []
    sample_size = row.get("n_per_side")
    if not isinstance(sample_size, int) or isinstance(sample_size, bool) or sample_size <= 0:
        errors.append(f"invalid_measurement:{pair}:n_per_side")
    for field in ("off_avg_ms", "on_avg_ms", "off_p95_ms", "on_p95_ms"):
        if not _is_positive_number(row.get(field)):
            errors.append(f"invalid_measurement:{pair}:{field}")
    for field in ("avg_delta_pct", "p95_delta_pct"):
        if not _is_finite_number(row.get(field)):
            errors.append(f"invalid_measurement:{pair}:{field}")
    return errors


def _reported_delta(row: dict[str, Any], prefix: str) -> float | None:
    off = row.get(f"off_{prefix}_ms")
    on = row.get(f"on_{prefix}_ms")
    if not isinstance(off, (int, float)) or not isinstance(on, (int, float)) or off == 0:
        return None
    return (on / off - 1) * 100


def _protocol_errors(summary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected = {
        "warmup_passes": _EXPECTED_WARMUP_PASSES,
        "measured_pairs": _EXPECTED_MEASURED_PAIRS,
        "observations_per_side": _EXPECTED_OBSERVATIONS_PER_SIDE,
    }
    for field, value in expected.items():
        actual = summary.get(field)
        if not isinstance(actual, int) or isinstance(actual, bool) or actual != value:
            errors.append(f"protocol_mismatch:{field}")
    acceptance_failures = summary.get("acceptance_failures")
    if not isinstance(acceptance_failures, list) or acceptance_failures:
        errors.append("protocol_acceptance_failures")
    return errors


def validate_sla(
    summary: dict[str, Any],
    recheck: dict[str, Any],
    *,
    require_complete_matrix: bool = False,
    require_protocol: bool = False,
) -> dict[str, Any]:
    """Check summary/recheck against the authoritative Stage 8 strict ratios."""
    criterion = recheck.get("criterion") or {}
    average_max_ratio = _EXPECTED_AVERAGE_MAX_RATIO
    p95_max_ratio = _EXPECTED_P95_MAX_RATIO
    errors: list[str] = []
    if require_protocol:
        errors.extend(_protocol_errors(summary))
    if criterion.get("average_on_to_off_max_ratio") != average_max_ratio:
        errors.append("criterion_mismatch:average")
    if criterion.get("p95_on_to_off_max_ratio") != p95_max_ratio:
        errors.append("criterion_mismatch:p95")
    summary_rows = summary.get("aggregate") or []
    recheck_rows = recheck.get("results") or []
    if not summary_rows and not recheck_rows:
        errors.append("empty_matrix")
    summary_pairs = [_pair(row) for row in summary_rows]
    recheck_pairs = [_pair(row) for row in recheck_rows]
    for pair in sorted({pair for pair in summary_pairs if summary_pairs.count(pair) > 1}):
        errors.append(f"duplicate_summary_pair:{pair}")
    for pair in sorted({pair for pair in recheck_pairs if recheck_pairs.count(pair) > 1}):
        errors.append(f"duplicate_recheck_pair:{pair}")
    summary_by_pair = {_pair(row): row for row in summary_rows}
    recheck_by_pair = {_pair(row): row for row in recheck_rows}
    pairs = sorted(set(summary_by_pair) | set(recheck_by_pair))
    if require_complete_matrix:
        for pair in sorted(_EXPECTED_PAIRS - set(pairs)):
            errors.append(f"missing_expected_pair:{pair}")
    passed_pairs = failed_pairs = 0

    for pair in pairs:
        source = summary_by_pair.get(pair)
        target = recheck_by_pair.get(pair)
        if source is None:
            errors.append(f"summary_pair_missing:{pair}")
            continue
        if target is None:
            errors.append(f"recheck_pair_missing:{pair}")
            continue

        errors.extend(_measurement_errors(source, pair))
        for field in _MEASURE_FIELDS:
            if source.get(field) != target.get(field):
                errors.append(f"value_mismatch:{pair}:{field}")

        for metric, field in (("avg", "avg_delta_pct"), ("p95", "p95_delta_pct")):
            derived = _reported_delta(source, metric)
            reported = source.get(field)
            if derived is not None and isinstance(reported, (int, float)):
                if abs(float(reported) - derived) > _DELTA_TOLERANCE_PCT:
                    errors.append(f"delta_mismatch:{pair}:{metric}")

        expected_failures = _strict_failures(
            source,
            average_max_ratio=average_max_ratio,
            p95_max_ratio=p95_max_ratio,
        )
        expected_pass = not expected_failures
        if expected_pass:
            passed_pairs += 1
        else:
            failed_pairs += 1

        actual_failures = list(target.get("failed_metrics") or [])
        if actual_failures != expected_failures:
            errors.append(f"failed_metrics_mismatch:{pair}")
        if bool(source.get("sla_pass")) != expected_pass:
            errors.append(f"summary_sla_pass_mismatch:{pair}")
        if bool(target.get("sla_pass")) != expected_pass:
            errors.append(f"recheck_sla_pass_mismatch:{pair}")

    expected_summary = {
        "passed_pairs": passed_pairs,
        "failed_pairs": failed_pairs,
        "status": "PASS" if failed_pairs == 0 else "BLOCKED",
    }
    actual_summary = recheck.get("summary") or {}
    for field, expected in expected_summary.items():
        if actual_summary.get(field) != expected:
            errors.append(f"recheck_summary_mismatch:{field}")

    return {
        "status": "READY" if not errors else "BLOCKED",
        "sla_status": expected_summary["status"],
        "criterion": {
            "average_on_to_off_max_ratio": average_max_ratio,
            "p95_on_to_off_max_ratio": p95_max_ratio,
        },
        "pair_count": len(pairs),
        "passed_pairs": passed_pairs,
        "failed_pairs": failed_pairs,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--summary",
        type=Path,
        default=script_dir / "probe-stage8-bounded-hydration-summary-20260912.json",
    )
    parser.add_argument(
        "--recheck",
        type=Path,
        default=script_dir / "probe-stage8-bounded-hydration-sla-recheck-20260912.json",
    )
    args = parser.parse_args(argv)
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    recheck = json.loads(args.recheck.read_text(encoding="utf-8"))
    result = validate_sla(
        summary,
        recheck,
        require_complete_matrix=True,
        require_protocol=True,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
