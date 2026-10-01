"""Strict numeric memory analysis; never convert a diagnostic run into SLA acceptance."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
from statistics import median

BASE = {"pid", "at_unix_ms", "uptime_ms", "birth_unix_ms", "rss_bytes", "heap_used_bytes",
        "heap_total_bytes", "external_bytes", "array_buffers_bytes", "physical_heap_bytes", "heap_limit_bytes",
        "native_contexts", "detached_contexts", "minor_count", "major_count", "other_count",
        "minor_ms", "major_ms", "other_ms"}


def analyze(data):
    if not data.get("complete") or not data.get("diagnostic_only") or len(data.get("rows", [])) != 8:
        raise ValueError("Incomplete matched memory experiment")
    modes = Counter((r["capture"], r["zip"]) for r in data["rows"])
    if modes != Counter({("off", False): 2, ("standard", False): 2, ("off", True): 2, ("standard", True): 2}):
        raise ValueError("Missing matched modes")
    cases = []
    for row in data["rows"]:
        start, end, idle = (row[key] for key in ("measurement_start_age", "measurement_end_age", "idle_end_age"))
        if not 40 <= start <= 42 or not 60 <= end - start <= 62 or not 60 <= idle - end <= 63 or row["requests"] < 1000:
            raise ValueError("Incomplete or unmatched process-age window")
        for sample in row["node_samples"]:
            expected = {"role", "type"} | BASE | ({"gc_kind", "gc_duration_ms"} if sample["type"] == "gc" else set())
            if (set(sample) != expected or sample["role"] not in {"next", "launcher"}
                    or sample["type"] not in {"start", "sample", "gc", "stop"}
                    or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                           for key, value in sample.items() if key not in {"role", "type"})):
                raise ValueError("Unknown or nonnumeric observer fields")
        next_rows = [r for r in row["node_samples"] if r["role"] == "next"]
        measured = [r for r in next_rows if 10 <= r["uptime_ms"] / 1000 <= end]
        quiet = [r for r in next_rows if idle - 5 <= r["uptime_ms"] / 1000 <= idle]
        if not measured or not quiet or {r["role"] for r in row["node_samples"]} != {"next", "launcher"}:
            raise ValueError("Missing process/idle observations")
        gc_idle = [r for r in next_rows if r["type"] == "gc" and r["gc_kind"] == 4 and end <= r["uptime_ms"] / 1000 <= idle]
        linux = [r for r in row["linux_samples"] if 10 <= r["age_seconds"] <= end]
        to_mib = lambda value: round(value / 1048576, 3)
        cases.append({"case": row["case"], "capture": row["capture"], "zip": row["zip"], "requests": row["requests"],
                      "p95_ms": row["p95_ms"], "next_peak_rss_mib": to_mib(max(r["rss_bytes"] for r in measured)),
                      "tree_peak_rss_mib": to_mib(max(r["tree_rss_bytes"] for r in linux)),
                      "tree_peak_pss_mib": to_mib(max(sum(p["Pss"] for p in r["processes"]) for r in linux)),
                      "private_peak_mib": to_mib(max(sum(p["Private_Clean"] + p["Private_Dirty"] for p in r["processes"]) for r in linux)),
                      "next_peak_heap_used_mib": to_mib(max(r["heap_used_bytes"] for r in measured)),
                      "next_peak_heap_total_mib": to_mib(max(r["heap_total_bytes"] for r in measured)),
                      "next_peak_external_mib": to_mib(max(r["external_bytes"] for r in measured)),
                      "idle_rss_mib": to_mib(median(r["rss_bytes"] for r in quiet)),
                      "idle_heap_used_mib": to_mib(median(r["heap_used_bytes"] for r in quiet)),
                      "idle_heap_total_mib": to_mib(median(r["heap_total_bytes"] for r in quiet)),
                      "idle_major_gc_events": len(gc_idle),
                      "post_major_observed_heap_mib": to_mib(gc_idle[-1]["heap_used_bytes"]) if gc_idle else None,
                      "detached_contexts_max": max(r["detached_contexts"] for r in next_rows),
                      "native_contexts_max": max(r["native_contexts"] for r in next_rows),
                      "minor_gc_events": sum(r["type"] == "gc" and r["gc_kind"] == 1 for r in next_rows),
                      "major_gc_events": sum(r["type"] == "gc" and r["gc_kind"] == 4 for r in next_rows),
                      "bundle_bytes": (row["bundle"] or {}).get("size_bytes"),
                      "bundle_events": (row["bundle"] or {}).get("manifest_events")})
    comparisons = []
    for repeat in (cases[:4], cases[4:]):
        baseline = next(r for r in repeat if r["capture"] == "off" and not r["zip"])
        for row in repeat:
            comparisons.append({"case": row["case"], "baseline_case": baseline["case"],
                                "tree_rss_delta_mib": round(row["tree_peak_rss_mib"] - baseline["tree_peak_rss_mib"], 3),
                                "idle_heap_delta_mib": round(row["idle_heap_used_mib"] - baseline["idle_heap_used_mib"], 3)})
    return {"diagnostic_only": True, "complete": True, "old_acceptance": "failed_unchanged",
            "requests": sum(r["requests"] for r in cases), "cases": cases, "comparisons": comparisons}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Preserve existing analysis")
    result = analyze(json.loads(args.input.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + "\n", newline="\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
