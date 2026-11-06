"""Small owned-lab load evidence must preserve timing, identity and failures."""
import importlib.util
from pathlib import Path
import sys

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_load.py"
    assert path.is_file(), "Bounded replica load probe is missing"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("replica_load_test", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_latency_summary_preserves_tail_and_rejects_failed_or_invalid_samples():
    tool = module()
    result = tool.latencies([10, 20, 30, 40, 100])
    assert result == {"count": 5, "p50_ms": 30, "p95_ms": 100, "max_ms": 100}
    for bad in ([], [1, float("nan")], [-1], [True]):
        with pytest.raises(ValueError):
            tool.latencies(bad)


def test_cpu_and_memory_summary_never_compares_replaced_or_reset_containers():
    tool = module()
    before = {"uid": "pod", "at": 10, "memory_current": 100, "memory_peak": 120, "memory_limit": 200,
        "cpu_usage_usec": 1000000, "cpu_throttled_usec": 100, "oom_kill": 0}
    after = {**before, "at": 12, "memory_current": 110, "memory_peak": 150, "cpu_usage_usec": 1500000, "cpu_throttled_usec": 300}
    result = tool.resource_delta(before, after)
    assert result["average_cpu_millicores"] == 250 and result["memory_peak_bytes"] == 150
    for change in ({"uid": "other"}, {"cpu_usage_usec": 0}, {"oom_kill": 1}, {"at": 10}):
        with pytest.raises(ValueError):
            tool.resource_delta(before, {**after, **change})


def test_partial_or_degraded_batch_is_never_successful_throughput():
    tool = module()
    good = [{"id": "a", "status": "done", "problem": None, "active_count": 1},
        {"id": "b", "status": "done", "problem": None, "active_count": 1}]
    tool.assert_complete(["a", "b"], good)
    for bad in (good[:1], [good[0], {**good[1], "problem": "partial"}], [good[0], {**good[1], "active_count": 2}]):
        with pytest.raises(ValueError):
            tool.assert_complete(["a", "b"], bad)


def test_worker_count_comparison_refuses_changed_cpu_or_memory_policy():
    import copy
    tool = module()
    pod = {"metadata": {"labels": {"okf-role": "worker"}}, "spec": {"containers": [{
        "resources": {"limits": {"cpu": "500m", "memory": "768Mi"}, "requests": {"cpu": "200m", "memory": "128Mi"}}}]}}
    cases = [{"pods": [pod]}, {"pods": [copy.deepcopy(pod)]}]
    assert tool.resource_policies(cases)["worker"]["limits"]["cpu"] == "500m"
    for change in ({"cpu": "1", "memory": "768Mi"}, {"cpu": "500m", "memory": "1Gi"}):
        changed = copy.deepcopy(cases)
        changed[1]["pods"][0]["spec"]["containers"][0]["resources"]["limits"] = change
        with pytest.raises(ValueError):
            tool.resource_policies(changed)
