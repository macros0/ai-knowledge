"""A current backup stop guard cannot infer process death from Pod absence."""
from copy import deepcopy
import json

import pytest

from tests.test_replica_backup_coordinator import coordinator, config
from tests.test_replica_provider_stop import fixture as stopped_fixture


def observed_lab(monkeypatch):
    module = coordinator()
    value, binding, items, nodes = deepcopy(stopped_fixture())
    inputs = config()
    uid = "11111111-1111-4111-8111-111111111111"
    value.update(namespace=inputs["namespace"], namespace_uid=uid)
    for item in items:
        meta = item["metadata"]
        labels = meta.get("labels", {})
        if item["kind"] == "Namespace":
            meta.update(name=inputs["namespace"], uid=uid)
        if labels.get("app.kubernetes.io/instance") == "application":
            labels["app.kubernetes.io/instance"] = inputs["application_release"]
        if labels.get("app.kubernetes.io/instance") == "storage":
            labels["app.kubernetes.io/instance"] = inputs["storage_release"]
            meta["name"] = meta["name"].replace("storage-", inputs["storage_release"] + "-", 1)
    containers = []
    binding["storage_pods"] = {}
    for index, pod in enumerate(v for v in items if v["kind"] == "Pod"):
        container_id = str(index + 1) * 64
        pod["status"]["containerStatuses"][0].update(containerID="containerd://" + container_id, name="storage")
        binding["storage_pods"][pod["metadata"]["name"]] = {"uid": pod["metadata"]["uid"], "container_ids": [container_id]}
        containers.append({"id": container_id, "state": "CONTAINER_RUNNING", "labels": {
            "io.kubernetes.pod.namespace": inputs["namespace"], "io.kubernetes.pod.name": pod["metadata"]["name"],
            "io.kubernetes.pod.uid": pod["metadata"]["uid"]}})
    lab = module.Lab(inputs, uid)
    lab._backup_stop_binding = binding
    monkeypatch.setattr(lab, "inventory", lambda: items)
    monkeypatch.setattr(lab, "kubectl", lambda *_a, **_k: json.dumps({"items": nodes}).encode())
    monkeypatch.setattr(module, "fetch_stop_observations", lambda _lab: (items, nodes, containers), raising=False)
    return module, lab, binding, items, nodes, containers


@pytest.mark.parametrize("fault", ["retired_writer", "unknown_writer", "replaced_node", "missing_cri", "old_storage_process"])
def test_current_backup_refuses_incomplete_or_surviving_process_observations(monkeypatch, fault):
    _, lab, binding, items, nodes, containers = observed_lab(monkeypatch)
    if fault in {"retired_writer", "unknown_writer"}:
        containers.append({"id": "f" * 64, "state": "CONTAINER_RUNNING", "labels": {
            "io.kubernetes.pod.namespace": lab.config["namespace"], "io.kubernetes.pod.name": "deleted-worker",
            "io.kubernetes.pod.uid": binding["writer_pod_uids"][0] if fault == "retired_writer" else "unknown"}})
    elif fault == "replaced_node":
        nodes[0]["metadata"]["uid"] = "replacement-node"
    elif fault == "missing_cri":
        containers.clear()
    else:
        containers.append({"id": "f" * 64, "state": "CONTAINER_RUNNING", "labels": {
            **containers[0]["labels"], "io.kubernetes.pod.uid": "old-storage-pod"}})
    with pytest.raises(ValueError):
        lab.drained()


def test_current_backup_repeats_live_node_observation_on_every_guard(monkeypatch):
    module, lab, _, items, nodes, containers = observed_lab(monkeypatch)
    calls = []

    def collect(_lab):
        calls.append(True)
        if len(calls) == 2:
            raise ValueError("Bound node observation unavailable")
        return items, nodes, containers

    monkeypatch.setattr(module, "fetch_stop_observations", collect, raising=False)
    lab.drained()
    with pytest.raises(ValueError):
        lab.drained()
    assert len(calls) == 2


def test_current_backup_accepts_only_the_three_bound_live_storage_processes(monkeypatch):
    _, lab, _, _, _, _ = observed_lab(monkeypatch)
    result = lab.drained()
    assert result["unknown_processes"] == result["retired_processes"] == 0
    assert result["storage_processes"] == 3


def test_backup_binds_live_writer_uids_before_they_disappear(monkeypatch):
    module, lab, _, items, nodes, containers = observed_lab(monkeypatch)
    lab._backup_stop_binding = None
    initial = deepcopy(items)
    nodes[0]["metadata"]["name"] = "okf-policy-control-plane"
    for pod in [v for v in items if v["kind"] == "Pod"] + [v for v in initial if v["kind"] == "Pod"]:
        pod["spec"]["nodeName"] = "okf-policy-control-plane"
    for deployment in [v for v in initial if v["kind"] == "Deployment"]:
        role = deployment["metadata"]["name"]
        deployment["spec"]["replicas"] = 1 if role == "model-stub" else 2
        for index in range(deployment["spec"]["replicas"]):
            uid = role + "-" + str(index)
            initial.append({"kind": "Pod", "metadata": {"name": uid, "uid": uid,
                "labels": {"okf-role": role, "app.kubernetes.io/instance": lab.config["application_release"]}},
                "spec": {"nodeName": "okf-policy-control-plane"},
                "status": {"phase": "Running", "containerStatuses": [{"ready": True}]}})
    monkeypatch.setattr(module, "fetch_stop_observations", lambda _lab: (initial, nodes, containers), raising=False)
    lab.bind_stop_observation()
    binding = lab._backup_stop_binding
    assert len(binding["writer_pod_uids"]) == 6 and binding["provider_pod_uid"] == "model-stub-0"
    assert binding["nodes"] == {"okf-policy-control-plane": "node-uid"}
    monkeypatch.setattr(module, "fetch_stop_observations", lambda _lab: (items, nodes, containers))
    assert lab.drained()["storage_processes"] == 3
    containers.append({"state": "CONTAINER_RUNNING", "labels": {
        "io.kubernetes.pod.uid": binding["writer_pod_uids"][0]}})
    with pytest.raises(ValueError):
        lab.drained()
