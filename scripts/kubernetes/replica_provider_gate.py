"""Fixed scope and durable protocol for a bounded own model-resource SQL gate.

Only the native adapter supplies a backend; manifests cannot import commands,
modules or SQL. A row gate never releases model capacity or modifies work rows.
"""
from __future__ import annotations

import copy
from datetime import datetime
import ipaddress
import re

from test_scripts.replica_frontend_http import PrivateFiles
from test_scripts.replica_provider_faults import fault_policy, hexadecimal, require, uid

MAXIMUM_SECONDS = 180
MINIMUM_LEASE_SECONDS = 30


def aware(value):
    require(isinstance(value, str) and len(value) <= 64)
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Aware native gate timestamp required") from None
    require(result.tzinfo is not None and result.utcoffset() is not None)
    return result


def integer(value, *, maximum=2147483647):
    return type(value) is int and 0 < value <= maximum


def gate_scope(value, operation, api, epoch):
    fault_policy(value, "qdrant", operation)
    require(value.get("context") == "kind-okf-policy" and hexadecimal(value.get("installation_id"), 32)
        and hexadecimal(value.get("runtime_fingerprint"), 64) and type(epoch) is int and 0 <= epoch < 2**63)
    metadata, status = api.get("metadata", {}), api.get("status", {})
    containers = status.get("containerStatuses", [])
    require(uid(metadata.get("uid")) and metadata.get("namespace") == value["namespace"]
        and isinstance(metadata.get("name"), str) and re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", metadata["name"]) is not None
        and not metadata.get("deletionTimestamp") and status.get("phase") == "Running"
        and metadata.get("labels", {}).get("okf-role") == "api"
        and metadata.get("labels", {}).get("app.kubernetes.io/instance") == value["application_release"]
        and api.get("spec", {}).get("nodeName") == "okf-policy-control-plane" and len(containers) == 1)
    container = containers[0]
    require(type(container.get("ready")) is bool and type(container.get("restartCount")) is int and container["restartCount"] == 0
        and container.get("containerID", "").startswith("containerd://")
        and hexadecimal(container["containerID"].removeprefix("containerd://"), 64)
        and re.search(r"sha256:[0-9a-f]{64}$", container.get("imageID", "")) is not None)
    return dict(operation_id=operation, namespace=value["namespace"], namespace_uid=value["namespace_uid"],
        run_id=value["run_id"], application_release=value["application_release"],
        installation_id=value["installation_id"], fingerprint=value["runtime_fingerprint"], epoch=epoch,
        api_pod=metadata["name"], api_pod_uid=metadata["uid"], api_container_id=container["containerID"],
        api_image_id=container["imageID"], node="okf-policy-control-plane", maximum_seconds=MAXIMUM_SECONDS)


def admit_proof(value):
    require(isinstance(value, dict) and set(value) == {"native_process", "pg_pid", "pg_started_at", "maximum_seconds"}
        and value["maximum_seconds"] == MAXIMUM_SECONDS and type(value["maximum_seconds"]) is int
        and integer(value["pg_pid"]))
    aware(value["pg_started_at"])
    process = value["native_process"]
    require(isinstance(process, dict) and set(process) == {"pid", "start_ticks", "uid", "argv_sha256"}
        and integer(process["pid"]) and type(process["uid"]) is int and process["uid"] == 1000
        and isinstance(process["start_ticks"], str) and re.fullmatch(r"[1-9][0-9]{0,30}", process["start_ticks"]) is not None
        and hexadecimal(process["argv_sha256"], 64))
    return copy.deepcopy(value)


class GateActions:
    """Append-only start/release intents, with observation after every lost ACK."""
    def __init__(self, value, directory, operation, guard, *, backend):
        require(callable(guard) and callable(backend))
        self.value, self.operation = copy.deepcopy(value), operation
        self.files, self.guard, self.backend = PrivateFiles(directory), guard, backend

    def check(self):
        require(self.guard() is not False)

    def binding(self):
        return self.files.read("qdrant.gate.start.intent.json")

    def receive(self, response):
        require(isinstance(response, dict) and response.get("binding") == self.binding())
        proof = admit_proof(response.get("proof"))
        name = "qdrant.gate.created.json"
        if self.files.path(name).exists():
            require(self.files.read(name) == dict(binding=self.binding(), proof=proof))
        return proof

    def ready(self, response):
        proof = self.receive(response)
        require(response.get("phase") == "ready" and response.get("native_alive") is True
            and type(response.get("remaining_seconds")) in {int, float}
            and 0 < response["remaining_seconds"] <= MAXIMUM_SECONDS)
        name = "qdrant.gate.created.json"
        if not self.files.path(name).exists():
            self.files.once(name, dict(binding=self.binding(), proof=proof))
        return dict(passed=True, proof=proof)

    def start(self, api, epoch):
        self.check()
        binding = gate_scope(self.value, self.operation, api, epoch)
        name = "qdrant.gate.start.intent.json"
        if self.files.path(name).exists():
            require(self.files.read(name) == binding)
            return self.observe()
        require(api["status"]["containerStatuses"][0]["ready"] is True)
        self.files.once(name, binding)
        return self.ready(self.backend("start", binding))

    def observe(self):
        self.check()
        return self.ready(self.backend("observe", self.binding()))

    def closed(self, response):
        proof = self.receive(response)
        require(response.get("phase") == "closed" and response.get("native_alive") is False
            and response.get("sql_backend_gone") is True and response.get("rolled_back") is True
            and response.get("reason") == "explicit-release")
        aware(response.get("closed_at"))
        result = dict(passed=True, proof=proof, closure=dict(closed_at=response["closed_at"], rolled_back=True,
            reason="explicit-release", sql_backend_gone=True, native_process_gone=True))
        name = "qdrant.gate.closed.json"
        if self.files.path(name).exists():
            require(self.files.read(name) == result)
        else:
            self.files.once(name, result)
        return result

    def release(self):
        self.check()
        binding = self.binding()
        created = self.files.read("qdrant.gate.created.json")
        require(created["binding"] == binding)
        name = "qdrant.gate.release.intent.json"
        if self.files.path(name).exists():
            require(self.files.read(name) == created)
            return self.closed(self.backend("observe", binding))
        self.files.once(name, created)
        return self.closed(self.backend("release", binding))

    def observe_release(self):
        self.check()
        name = "qdrant.gate.release.intent.json"
        if not self.files.path(name).exists():
            return None
        created = self.files.read("qdrant.gate.created.json")
        require(created["binding"] == self.binding() and self.files.read(name) == created)
        return self.closed(self.backend("observe", self.binding()))


def assert_admitted_claim(work, probe, gate, worker_ips):
    require(isinstance(work, dict) and work.get("status") == "running" and work.get("kind") == "document"
        and hexadecimal(work.get("id"), 32) and hexadecimal(work.get("doc_id"), 16)
        and integer(work.get("attempts")) and integer(work.get("fence"))
        and isinstance(work.get("owner_id"), str) and 0 < len(work["owner_id"]) <= 128
        and work.get("cancel_requested") is False)
    fields = ("id", "doc_id", "kind", "status", "attempts", "fence", "owner_id", "cancel_requested", "lease_until")
    require(isinstance(probe, dict) and isinstance(probe.get("work"), dict)
        and all(probe["work"].get(k) == work.get(k) for k in fields)
        and type(probe.get("model_owners")) is int and probe["model_owners"] == 0
        and type(probe.get("running_documents")) is int and probe["running_documents"] == 1
        and type(probe.get("running_chats")) is int and probe["running_chats"] == 0
        and integer(gate.get("pg_pid")) and probe.get("gate_pid") == gate["pg_pid"]
        and aware(probe.get("gate_started_at")) == aware(gate.get("pg_started_at")))
    require((aware(work.get("lease_until")) - aware(probe.get("now"))).total_seconds() >= MINIMUM_LEASE_SECONDS)
    require(isinstance(worker_ips, set) and len(worker_ips) == 2)
    try:
        require(all(isinstance(ip, str) and ipaddress.ip_address(ip).is_private for ip in worker_ips))
    except ValueError:
        raise ValueError("Owned worker IP set required") from None
    blocked = probe.get("blocked")
    require(isinstance(blocked, list) and len(blocked) == 1 and isinstance(blocked[0], dict)
        and blocked[0].get("client_addr") in worker_ips and blocked[0].get("blocked_by") == [gate["pg_pid"]])
    return dict(work_id=work["id"], owner_id=work["owner_id"], fence=work["fence"],
        lease_until=work["lease_until"], gate_pg_pid=gate["pg_pid"])


def assert_released_claim(claim, closed):
    require(isinstance(claim, dict) and hexadecimal(claim.get("work_id"), 32)
        and integer(claim.get("fence")) and isinstance(claim.get("owner_id"), str)
        and isinstance(closed, dict) and closed.get("proof", {}).get("pg_pid") == claim.get("gate_pg_pid"))
    receipt = closed.get("closure", {})
    require(receipt.get("rolled_back") is True and receipt.get("reason") == "explicit-release"
        and receipt.get("sql_backend_gone") is True and receipt.get("native_process_gone") is True
        and aware(receipt.get("closed_at")) < aware(claim.get("lease_until")))
