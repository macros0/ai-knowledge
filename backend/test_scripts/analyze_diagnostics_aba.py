"""Describe bracketing controls and aligned numeric metrics; never grant SLA."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics


def distribution(intervals):
    values = sorted(item["duration_ms"] for item in intervals)
    result = {"requests": len(values)}
    if values:
        result.update(p50_ms=statistics.median(values), p95_ms=values[int(.95 * (len(values) - 1))],
                      p99_ms=values[int(.99 * (len(values) - 1))], max_ms=values[-1])
    return result


def metrics_window(samples, start, end):
    # Counter deltas use the nearest enclosing samples, including short ZIP phases.
    before = next((item for item in reversed(samples) if item["at_monotonic"] <= start), samples[0])
    after = next((item for item in samples if item["at_monotonic"] >= end), samples[-1])
    observed = after["at_monotonic"] - before["at_monotonic"]
    if observed <= 0:
        return {"available": False}
    result = {"available": True, "observed_seconds": observed,
              "requested_seconds": end - start}
    ticks = after["cpu_total_ticks"] - before["cpu_total_ticks"]
    if ticks:
        result["cpu_busy_percent"] = 100 * (1 - (after["cpu_idle_ticks"] - before["cpu_idle_ticks"]) / ticks)
        result["cpu_iowait_percent"] = 100 * (after["cpu_iowait_ticks"] - before["cpu_iowait_ticks"]) / ticks
        result["cpu_steal_percent"] = 100 * (after["cpu_steal_ticks"] - before["cpu_steal_ticks"]) / ticks
    for key in ("cpu_some", "cpu_full", "io_some", "io_full", "memory_some", "memory_full"):
        if key in before and key in after:
            result[key + "_stall_percent"] = 100 * (
                after[key]["total_usec"] - before[key]["total_usec"]) / (observed * 1_000_000)
    result["containers"] = {}
    for role, values in before.get("containers", {}).items():
        last = after["containers"][role]
        result["containers"][role] = {
            "cpu_core_percent": 100 * (last["usage_usec"] - values["usage_usec"]) / (observed * 1_000_000),
            "throttled_usec": last.get("throttled_usec", 0) - values.get("throttled_usec", 0),
            "memory_current_delta_bytes": last["memory_current_bytes"] - values["memory_current_bytes"],
            "io_read_bytes": sum(last["io_counters"].get(device, {}).get("rbytes", 0) - entry.get("rbytes", 0)
                                 for device, entry in values["io_counters"].items()),
            "io_write_bytes": sum(last["io_counters"].get(device, {}).get("wbytes", 0) - entry.get("wbytes", 0)
                                  for device, entry in values["io_counters"].items())}
    return result


def analyze(data, pve, ct, *, host_label="PVE", runtime_label="CT102"):
    if not data.get("diagnostic_aba") or len(data["rows"]) != 12:
        raise ValueError("Require all twelve diagnostic rows")
    groups = defaultdict(dict)
    timelines = []
    for row in data["rows"]:
        intervals = row["request_intervals"]
        if len(intervals) != row["requests"] or not row["response_equality_pass"]:
            raise ValueError("Missing request timings or changed responses")
        groups[row["experiment"]][row["position"]] = row
        origin = intervals[0]["started"]
        windows = defaultdict(list)
        for item in intervals:
            windows[int((item["started"] - origin) / 5)].append(item)
        summary = {"experiment": row["experiment"], "position": row["position"],
                   "mode": row["mode"], "with_zip": row["with_zip"],
                   **distribution(intervals), "windows": [], "phase_windows": []}
        for number, requests in sorted(windows.items()):
            start, end = requests[0]["started"], requests[-1]["finished"]
            summary["windows"].append({"offset_seconds": number * 5, **distribution(requests),
                "pve": metrics_window(pve, start, end), "ct": metrics_window(ct, start, end)})
        for phase in row["phases"]:
            start, end = phase["started"], phase["finished"]
            requests = [item for item in intervals if item["started"] < end and item["finished"] > start]
            summary["phase_windows"].append({"phase": phase["phase"], "started": start, "finished": end,
                **distribution(requests), "pve": metrics_window(pve, start, end),
                "ct": metrics_window(ct, start, end)})
        summary["pve"] = metrics_window(pve, origin, intervals[-1]["finished"])
        summary["ct"] = metrics_window(ct, origin, intervals[-1]["finished"])
        timelines.append(summary)
    comparisons = []
    for experiment, rows in groups.items():
        if set(rows) != {"A1", "B", "A2"}:
            raise ValueError("Missing bracketing control")
        a1, b, a2 = (rows[position] for position in ("A1", "B", "A2"))
        comparisons.append({"experiment": experiment, "p95_A1_ms": a1["p95_ms"],
            "p95_B_ms": b["p95_ms"], "p95_A2_ms": a2["p95_ms"],
            "B_vs_A1_percent": 100 * (b["p95_ms"] / a1["p95_ms"] - 1),
            "B_vs_A2_percent": 100 * (b["p95_ms"] / a2["p95_ms"] - 1),
            "A2_vs_A1_percent": 100 * (a2["p95_ms"] / a1["p95_ms"] - 1)})
    windows = [window for row in timelines for window in row["windows"]
               if window["pve"]["requested_seconds"] >= 4]
    io_windows = [window for window in windows if "io_full_stall_percent" in window["pve"]]
    latencies = [window["p95_ms"] for window in io_windows]
    io_values = [window["pve"]["io_full_stall_percent"] for window in io_windows]
    association = {"five_second_windows": len(windows), "io_available_windows": len(io_windows),
                   "exploratory_not_causal": True, "serial_windows_not_independent": True,
                   "pearson_p95_io_full": (statistics.correlation(latencies, io_values)
                                           if len(set(latencies)) > 1 and len(set(io_values)) > 1 else None)}
    for name, chosen in (("io_full_ge_5_percent", [window for window in io_windows
                                                if window["pve"]["io_full_stall_percent"] >= 5]),
                         ("io_full_lt_5_percent", [window for window in io_windows
                                                if window["pve"]["io_full_stall_percent"] < 5])):
        association[name] = {"windows": len(chosen),
                             "median_window_p95_ms": statistics.median(window["p95_ms"] for window in chosen)
                             if chosen else None}
    return {"schema_version": 1, "diagnostic_only": True, "sla_acceptance": "open",
            "metric_source_labels": {"pve": host_label, "ct": runtime_label},
            "metrics_counter_window": "nearest enclosing .25s samples; not independent evidence of causation",
            "comparisons": comparisons, "timelines": timelines, "io_association": association}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--pve", type=Path, required=True)
    parser.add_argument("--ct", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host-label", default="PVE")
    parser.add_argument("--runtime-label", default="CT102")
    args = parser.parse_args()
    result = analyze(json.loads(args.input.read_text()),
                     [json.loads(line) for line in args.pve.read_text().splitlines()],
                     [json.loads(line) for line in args.ct.read_text().splitlines()],
                     host_label=args.host_label, runtime_label=args.runtime_label)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["comparisons"], indent=2))
