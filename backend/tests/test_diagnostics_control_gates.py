"""Private control evidence cannot pass after corruption or incomplete measurement."""
from test_scripts.analyze_diagnostics_controls import analyze_fixed, analyze_storm


def fixed_fixture():
    baseline = {"mode": "baseline", "with_zip": False, "repeat": 1, "concurrency": 8,
                "p95_ms": 10, "requests": 1000, "elapsed_seconds": 60,
                "peak_process_tree_rss_bytes": 100, "input_sha256": "a" * 64,
                "response_equality_pass": True}
    bundle = {"status": "ready", "crc_pass": True, "events": 1000,
              "input_sha256": "a" * 64, "output_bytes": 100}
    rows = [baseline] + [{**baseline, "mode": mode, "with_zip": True, "bundle": bundle.copy(),
                         "phase_latencies": {"prepare": {"requests": 8}, "zip": {"requests": 8}}}
                        for mode in ("baseline", "standard", "detailed")]
    return {"warmup_seconds": 30, "rows": rows}


def test_fixed_input_corruption_fails_even_when_job_is_ready():
    data = fixed_fixture()
    assert analyze_fixed(data, records=1000, repeats=1, concurrencies=[8])["all_bundle_integrity_pass"]
    data["rows"][-1]["bundle"]["input_sha256"] = "b" * 64
    assert not analyze_fixed(data, records=1000, repeats=1, concurrencies=[8])["all_bundle_integrity_pass"]


def test_fixed_input_missing_case_and_slow_run_are_rejected():
    data = fixed_fixture()
    data["rows"][-1]["p95_ms"] = 12.1
    assert not analyze_fixed(data, records=1000, repeats=1, concurrencies=[8])["all_p95_pass"]
    data["rows"].pop()
    assert not analyze_fixed(data, records=1000, repeats=1, concurrencies=[8])["all_complete_pass"]


def storm_fixture():
    return {"duration_seconds": 60, "attempted": 600000, "accepted": 599000, "quota_bytes": 1000,
            "target_rate_per_second": 10000, "achieved_rate_per_second": 10000,
            "starting_process_rss_bytes": 100, "peak_process_rss_bytes": 200,
            "recorder": {"written": 599000, "dropped": 1000, "invalid": 0, "queued": 0,
                         "used_bytes": 500, "reserved_bytes": 0, "storage_degraded": False},
            "health": {"ok": 50, "failed": 0}, "search": {"ok": 50, "failed": 0}}


def test_storm_cannot_hide_missing_quota_evidence_or_live_failures():
    data = storm_fixture()
    result = analyze_storm(data, seconds=60)
    assert all(value for key, value in result.items() if key.startswith("all_"))
    del data["recorder"]["used_bytes"]
    data["search"]["failed"] = 1
    result = analyze_storm(data, seconds=60)
    assert not result["all_counters_known_pass"]
    assert not result["all_quota_pass"]
    assert not result["all_availability_pass"]


def test_storm_rejects_underloaded_arrival_schedule():
    data = storm_fixture()
    data.update(attempted=1, accepted=1, achieved_rate_per_second=1 / 60)
    assert not analyze_storm(data, seconds=60).get("all_load_pass", True)


def test_storm_requires_measured_and_bounded_memory():
    data = storm_fixture()
    data["starting_process_rss_bytes"] = None
    assert not analyze_storm(data, seconds=60).get("all_memory_pass", True)
    data.update(starting_process_rss_bytes=100, peak_process_rss_bytes=100 + 128 * 1048576 + 1)
    assert not analyze_storm(data, seconds=60).get("all_memory_pass", True)
