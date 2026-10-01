"""Numerical gates for private controls; their RSS never replaces live-tree RSS."""
import math

if __package__:
    from .analyze_diagnostics_matrix import loss_counters
else:
    from analyze_diagnostics_matrix import loss_counters


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def duration(row, *, smoke=False):
    return (numeric(row.get("elapsed_seconds")) and row["elapsed_seconds"] >= (0 if smoke else 60)
            and type(row.get("requests")) is int and row["requests"] >= (1 if smoke else 1000))


def analyze_testclient(data, *, smoke=False, reference=False):
    rows = [data[name] for name in ("baseline", "capture", "capture_build")]
    baseline = rows[0]
    losses = loss_counters(data.get("recorder"))
    timed = all(numeric(row.get("p95_ms")) and row["p95_ms"] > 0 for row in rows)
    rss = all(numeric(row.get("peak_process_tree_rss_bytes")) for row in rows)
    return {"scope": "private TestClient control", "performance_acceptance": not smoke and not reference,
            "all_duration_requests_pass": numeric(data.get("warmup_seconds"))
                and data["warmup_seconds"] >= (0 if smoke else 30)
                and all(duration(row, smoke=smoke) for row in rows),
            "all_p95_pass": timed and (smoke or reference or (
                rows[1]["p95_ms"] <= baseline["p95_ms"] * 1.10
                and rows[2]["p95_ms"] <= baseline["p95_ms"] * 1.20)),
            "all_private_rss_pass": rss and (smoke or reference or all(
                row["peak_process_tree_rss_bytes"] - baseline["peak_process_tree_rss_bytes"] <= 128 * 1048576
                for row in rows[1:])),
            "all_loss_counters_known_pass": losses is not None,
            "all_losses_pass": losses is not None and not any(losses.values()),
            "observed_losses": losses,
            "all_bundle_pass": data.get("bundles", {}).get("built") == 1
                and data["bundles"].get("failed") == 0
                and rows[2].get("requests_overlapping_build", 0) > 0}


def analyze_fixed(data, *, records, repeats, concurrencies, smoke=False, reference=False):
    rows = data["rows"]
    cases = [("baseline", False), ("baseline", True)]
    if not reference:
        cases += [("standard", True), ("detailed", True)]
    expected = {(repeat, concurrency, mode, zipped) for repeat in range(1, repeats + 1)
                for concurrency in concurrencies for mode, zipped in cases}
    actual = {(r["repeat"], r["concurrency"], r["mode"], r["with_zip"]) for r in rows}
    complete = actual == expected and len(rows) == len(expected)
    baselines = {(r["repeat"], r["concurrency"]): r for r in rows if not r["with_zip"]}
    hashes = {row.get("input_sha256") for row in rows}
    intact = len(hashes) == 1 and all(isinstance(h, str) and len(h) == 64 for h in hashes)
    p95, rss, bundles = [], [], []
    for row in rows:
        baseline = baselines.get((row["repeat"], row["concurrency"]))
        p95.append(baseline is not None and numeric(row.get("p95_ms"))
                   and numeric(baseline.get("p95_ms")) and baseline["p95_ms"] > 0
                   and (smoke or reference or row["p95_ms"] <= baseline["p95_ms"] * 1.20))
        rss.append(baseline is not None and numeric(row.get("peak_process_tree_rss_bytes"))
                   and numeric(baseline.get("peak_process_tree_rss_bytes"))
                   and (smoke or reference or row["peak_process_tree_rss_bytes"]
                        - baseline["peak_process_tree_rss_bytes"] <= 128 * 1048576))
        if row["with_zip"]:
            bundle = row.get("bundle") or {}
            bundles.append(bundle.get("status") == "ready" and bundle.get("crc_pass") is True
                           and bundle.get("events") == records
                           and bundle.get("input_sha256") == row.get("input_sha256")
                           and numeric(bundle.get("output_bytes")) and bundle["output_bytes"] > 0
                           and all(row.get("phase_latencies", {}).get(phase, {}).get("requests", 0) > 0
                                   for phase in ("prepare", "zip")))
    return {"scope": "private fixed-input control; live-tree RSS is assessed by HTTP matrix",
            "performance_acceptance": not smoke and not reference,
            "all_complete_pass": complete,
            "all_duration_requests_pass": numeric(data.get("warmup_seconds"))
                and data["warmup_seconds"] >= (0 if smoke else 30)
                and bool(rows) and all(duration(row, smoke=smoke) for row in rows),
            "all_p95_pass": bool(p95) and all(p95), "all_private_rss_pass": bool(rss) and all(rss),
            "all_bundle_integrity_pass": intact and bool(bundles) and all(bundles),
            "all_response_equality_pass": bool(rows) and all(row.get("response_equality_pass") is True for row in rows)}


def analyze_storm(data, *, seconds, target_rate=10000):
    recorder = data.get("recorder", {})
    counters = ("written", "dropped", "invalid", "queued", "used_bytes", "reserved_bytes")
    known = all(type(recorder.get(key)) is int and recorder[key] >= 0 for key in counters)
    measured = numeric(data.get("duration_seconds")) and data["duration_seconds"] > 0
    attempted = data.get("attempted")
    achieved = data.get("achieved_rate_per_second")
    load = (measured and type(attempted) is int and attempted > 0
            and type(data.get("target_rate_per_second")) is int
            and data["target_rate_per_second"] == target_rate and target_rate > 0
            and numeric(achieved)
            and math.isclose(achieved, attempted / data["duration_seconds"], rel_tol=1e-9)
            and abs(achieved / target_rate - 1) <= .001)
    start, peak = data.get("starting_process_rss_bytes"), data.get("peak_process_rss_bytes")
    memory = numeric(start) and start > 0 and numeric(peak) and peak >= start and peak - start <= 128 * 1048576
    return {"scope": "private library error storm with live availability probes",
            "arrival_rate_tolerance_fraction": .001,
            "private_rss_growth_limit_bytes": 128 * 1048576,
            "all_duration_pass": numeric(data.get("duration_seconds")) and data["duration_seconds"] >= seconds,
            "all_attempts_pass": type(data.get("attempted")) is int and data["attempted"] > 0
                and type(data.get("accepted")) is int and 0 <= data["accepted"] <= data["attempted"],
            "all_load_pass": load, "all_memory_pass": memory,
            "all_counters_known_pass": known,
            "all_quota_pass": known and numeric(data.get("quota_bytes"))
                and recorder["used_bytes"] <= data["quota_bytes"] and recorder["reserved_bytes"] == 0
                and recorder["queued"] == 0 and recorder.get("storage_degraded") is False,
            "all_availability_pass": all(data.get(name, {}).get("ok", 0) > 0
                and data[name].get("failed") == 0 for name in ("health", "search"))}
