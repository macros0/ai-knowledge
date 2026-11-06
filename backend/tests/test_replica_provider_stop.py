"""Physical stop observations must reject surviving, replaced or unknown actors."""
import copy
import importlib
import importlib.util

import pytest


def module():
    assert importlib.util.find_spec("test_scripts.replica_provider_stop") is not None, "Physical provider stop observer is missing"
    return importlib.import_module("test_scripts.replica_provider_stop")


def fixture():
    value = {"namespace": "replica-test-fixture01", "namespace_uid": "n1", "application_release": "application", "storage_release": "storage"}
    binding = {"service_uid": "s1", "nodes": {"node1": "node-uid"}, "provider_pod_uid": "old-provider", "writer_pod_uids": ["old-worker"]}
    nodes = [{"metadata": {"name": "node1", "uid": "node-uid"}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}]
    items = [{"kind": "Namespace", "metadata": {"name": value["namespace"], "uid": "n1"}}]
    for role in ("api", "worker", "frontend", "model-stub"):
        items.append({"kind": "Deployment", "metadata": {"name": role, "uid": role, "generation": 2,
            "labels": {"app.kubernetes.io/instance": "application"}}, "spec": {"replicas": 0}, "status": {"observedGeneration": 2}})
    for role in ("postgres", "qdrant", "artifacts"):
        items.append({"kind": "Pod", "metadata": {"name": "storage-" + role + "-0", "uid": role,
            "labels": {"app.kubernetes.io/instance": "storage"}}, "spec": {"nodeName": "node1"},
            "status": {"phase": "Running", "containerStatuses": [{"ready": True}]}})
    items.append({"kind": "Service", "metadata": {"name": "model-stub", "uid": "s1", "labels": {"app.kubernetes.io/instance": "application"}},
        "spec": {"type": "ClusterIP", "selector": {"okf-role": "model-stub"}}})
    return value, binding, items, nodes


@pytest.mark.parametrize("fault", ["provider_pod", "unknown_pod", "endpoint", "node_replaced", "node_unready", "namespace_replaced", "service_replaced", "scheduled_job", "active_job"])
def test_uncertain_actor_cannot_produce_stop_proof(fault):
    tool = module()
    value, binding, items, nodes = copy.deepcopy(fixture())
    if fault in {"provider_pod", "unknown_pod"}:
        items.append({"kind": "Pod", "metadata": {"name": fault, "uid": "old-provider"}, "status": {"phase": "Running"}})
    elif fault == "endpoint":
        items.append({"kind": "EndpointSlice", "metadata": {"labels": {"kubernetes.io/service-name": "model-stub"}}, "endpoints": [{"addresses": ["10.0.0.1"]}]})
    elif fault == "node_replaced":
        nodes[0]["metadata"]["uid"] = "different"
    elif fault == "node_unready":
        nodes[0]["status"]["conditions"][0]["status"] = "False"
    elif fault == "namespace_replaced":
        items[0]["metadata"]["uid"] = "different"
    elif fault == "service_replaced":
        items[-1]["metadata"]["uid"] = "different"
    elif fault == "scheduled_job":
        items.append({"kind": "CronJob", "spec": {"suspend": False}})
    else:
        items.append({"kind": "Job", "status": {"active": 1}})
    with pytest.raises(ValueError):
        tool.stopped_facts(items, nodes, value, binding)


def test_verified_stop_facts_are_stable_when_heartbeat_changes():
    tool = module()
    value, binding, items, nodes = fixture()
    first = tool.stopped_facts(items, nodes, value, binding)
    nodes[0]["status"]["conditions"][0]["lastHeartbeatTime"] = "a-different-clock"
    assert tool.stopped_facts(items, nodes, value, binding) == first


def test_kubernetes_null_endpoint_list_means_no_provider_endpoints():
    tool = module()
    value, binding, items, nodes = fixture()
    expected = tool.stopped_facts(items, nodes, value, binding)
    items.append({"kind": "EndpointSlice", "metadata": {"labels": {"kubernetes.io/service-name": "model-stub"}}, "endpoints": None})
    assert tool.stopped_facts(items, nodes, value, binding) == expected


def test_foreign_cri_identity_cannot_be_selected_for_failure_injection():
    tool = module()
    expected = {"container_id": "a" * 64, "pod_uid": "pod1", "namespace": "replica-test-fixture01", "node": "okf-policy-control-plane"}
    status = {"id": "a" * 64, "state": "CONTAINER_RUNNING", "metadata": {"name": "worker"},
        "labels": {"io.kubernetes.pod.uid": "pod1", "io.kubernetes.pod.namespace": "foreign"}}
    with pytest.raises(ValueError):
        tool.validate_cri_worker(status, expected)


@pytest.mark.parametrize("retired", [True, False])
def test_cri_running_orphan_blocks_stop_even_after_pod_disappeared(retired):
    tool = module()
    value, binding, _, _ = fixture()
    labels = {"io.kubernetes.pod.namespace": value["namespace"], "io.kubernetes.pod.name": "unknown",
        "io.kubernetes.pod.uid": "old-provider" if retired else "unknown"}
    with pytest.raises(ValueError):
        tool.stopped_cri_facts([{"state": "CONTAINER_RUNNING", "labels": labels}], value, binding)


def test_cri_exited_processes_and_other_namespaces_do_not_block():
    tool = module()
    value, binding, _, _ = fixture()
    containers = [{"state": "CONTAINER_EXITED", "labels": {"io.kubernetes.pod.uid": "old-provider"}},
        {"state": "CONTAINER_RUNNING", "labels": {"io.kubernetes.pod.namespace": "other"}}]
    assert tool.stopped_cri_facts(containers, value, binding) == {"retired_processes": 0, "unknown_processes": 0}
