from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from test_scripts import stage8_sla_validator


def _summary(*, chat_hybrid_p95: float = 11.321) -> dict:
    aggregate = [
        {
            "api": "search",
            "mode": "dense",
            "n_per_side": 210,
            "off_avg_ms": 50.0,
            "on_avg_ms": 52.0,
            "avg_delta_pct": 4.0,
            "off_p95_ms": 60.0,
            "on_p95_ms": 61.0,
            "p95_delta_pct": 1.667,
            "sla_pass": True,
        },
        {
            "api": "chat",
            "mode": "hybrid",
            "n_per_side": 210,
            "off_avg_ms": 45.0,
            "on_avg_ms": 48.0,
            "avg_delta_pct": 6.667,
            "off_p95_ms": 53.0,
            "on_p95_ms": 59.0,
            "p95_delta_pct": chat_hybrid_p95,
            "sla_pass": False,
        },
    ]
    return {"aggregate": aggregate}


def _recheck(*, failed_metrics: list[str] | None = None, sla_pass: bool = False) -> dict:
    return {
        "criterion": {
            "average_on_to_off_max_ratio": 1.1,
            "p95_on_to_off_max_ratio": 1.1,
        },
        "results": [
            {
                "api": "search",
                "mode": "dense",
                "n_per_side": 210,
                "off_avg_ms": 50.0,
                "on_avg_ms": 52.0,
                "avg_delta_pct": 4.0,
                "off_p95_ms": 60.0,
                "on_p95_ms": 61.0,
                "p95_delta_pct": 1.667,
                "sla_pass": True,
            },
            {
                "api": "chat",
                "mode": "hybrid",
                "n_per_side": 210,
                "off_avg_ms": 45.0,
                "on_avg_ms": 48.0,
                "avg_delta_pct": 6.667,
                "off_p95_ms": 53.0,
                "on_p95_ms": 59.0,
                "p95_delta_pct": 11.321,
                "sla_pass": sla_pass,
                **({"failed_metrics": failed_metrics} if failed_metrics is not None else {"failed_metrics": ["p95"]}),
            },
        ],
        "summary": {"passed_pairs": 1, "failed_pairs": 1, "status": "BLOCKED"},
    }


def test_validate_sla_accepts_strictly_consistent_blocked_artifact():
    result = stage8_sla_validator.validate_sla(_summary(), _recheck())

    assert result["status"] == "READY"
    assert result["errors"] == []
    assert result["sla_status"] == "BLOCKED"
    assert result["passed_pairs"] == 1
    assert result["failed_pairs"] == 1


def test_validate_sla_rejects_summary_that_ignores_p95_limit():
    summary = _summary(chat_hybrid_p95=9.0)
    summary["aggregate"][1]["sla_pass"] = True
    recheck = _recheck(sla_pass=True, failed_metrics=[])

    result = stage8_sla_validator.validate_sla(summary, recheck)

    assert result["status"] == "BLOCKED"
    assert "summary_sla_pass_mismatch:chat/hybrid" in result["errors"]
    assert "recheck_sla_pass_mismatch:chat/hybrid" in result["errors"]


def test_validate_sla_rejects_missing_failed_metric_and_value_drift():
    summary = _summary()
    recheck = _recheck(failed_metrics=[])
    recheck["results"][1]["on_p95_ms"] = 58.0

    result = stage8_sla_validator.validate_sla(summary, recheck)

    assert result["status"] == "BLOCKED"
    assert "failed_metrics_mismatch:chat/hybrid" in result["errors"]
    assert "value_mismatch:chat/hybrid:on_p95_ms" in result["errors"]


def test_validate_sla_rejects_empty_matrix():
    result = stage8_sla_validator.validate_sla(
        {"aggregate": []},
        {"results": [], "summary": {"passed_pairs": 0, "failed_pairs": 0, "status": "PASS"}},
    )

    assert result["status"] == "BLOCKED"
    assert "empty_matrix" in result["errors"]


def test_validate_sla_rejects_recheck_criterion_override():
    summary = _summary()
    recheck = _recheck(sla_pass=True, failed_metrics=[])
    recheck["criterion"] = {
        "average_on_to_off_max_ratio": 3.0,
        "p95_on_to_off_max_ratio": 3.0,
    }
    recheck["summary"] = {"passed_pairs": 2, "failed_pairs": 0, "status": "PASS"}

    result = stage8_sla_validator.validate_sla(summary, recheck)

    assert result["status"] == "BLOCKED"
    assert "criterion_mismatch:average" in result["errors"]
    assert "criterion_mismatch:p95" in result["errors"]


def test_validate_sla_rejects_reported_delta_drift():
    summary = _summary()
    recheck = _recheck()
    summary["aggregate"][1]["avg_delta_pct"] = 999.0
    recheck["results"][1]["avg_delta_pct"] = 999.0

    result = stage8_sla_validator.validate_sla(summary, recheck)

    assert result["status"] == "BLOCKED"
    assert "delta_mismatch:chat/hybrid:avg" in result["errors"]


def test_validate_sla_can_require_complete_matrix():
    result = stage8_sla_validator.validate_sla(
        _summary(), _recheck(), require_complete_matrix=True
    )

    assert result["status"] == "BLOCKED"
    assert "missing_expected_pair:chat/dense" in result["errors"]


def test_validate_sla_rejects_non_positive_sample_and_latency_values():
    summary = _summary()
    recheck = _recheck()
    for row in summary["aggregate"] + recheck["results"]:
        row["n_per_side"] = 0
        row["off_avg_ms"] = -1.0
        row["on_avg_ms"] = -1.0
        row["off_p95_ms"] = -1.0
        row["on_p95_ms"] = -1.0

    result = stage8_sla_validator.validate_sla(summary, recheck)

    assert result["status"] == "BLOCKED"
    assert "invalid_measurement:search/dense:n_per_side" in result["errors"]
    assert "invalid_measurement:chat/hybrid:off_avg_ms" in result["errors"]


def test_validate_sla_rejects_incomplete_protocol_when_required():
    summary = _summary()
    summary.update(
        warmup_passes=1,
        measured_pairs=4,
        observations_per_side=1,
        acceptance_failures=["synthetic failure"],
    )

    result = stage8_sla_validator.validate_sla(
        summary,
        _recheck(),
        require_protocol=True,
    )

    assert result["status"] == "BLOCKED"
    assert "protocol_mismatch:warmup_passes" in result["errors"]
    assert "protocol_mismatch:measured_pairs" in result["errors"]
    assert "protocol_mismatch:observations_per_side" in result["errors"]
    assert "protocol_acceptance_failures" in result["errors"]
