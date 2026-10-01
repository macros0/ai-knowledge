"""Evaluate saved synthetic HTTP runs against unchanged p95/RSS gates."""

import argparse
import json
from pathlib import Path

BACKEND_LOSS_KEYS = ("dropped", "invalid", "expired_queue", "storage_errors", "drain_timeouts")
FRONTEND_LOSS_KEYS = ("dropped", "invalid", "expired_queue")


def loss_counters(value, keys=BACKEND_LOSS_KEYS):
    if not isinstance(value, dict) or any(type(value.get(key)) is not int or value[key] < 0 for key in keys):
        return None
    return {key: value[key] for key in keys}


def analyze(normal, *, bundle=None):
    if bundle is not None and (not normal.get("image_ids") or not bundle.get("image_ids")
                               or normal["image_ids"] != bundle["image_ids"]):
        raise ValueError("Cannot compare bundles across unknown or different image IDs")
    rows = normal["rows"]
    baselines = {(r["corpus_size"], r["concurrency"], r["repeat"]): r
                 for r in rows if r["mode"] == "baseline"}
    if bundle is not None:
        baselines.update({(r["corpus_size"], r["concurrency"], r["repeat"]): r
                          for r in bundle["rows"] if r["mode"] == "baseline" and r.get("bundle") is None})
    decisions = []
    for row in (bundle["rows"] if bundle is not None else rows):
        if ((row["mode"] == "baseline" and bundle is None)
                or (bundle is not None and row.get("bundle") is None)):
            continue
        key = row["corpus_size"], row["concurrency"], row["repeat"]
        baseline = baselines[key]
        with_zip = bundle is not None
        p95_limit = 1.20 if with_zip else 1.10
        backend_growth = row["peak_rss_bytes"]["backend"] - baseline["peak_rss_bytes"]["backend"]
        frontend_growth = row["peak_rss_bytes"]["frontend"] - baseline["peak_rss_bytes"]["frontend"]
        losses = loss_counters(row.get("recorder_delta"))
        frontend_losses = loss_counters(row.get("frontend_delta"), FRONTEND_LOSS_KEYS)
        phase_samples = row.get("bundle", {}).get("phase_latencies", {}) if with_zip else {}
        decisions.append({
            "corpus_size": row["corpus_size"], "concurrency": row["concurrency"],
            "repeat": row["repeat"], "mode": row["mode"], "bundle": with_zip,
            "baseline_p95_ms": baseline["p95_ms"], "measured_p95_ms": row["p95_ms"],
            "p95_delta_ms": row["p95_ms"] - baseline["p95_ms"],
            "p95_overhead_pct": 100 * (row["p95_ms"] / baseline["p95_ms"] - 1),
            "p95_pass": row["p95_ms"] <= baseline["p95_ms"] * p95_limit,
            "backend_rss_growth_bytes": backend_growth,
            "frontend_rss_growth_bytes": frontend_growth,
            "rss_pass": backend_growth <= 128 * 1048576 and frontend_growth <= 32 * 1048576,
            "duration_requests_pass": row["warmup_seconds"] >= 30
                                      and row["elapsed_seconds"] >= 60
                                      and row["requests"] >= 1000,
            "backend_losses": losses, "frontend_losses": frontend_losses,
            "losses_pass": losses is not None and not any(losses.values()) and frontend_losses is not None
                           and not any(frontend_losses.values()),
            "response_equality_pass": row.get("response_equality_pass") is True
                                      and row.get("response_fingerprints") == baseline.get("response_fingerprints"),
            "capture_counts_pass": row["mode"] == "baseline" or row.get("capture_counts_pass") is True,
            "phase_distributions_pass": not with_zip or all(
                phase_samples.get(name, {}).get("requests", 0) > 0 for name in ("prepare", "zip")),
            "zip_overlap_pass": (not with_zip or (row["bundle"]["status"] == "ready"
                                  and row["bundle"]["size_bytes"] > 0
                                  and row["bundle"]["observed_building"]
                                  and row["bundle"]["overlapping_requests"] > 0)),
        })
    return {"schema_version": 1, "decisions": decisions,
            "all_p95_pass": bool(decisions) and all(item["p95_pass"] for item in decisions),
            "all_rss_pass": bool(decisions) and all(item["rss_pass"] for item in decisions),
            "all_duration_requests_pass": bool(decisions) and all(
                item["duration_requests_pass"] for item in decisions),
            "all_losses_pass": bool(decisions) and all(item["losses_pass"] for item in decisions),
            "all_zip_overlap_pass": bool(decisions) and all(item["zip_overlap_pass"]
                                                       for item in decisions),
            "all_response_equality_pass": bool(decisions) and all(
                item["response_equality_pass"] for item in decisions),
            "all_capture_counts_pass": bool(decisions) and all(
                item["capture_counts_pass"] for item in decisions),
            "all_phase_distributions_pass": bool(decisions) and all(
                item["phase_distributions_pass"] for item in decisions)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--normal", type=Path, required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    normal = json.loads(args.normal.read_text())
    bundle = json.loads(args.bundle.read_text()) if args.bundle else None
    result = analyze(normal, bundle=bundle)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "decisions"},
                     sort_keys=True))
