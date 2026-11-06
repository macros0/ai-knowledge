"""Physical observations for the owned, single-node synthetic provider recipe.

No TTL, log message, saved receipt or unreachable node proves process completion.
The caller must fetch fresh Kubernetes and CRI observations for each guard call.
"""
from hashlib import sha256
import json
import re


ROLES = {"api", "worker", "frontend", "model-stub"}


def stopped_facts(items, nodes, value, binding):
    namespaces = [o for o in items if o.get("kind") == "Namespace"]
    if (len(namespaces) != 1 or namespaces[0]["metadata"].get("uid") != value["namespace_uid"]
            or namespaces[0]["metadata"].get("name") != value["namespace"]
            or namespaces[0]["metadata"].get("deletionTimestamp")):
        raise ValueError("Recorded namespace no longer exists")
    current = {n["metadata"]["name"]: n for n in nodes}
    if not binding.get("nodes") or not binding.get("writer_pod_uids") or not binding.get("provider_pod_uid"):
        raise ValueError("Original writer and node bindings required")
    for name, uid in binding["nodes"].items():
        node = current.get(name, {})
        if (node.get("metadata", {}).get("uid") != uid or node.get("metadata", {}).get("deletionTimestamp")
                or not any(c.get("type") == "Ready" and c.get("status") == "True"
                    for c in node.get("status", {}).get("conditions", []))):
            raise ValueError("A writer node is replaced or unreachable")
    deployments = [o for o in items if o.get("kind") == "Deployment"]
    if len(deployments) != 4 or {o["metadata"]["name"] for o in deployments} != ROLES:
        raise ValueError("Unexpected deployment set")
    for obj in deployments:
        meta, status = obj["metadata"], obj.get("status", {})
        if (meta.get("deletionTimestamp") or meta.get("labels", {}).get("app.kubernetes.io/instance") != value["application_release"]
                or obj["spec"].get("replicas") != 0 or status.get("observedGeneration") != meta.get("generation")
                or any(status.get(k, 0) != 0 for k in ("replicas", "readyReplicas", "availableReplicas", "updatedReplicas"))
                or binding.get("deployments", {}).get(meta["name"], meta["uid"]) != meta["uid"]):
            raise ValueError("Application controller is not stopped")
    storage = {value["storage_release"] + "-" + role + "-0" for role in ("postgres", "qdrant", "artifacts")}
    seen = set()
    for obj in items:
        kind, meta = obj.get("kind"), obj.get("metadata", {})
        status = obj.get("status", {})
        if kind == "Pod":
            if status.get("phase") in {"Succeeded", "Failed"}:
                if any("terminated" not in c.get("state", {}) for c in status.get("containerStatuses", [])):
                    raise ValueError("Terminal Pod still has a process")
                continue
            if (meta.get("name") not in storage or meta.get("deletionTimestamp")
                    or meta.get("labels", {}).get("app.kubernetes.io/instance") != value["storage_release"]
                    or obj.get("spec", {}).get("nodeName") not in binding["nodes"]
                    or status.get("phase") != "Running" or not status.get("containerStatuses")
                    or any(c.get("ready") is not True for c in status["containerStatuses"])):
                raise ValueError("A writer or unknown Pod remains")
            seen.add(meta["name"])
        elif kind == "Job" and (status.get("active", 0) or not any(c.get("type") in {"Complete", "Failed"}
                and c.get("status") == "True" for c in status.get("conditions", []))):
            raise ValueError("An unfinished Job remains")
        elif kind == "CronJob" and (obj.get("spec", {}).get("suspend") is not True or status.get("active")):
            raise ValueError("A scheduled writer remains")
        elif kind in {"DaemonSet", "ReplicationController"}:
            raise ValueError("An unsupported writer controller remains")
        elif kind == "StatefulSet" and (meta.get("name") + "-0" not in storage or obj["spec"].get("replicas") != 1
                or meta.get("labels", {}).get("app.kubernetes.io/instance") != value["storage_release"]):
            raise ValueError("Unexpected stateful writer")
        elif kind == "ReplicaSet" and (obj.get("spec", {}).get("replicas", 0) or status.get("replicas", 0)):
            raise ValueError("A replica controller remains active")
        elif kind == "EndpointSlice" and meta.get("labels", {}).get("kubernetes.io/service-name") in {"model-stub", "backend"}:
            if any(e.get("addresses") for e in (obj.get("endpoints") or [])):
                raise ValueError("A provider or API endpoint remains")
    if seen != storage:
        raise ValueError("The exact three storage Pods must remain available")
    services = [o for o in items if o.get("kind") == "Service" and o["metadata"].get("name") == "model-stub"]
    if len(services) != 1:
        raise ValueError("Owned provider service required")
    service = services[0]
    spec, meta = service["spec"], service["metadata"]
    if (meta.get("uid") != binding["service_uid"] or meta.get("deletionTimestamp")
            or meta.get("labels", {}).get("app.kubernetes.io/instance") != value["application_release"]
            or spec.get("type") != "ClusterIP" or spec.get("externalName") or spec.get("externalIPs")
            or spec.get("selector", {}).get("okf-role") != "model-stub"
            or spec.get("selector") != binding.get("service_selector", spec.get("selector"))):
        raise ValueError("Provider service identity changed")
    return {"namespace_uid": value["namespace_uid"], "nodes": dict(sorted(binding["nodes"].items())),
        "deployments": {o["metadata"]["name"]: o["metadata"]["uid"] for o in deployments},
        "service_uid": meta["uid"], "storage_pods": sorted(seen), "writers": 0, "provider_endpoints": 0}


def validate_cri_worker(status, expected):
    if (expected.get("node") != "okf-policy-control-plane" or not re.fullmatch(r"[0-9a-f]{64}", expected.get("container_id", ""))
            or not re.fullmatch(r"replica-test-[a-z0-9]{8,32}", expected.get("namespace", ""))
            or status.get("id") != expected["container_id"] or status.get("state") != "CONTAINER_RUNNING"
            or status.get("metadata", {}).get("name") != "worker"
            or status.get("labels", {}).get("io.kubernetes.pod.uid") != expected.get("pod_uid")
            or status.get("labels", {}).get("io.kubernetes.pod.namespace") != expected["namespace"]):
        raise ValueError("CRI worker differs from the bound owned Pod")


def stopped_cri_facts(containers, value, binding):
    """A ready kubelet alone is insufficient: also exclude live orphan containers."""
    retired = set(binding["writer_pod_uids"]) | {binding["provider_pod_uid"]}
    allowed = {value["storage_release"] + "-" + role + "-0" for role in ("postgres", "qdrant", "artifacts")}
    for container in containers:
        if container.get("state") != "CONTAINER_RUNNING":
            continue
        labels = container.get("labels", {})
        if labels.get("io.kubernetes.pod.uid") in retired or (labels.get("io.kubernetes.pod.namespace") == value["namespace"]
                and labels.get("io.kubernetes.pod.name") not in allowed):
            raise ValueError("A retired or unknown namespace process remains")
    return {"retired_processes": 0, "unknown_processes": 0}


def observation_digest(facts, cri_facts):
    return sha256(json.dumps({"kubernetes": facts, "cri": cri_facts}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
