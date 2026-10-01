import pytest
from test_scripts.analyze_node_memory_matched import analyze


def fixture():
    rows = []
    order = [("off", False), ("standard", False), ("off", True), ("standard", True)]
    for index, (capture, zip_work) in enumerate(order + list(reversed(order))):
        node = []
        for age, used, rss in [(10, 35, 110), (40, 48, 150), (80, 35, 130), (100, 38, 150), (120, 35, 110), (160, 36, 111)]:
            for role in ("next", "launcher"):
                row = {"role": role, "type": "gc" if age in (80, 120) and role == "next" else "sample",
                       "pid": 18 if role == "next" else 1, "at_unix_ms": 1000000 + age * 1000,
                       "uptime_ms": age * 1000, "birth_unix_ms": 1000000,
                       "rss_bytes": (rss if role == "next" else 44) * 1048576,
                       "heap_used_bytes": (used if role == "next" else 6) * 1048576,
                       "heap_total_bytes": 80 * 1048576, "external_bytes": 3 * 1048576,
                       "array_buffers_bytes": 100000, "physical_heap_bytes": 70 * 1048576,
                       "heap_limit_bytes": 500 * 1048576, "native_contexts": 1, "detached_contexts": 0,
                       "minor_count": 0, "major_count": 0, "other_count": 0, "minor_ms": 0,
                       "major_ms": 0, "other_ms": 0}
                if row["type"] == "gc": row.update(gc_kind=4, gc_duration_ms=5)
                node.append(row)
        rows.append({"case": index + 1, "capture": capture, "zip": zip_work, "requests": 1000,
                     "measurement_start_age": 40, "measurement_end_age": 100, "idle_end_age": 160,
                     "node_samples": node, "linux_samples": [{"age_seconds": age, "tree_rss_bytes": 190 * 1048576,
                     "processes": [{"Pss": 150 * 1048576, "Private_Clean": 2 * 1048576,
                                    "Private_Dirty": 130 * 1048576}]} for age in (10, 40, 80, 100, 130, 159)],
                     "p95_ms": 100, "bundle": None})
    return {"complete": True, "diagnostic_only": True, "rows": rows}


def test_reports_natural_major_gc_separately_from_rss_peak():
    result = analyze(fixture())
    first = result["cases"][0]
    assert first["next_peak_rss_mib"] == 150
    assert first["idle_heap_used_mib"] == 36
    assert first["idle_major_gc_events"] == 1
    assert first["post_major_observed_heap_mib"] == 35
    assert result["old_acceptance"] == "failed_unchanged"


def test_absence_of_major_gc_stays_unknown():
    data = fixture()
    for row in data["rows"]:
        for sample in row["node_samples"]:
            sample["type"] = "sample"; sample.pop("gc_kind", None); sample.pop("gc_duration_ms", None)
    assert analyze(data)["cases"][0]["post_major_observed_heap_mib"] is None


def test_incomplete_or_private_numeric_samples_cannot_be_accepted():
    data = fixture(); data["rows"].pop()
    with pytest.raises(ValueError): analyze(data)
    data = fixture(); data["rows"][0]["node_samples"][0]["password"] = 123
    with pytest.raises(ValueError): analyze(data)
    data = fixture(); data["rows"][0]["measurement_end_age"] = 90
    with pytest.raises(ValueError): analyze(data)
