"""Partition only one bound worker; never weaken the queue or model quarantine."""
import copy
import importlib.util
from pathlib import Path
import sys

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_postgres_partition.py"
    assert path.is_file(), "Actual PostgreSQL partition coordinator is missing"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("postgres_partition_test", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_deny_policy_selects_only_the_owned_fault_label_and_postgres_port():
    tool = module()
    v = {"namespace": "replica-test-fixture01", "run_id": "fixture01", "application_release": "application", "storage_release": "storage"}
    result = tool.partition_policy(v, "a" * 32)
    assert result["metadata"]["namespace"] == v["namespace"]
    assert result["spec"]["endpointSelector"]["matchLabels"] == {
        "okf-role": "worker", "app.kubernetes.io/instance": "application", "okf-pg-partition": "a" * 32}
    assert result["spec"]["egressDeny"] == [{"toEndpoints": [{"matchLabels": {
        "app.kubernetes.io/instance": "storage", "okf-storage-component": "postgres"}}],
        "toPorts": [{"ports": [{"port": "5432", "protocol": "TCP"}]}]}]
    with pytest.raises(ValueError):
        tool.partition_policy({**v, "namespace": "production"}, "a" * 32)


def test_traceable_startup_preserves_image_configuration_and_worker_entrypoint():
    tool = module()
    value = {"spec": {"replicas": 2, "template": {"spec": {"containers": [{"name": "worker",
        "command": ["python", "-m", "app.worker"], "image": "owned-image", "envFrom": [{"secretRef": {"name": "private"}}]}]}}}}
    before = copy.deepcopy(value)
    result = tool.identified_worker(value)
    assert value == before
    old = before["spec"]["template"]["spec"]["containers"][0]
    new = result["spec"]["template"]["spec"]["containers"][0]
    assert new["image"] == old["image"] and new["envFrom"] == old["envFrom"]
    assert "main()" in new["command"][-1] and "runtime.uuid4" in new["command"][-1]
    assert new["env"][-1]["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.uid"


@pytest.mark.parametrize("change", ["fence", "owner", "publication", "generation"])
def test_stale_return_requires_reclaim_without_late_publication(change):
    tool = module()
    original = {"id": "work", "owner_id": "old", "fence": 1}
    reclaimed = {"id": "work", "owner_id": "new", "fence": 2, "status": "running"}
    before = {"active": None, "candidate": "new-generation", "active_count": 0}
    after = dict(before)
    if change == "fence":
        reclaimed["fence"] = 1
    elif change == "owner":
        reclaimed["owner_id"] = "old"
    elif change == "publication":
        after["active_count"] = 1
    else:
        after["candidate"] = "old-generation"
    with pytest.raises(ValueError):
        tool.assert_stale_return(original, reclaimed, before, after)


@pytest.mark.parametrize("change", [{"postgres": True}, {"qdrant": False}, {"model": False}, {"live": 503}])
def test_delayed_observation_requires_the_same_postgres_only_fault(change):
    tool = module()
    net = {"postgres": False, "qdrant": True, "model": True, "live": 200}
    with pytest.raises(ValueError):
        tool.qualify_partition({**net, **change}, [{"status": "running"}], 0)


def test_provider_finish_does_not_make_unfinished_partitioned_work_an_idle_test():
    tool = module()
    net = {"postgres": False, "qdrant": True, "model": True, "live": 200}
    tool.qualify_partition(net, [{"status": "running"}], 0)
    with pytest.raises(ValueError):
        tool.qualify_partition(net, [{"status": "succeeded"}], 0)


@pytest.mark.parametrize("fault", ["old_work", "old_active", "duplicate", "running"])
def test_confirmed_reply_recovery_requires_only_the_new_generation_to_publish(fault):
    tool = module()
    work = {"id": "work", "fence": 2, "status": "succeeded"}
    doc = {"active": "new", "active_count": 1, "generations": [
        {"id": "old", "phase": "abandoned", "work_fence": 1}, {"id": "new", "phase": "active", "work_fence": 2}]}
    if fault == "old_work":
        work["fence"] = 1
    elif fault == "old_active":
        doc["generations"][0]["phase"] = "active"
    elif fault == "duplicate":
        doc["active_count"] = 2
    else:
        work["status"] = "running"
    with pytest.raises(ValueError):
        tool.assert_confirmed_return({"id": "work", "fence": 1}, {"fence": 2}, work, doc)
