"""Owned Cilium PostgreSQL partition with a live stale worker returning afterward.

The fixture changes only runtime's per-process UUID source to the Pod UID, so the
SQL owner can be located without memory inspection or changes to queue/fencing.
Full app.worker startup, handlers, heartbeat, health and model budget are retained.
"""
from __future__ import annotations

import argparse
import copy
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
import time
import traceback
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replica_worker_loss import BASELINE_SOURCE, SQL_STATE, WorkerLoss, emit, same_reservation
from test_scripts.replica_runtime_acceptance import role_pods, validate_inventory, verify_runtime
from test_scripts.replica_runtime_faults import FaultProbe, require_quiescent, upload
from replica_worker_loss import assert_recovered


LABEL = "okf-pg-partition"
IDENTIFIED_START = "import os;from uuid import UUID;import app.services.work.runtime as runtime;runtime.uuid4=lambda:UUID(os.environ['OKF_ACCEPTANCE_POD_UID']);from app.worker import main;raise SystemExit(main())"


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def partition_policy(value, operation):
    if (not re.fullmatch(r"[a-z0-9]{8,32}", value.get("run_id", ""))
            or value.get("namespace") != "replica-test-" + value["run_id"] or not re.fullmatch(r"[0-9a-f]{32}", operation)):
        raise ValueError("Explicit isolated partition operation required")
    return {"apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy",
        "metadata": {"name": "replica-pg-partition-" + operation[:12], "namespace": value["namespace"],
            "labels": {"okf-run-id": value["run_id"], "okf-partition-operation": operation}},
        "spec": {"endpointSelector": {"matchLabels": {"okf-role": "worker", "app.kubernetes.io/instance": value["application_release"], LABEL: operation}},
            "egressDeny": [{"toEndpoints": [{"matchLabels": {"app.kubernetes.io/instance": value["storage_release"],
                "okf-storage-component": "postgres"}}], "toPorts": [{"ports": [{"port": "5432", "protocol": "TCP"}]}]}]}}


def identified_worker(value):
    result = copy.deepcopy(value)
    containers = result["spec"]["template"]["spec"]["containers"]
    if len(containers) != 1 or containers[0].get("name") != "worker" or containers[0].get("command") != ["python", "-m", "app.worker"]:
        raise ValueError("Expected unchanged production worker entrypoint required")
    container = containers[0]
    if any(e.get("name") == "OKF_ACCEPTANCE_POD_UID" for e in container.get("env", [])):
        raise ValueError("Existing test identity instrumentation refused")
    container["command"] = ["python", "-u", "-c", IDENTIFIED_START]
    container.setdefault("env", []).append({"name": "OKF_ACCEPTANCE_POD_UID", "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}}})
    result.pop("status", None)
    result.get("metadata", {}).pop("managedFields", None)
    return result


def assert_stale_return(original, reclaimed, before, after):
    if (reclaimed["id"] != original["id"] or reclaimed["fence"] <= original["fence"]
            or reclaimed["owner_id"] == original["owner_id"] or reclaimed["status"] != "running"
            or before["active_count"] != 0 or after["active_count"] != 0 or after["active"] != before["active"]
            or not after.get("candidate") or after.get("candidate") != before.get("candidate")
            and after.get("candidate_work_fence", 0) < reclaimed["fence"]):
        raise ValueError("Stale return reclaimed ownership or changed a publication")


def qualify_partition(network, work, active_count):
    if (network.get("postgres") is not False or network.get("qdrant") is not True or network.get("model") is not True
            or network.get("live") != 200 or not any(w.get("status") == "running" for w in work) or active_count != 0):
        raise ValueError("An unfinished PostgreSQL-only partition with a live worker is required")


def assert_confirmed_return(original, reclaimed, work, doc):
    active = [g for g in doc["generations"] if g["phase"] == "active"]
    old = [g for g in doc["generations"] if g["work_fence"] == original["fence"]]
    if (work["id"] != original["id"] or work["status"] != "succeeded" or work["fence"] < reclaimed["fence"]
            or work["fence"] <= original["fence"] or doc["active_count"] != 1 or len(active) != 1
            or active[0]["id"] != doc["active"] or active[0]["work_fence"] != work["fence"]
            or not old or any(g["phase"] != "abandoned" for g in old)):
        raise ValueError("Confirmed-reply recovery must preserve only the new owner's publication")


DOC_STATE = '''import json
from sqlalchemy import text
from app.config import get_settings
from app.db.session import session_scope
from app.deployment.runtime_identity import enforce_runtime_identity
enforce_runtime_identity(get_settings())
with session_scope() as session:
 p=session.execute(text('SELECT active_generation_id,candidate_generation_id FROM document_generation_states WHERE doc_id=:id'),{'id':DOC_ID}).mappings().one()
 rows=[dict(r) for r in session.execute(text('SELECT id,phase,work_fence FROM document_generations WHERE doc_id=:id'),{'id':DOC_ID}).mappings()]
 current=next((r for r in rows if r['id']==p['candidate_generation_id']),{})
 print(json.dumps({'active':p['active_generation_id'],'candidate':p['candidate_generation_id'],'candidate_work_fence':current.get('work_fence'),
 'active_count':sum(r['phase']=='active' for r in rows),'generations':rows}))
'''


def connectivity_source(config):
    hosts = {"postgres": (config["storage_release"] + "-postgres", 5432), "qdrant": (config["storage_release"] + "-qdrant", 6333),
        "model": ("model-stub", 8000)}
    return '''import socket,json,urllib.request,urllib.error
result={}
for name,(host,port) in HOSTS.items():
 try:
  with socket.create_connection((host,port),timeout=1):pass
  result[name]=True
 except OSError:result[name]=False
for key in ('live','ready'):
 try:
  with urllib.request.urlopen('http://127.0.0.1:8001/health/'+key,timeout=2) as response:result[key]=response.status
 except urllib.error.HTTPError as error:result[key]=error.code
print(json.dumps(result))
'''.replace("HOSTS", repr(hosts))


METRICS_SOURCE = '''import json,urllib.request
with urllib.request.urlopen('http://127.0.0.1:8001/metrics',timeout=6) as response:lines=response.read().decode().splitlines()
print(json.dumps({line.split()[0]:float(line.split()[1]) for line in lines if line.startswith('okf_replica_process_')}))
'''


class PostgresPartition(WorkerLoss):
    def pod(self, previous):
        current = json.loads(self.kube.run("get", "pod", previous["metadata"]["name"], "-o", "json"))
        before = previous["status"]["containerStatuses"][0]
        now = current.get("status", {}).get("containerStatuses", [{}])[0]
        if (current["metadata"]["uid"] != previous["metadata"]["uid"] or current["metadata"].get("deletionTimestamp")
                or any(now.get(k) != before.get(k) for k in ("containerID", "imageID", "restartCount"))
                or "running" not in now.get("state", {})):
            raise ValueError("The original worker process is no longer running unchanged")
        return current

    def doc_state(self):
        return self.kube.python(self.journal["api_pod"]["metadata"]["name"], DOC_STATE.replace("DOC_ID", repr(self.journal["document"]["id"])))

    def inject(self):
        import httpx
        if self.journal is not None:
            raise ValueError("Existing journal prohibits another partition injection")
        probe = FaultProbe(self.kube, self.value)
        if probe.identity["fingerprint"] != self.fingerprint:
            raise ValueError("Installed runtime identity differs")
        require_quiescent(probe)
        objects = self.lab.inventory()
        deployments = {o["metadata"]["name"]: o for o in objects if o["kind"] == "Deployment"}
        if set(deployments) != {"api", "worker", "frontend", "model-stub"}:
            raise ValueError("Unknown deployment prevents fault injection")
        operation = uuid4().hex
        policy = partition_policy(self.value, operation)
        if json.loads(self.kube.run("get", "ciliumnetworkpolicies", "-o", "json"))["items"]:
            raise ValueError("Existing Cilium namespace rules prevent this qualified recipe")
        self.kube.run("create", "--dry-run=server", "-f", "-", source=json.dumps(policy))
        self.journal = {"version": 1, "recipe": "owned-cilium-pg-partition-v1", "phase": "prepared",
            "namespace_uid": self.value["namespace_uid"], "fingerprint": self.fingerprint,
            "operation_id": operation, "owner_id": "owned-lab-partition", "old_deployments": deployments,
            "baseline_documents": self.kube.python(role_pods(self.kube.inventory(), "api")[0]["metadata"]["name"], BASELINE_SOURCE)}
        self.save()
        self.lab.write(identified_worker(deployments["worker"]), verb="replace")
        self.kube.run("rollout", "status", "deployment/worker", "--timeout=45s")
        self.kube.run("set", "env", "deployment/model-stub", "MODEL_STUB_SCENARIO=delay", "MODEL_STUB_DELAY_SECONDS=30")
        self.kube.run("rollout", "status", "deployment/model-stub", "--timeout=45s")
        objects = self.kube.inventory()
        validate_inventory(objects, self.value)
        if verify_runtime(self.kube, objects, self.value)["fingerprint"] != self.fingerprint:
            raise ValueError("Tracing instrumentation changed runtime identity")
        models, workers = role_pods(objects, "model-stub"), role_pods(objects, "worker")
        if len(models) != 1:
            raise ValueError("One owned synthetic provider required")
        for pod in workers:
            if self.kube.python(pod["metadata"]["name"], connectivity_source(self.config)) != {
                    "postgres": True, "qdrant": True, "model": True, "live": 200, "ready": 200}:
                raise ValueError("Positive worker connectivity baseline failed")
        nodes = json.loads(self.kube.run("get", "nodes", "-o", "json"))["items"]
        writers = [p for role in ("api", "worker", "frontend") for p in role_pods(objects, role)]
        if {p["spec"]["nodeName"] for p in writers + models} != {"okf-policy-control-plane"}:
            raise ValueError("Only the owned single-node partition recipe is qualified")
        service = json.loads(self.kube.run("get", "service", "model-stub", "-o", "json"))
        binding = {"service_uid": service["metadata"]["uid"], "service_selector": service["spec"]["selector"],
            "nodes": {n["metadata"]["name"]: n["metadata"]["uid"] for n in nodes if n["metadata"]["name"] == "okf-policy-control-plane"},
            "deployments": {r: o["metadata"]["uid"] for r, o in deployments.items()}, "writer_pod_uids": [p["metadata"]["uid"] for p in writers],
            "provider_pod_uid": models[0]["metadata"]["uid"], "provider_image_digest": models[0]["status"]["containerStatuses"][0]["imageID"][-71:]}
        created = json.loads(self.kube.run("create", "-f", "-", "-o", "json", source=json.dumps(policy)))
        self.save(binding=binding, policy=created, identified_template=json.loads(self.lab.kubectl("get", "deploy", "worker", "-o", "json"))["spec"]["template"],
            api_pod=role_pods(objects, "api")[0], workers=workers, crash_receipt={"stopped_containers": 0})
        metrics = {p["metadata"]["uid"]: self.kube.python(p["metadata"]["name"], METRICS_SOURCE) for p in workers}
        with httpx.Client(base_url=self.value["frontend_url"], trust_env=False, timeout=25) as client:
            client.post("/api/auth/simulate", json={"username": "replica.admin"}).raise_for_status()
            document = upload(client, "replica-postgres-partition")
        self.save(document=document, metrics_before=metrics)
        deadline = time.monotonic() + 90
        while True:
            state = self.kube.python(self.journal["api_pod"]["metadata"]["name"], SQL_STATE.replace("DOC_ID", repr(document["id"])))
            running = [w for w in state["work"] if w["status"] == "running" and not w["cancel_requested"]]
            if len(state["leases"]) == 1 and state["provider"]["active"] == 1 and len(running) == 1:
                break
            if time.monotonic() > deadline:
                raise ValueError("No active request; no worker was partitioned")
            time.sleep(.5)
        owner = running[0]["owner_id"].split("/")[0]
        selected = [p for p in workers if p["metadata"]["uid"].replace("-", "") == owner]
        if len(selected) != 1 or state["fingerprint"] != self.fingerprint:
            raise ValueError("Exact active worker identity could not be located")
        old = self.pod(selected[0])
        self.save(source_epoch=state["epoch"], original_work=running[0], original_lease=state["leases"][0],
            provider_before=state["provider"], selected_worker=old, phase="partition-intent-recorded")
        patch = [{"op": "test", "path": "/metadata/uid", "value": old["metadata"]["uid"]},
            {"op": "test", "path": "/metadata/resourceVersion", "value": old["metadata"]["resourceVersion"]},
            {"op": "add", "path": "/metadata/labels/" + LABEL, "value": operation}]
        self.kube.run("patch", "pod", old["metadata"]["name"], "--type=json", "-p", json.dumps(patch))
        self.save(phase="partition-applied")
        deadline = time.monotonic() + 60
        while True:
            old = self.pod(self.journal["selected_worker"])
            connectivity = self.kube.python(old["metadata"]["name"], connectivity_source(self.config))
            if not connectivity["postgres"] and connectivity["qdrant"] and connectivity["model"] and connectivity["live"] == 200:
                break
            if time.monotonic() > deadline:
                raise ValueError("Required PostgreSQL-only deny was not observed")
            time.sleep(.5)
        self.confirm()

    def confirm(self):
        # Observation timeout does not terminate the already-applied fault. This
        # phase only observes the same Pod/container/policy, never re-injects it.
        if self.journal["phase"] != "partition-applied":
            raise ValueError("The same applied partition must be observed")
        expected = self.journal["policy"]
        current = json.loads(self.kube.run("get", "cnp", expected["metadata"]["name"], "-o", "json"))
        if current["metadata"]["uid"] != expected["metadata"]["uid"] or current["spec"] != expected["spec"]:
            raise ValueError("Original policy changed")
        old = self.pod(self.journal["selected_worker"])
        connectivity = self.kube.python(old["metadata"]["name"], connectivity_source(self.config))
        after, doc = self.state(), self.doc_state()
        qualify_partition(connectivity, after["work"], doc["active_count"])
        if (not same_reservation(after["leases"], self.journal["original_lease"])
                or after["provider"]["started"] != self.journal["provider_before"]["started"]):
            raise ValueError("The original provider reservation changed before reconnect")
        healthy = next(p for p in self.journal["workers"] if p["metadata"]["uid"] != old["metadata"]["uid"])
        if not self.kube.python(healthy["metadata"]["name"], connectivity_source(self.config))["postgres"]:
            raise ValueError("The other worker lost its PostgreSQL positive control")
        self.save(phase="partition-proven", connectivity_during=connectivity, healthy_worker=healthy, network_sample=after["provider"])
        emit("postgres-partition-proven", selected_workers=1, provider_active=after["provider"]["active"],
            unfinished_document=True, healthy_worker_postgres=True, qdrant_model_reachable=True)

    def observe(self):
        if self.journal["phase"] not in {"partition-proven", "reclaim-proven"}:
            raise ValueError("A recorded active partition must precede reclaim observation")
        deadline = time.monotonic() + 200
        while True:
            self.pod(self.journal["selected_worker"])
            self.pod(self.journal["healthy_worker"])
            state = self.state()
            if not same_reservation(state["leases"], self.journal["original_lease"]) or state["provider"]["started"] != self.journal["provider_before"]["started"]:
                raise ValueError("Original uncertain capacity was reallocated")
            work = [w for w in state["work"] if w["id"] == self.journal["original_work"]["id"]]
            if len(work) != 1:
                raise ValueError("Original work disappeared")
            current = work[0]
            doc = self.doc_state()
            if (current["fence"] > self.journal["original_work"]["fence"] and current["status"] == "running"
                    and current["owner_id"].split("/")[0] == self.journal["healthy_worker"]["metadata"]["uid"].replace("-", "")
                    and doc["candidate_work_fence"] == current["fence"] and state["provider"]["active"] == 0):
                break
            if time.monotonic() > deadline:
                raise ValueError("Reclaim unproven; inspect the same partition and live processes")
            time.sleep(2)
        old = self.kube.python(self.journal["selected_worker"]["metadata"]["name"], connectivity_source(self.config))
        if old["postgres"] or old["live"] != 200 or old["ready"] != 503:
            raise ValueError("Partitioned worker did not remain live and unready")
        self.save(phase="reclaim-proven", reclaimed_work=current, before_return=doc, partition_observation=state, partitioned_health=old)
        emit("healthy-worker-reclaimed", old_fence=self.journal["original_work"]["fence"], new_fence=current["fence"],
            old_process_still_running=True, old_worker_ready=False, provider_slot_still_owned=True)

    def delete_policy(self):
        expected = self.journal["policy"]
        raw = self.kube.run("get", "ciliumnetworkpolicy", expected["metadata"]["name"], "--ignore-not-found", "-o", "json")
        if not raw.strip():
            return
        current = json.loads(raw)
        if current["metadata"]["uid"] != expected["metadata"]["uid"] or current["spec"] != expected["spec"]:
            raise ValueError("Partition policy identity or content changed")
        self.kube.run("delete", "ciliumnetworkpolicy", expected["metadata"]["name"], "--wait=false")

    def heal(self):
        if self.journal["phase"] not in {"reclaim-proven", "healing"}:
            raise ValueError("New healthy ownership must be observed before old worker return")
        self.pod(self.journal["selected_worker"])
        self.save(phase="healing")
        self.delete_policy()
        deadline = time.monotonic() + 180
        while True:
            old = self.pod(self.journal["selected_worker"])
            connectivity = self.kube.python(old["metadata"]["name"], connectivity_source(self.config))
            if connectivity["postgres"] and connectivity["ready"] == 200:
                state = self.state()
                if not state["leases"] and not state["provider"]["active"] and any(
                        w["id"] == self.journal["original_work"]["id"] and w["status"] == "succeeded" for w in state["work"]):
                    return self.finish()
                metrics = self.kube.python(old["metadata"]["name"], METRICS_SOURCE)
                before = self.journal["metrics_before"][old["metadata"]["uid"]]
                if metrics["okf_replica_process_lease_losses_total"] > before["okf_replica_process_lease_losses_total"]:
                    break
            if time.monotonic() > deadline:
                raise ValueError("Original process return/ownership refusal unproven")
            time.sleep(2)
        state, doc = self.state(), self.doc_state()
        if (not same_reservation(state["leases"], self.journal["original_lease"])
                or state["provider"]["started"] != self.journal["provider_before"]["started"] or state["provider"]["active"] != 0):
            raise ValueError("Late return released or replayed an uncertain provider call")
        assert_stale_return(self.journal["original_work"], self.journal["reclaimed_work"], self.journal["before_return"], doc)
        objects = self.kube.inventory()
        validate_inventory(objects, self.value)
        busy_source = '''import json
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
from app.services.work.model_budget import acquire_model_budget
from app.services.llm_client import LLMBusyError
s=get_settings();enforce_runtime_identity(s)
try:b=acquire_model_budget(s,max_wait=0)
except LLMBusyError:print(json.dumps({'busy':True}))
else:
 b.complete(confirmed=True)
 print(json.dumps({'busy':False}))
'''
        for pod in role_pods(objects, "api"):
            if self.kube.python(pod["metadata"]["name"], busy_source) != {"busy": True}:
                raise ValueError("Expired uncertain request no longer blocks API admission")
        self.save(phase="quarantine-proven", stale_return=doc, returned_metrics=metrics, returned_connectivity=connectivity,
            lease_losses_delta=metrics["okf_replica_process_lease_losses_total"] - before["okf_replica_process_lease_losses_total"])
        emit("stale-worker-return-fenced", unchanged_container=True, lease_losses=self.journal["lease_losses_delta"],
            active_publications=0, original_slot_still_owned=True, busy_apis=2)

    def finish(self):
        """A live old provider client can acknowledge its confirmed terminal reply.

        That is different from an uncertain request after process loss: no offline
        acknowledgement or maintenance epoch is needed once normal capacity is idle.
        """
        if self.journal["phase"] != "healing":
            raise ValueError("Observe the same reconnected process, not a new fault")
        old = self.pod(self.journal["selected_worker"])
        self.pod(self.journal["healthy_worker"])
        net = self.kube.python(old["metadata"]["name"], connectivity_source(self.config))
        metrics = self.kube.python(old["metadata"]["name"], METRICS_SOURCE)
        before = self.journal["metrics_before"][old["metadata"]["uid"]]
        if (net != {"postgres": True, "qdrant": True, "model": True, "live": 200, "ready": 200}
                or metrics["okf_replica_process_claims_total"] < before["okf_replica_process_claims_total"] + 2
                or metrics["okf_replica_process_completed_total"] <= before["okf_replica_process_completed_total"]):
            raise ValueError("Original unchanged process did not resume useful work")
        state, doc = self.state(), self.doc_state()
        if (state["leases"] or state["provider"]["active"] or state["provider"]["peak_active"] != 1
                or state["provider"]["started"] != state["provider"]["finished"]):
            raise ValueError("Confirmed completion is not quiescent; uncertain capacity requires physical reconciliation")
        work = next(w for w in state["work"] if w["id"] == self.journal["original_work"]["id"])
        assert_confirmed_return(self.journal["original_work"], self.journal["reclaimed_work"], work, doc)
        # Run the real publication entry guard with the captured original owner/
        # fence, even granting a future client lease. SQL decides ownership. Any
        # unexpected acceptance rolls back; the probe never commits generation data.
        receipt = {k: self.journal["original_work"][k] for k in ("id", "owner_id", "fence")}
        source = '''import json
from datetime import datetime,timezone
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
from app.db.session import session_scope
from app.db.models import WorkItem
from app.services.work.models import WorkClaim,WorkSpec,LostOwnership
from app.services.work.document_handler import prepare_document_attempt
s=get_settings();enforce_runtime_identity(s)
original=ORIGINAL
with session_scope() as session:
 row=session.get(WorkItem,original['id'])
 spec=WorkSpec(row.kind,row.payload_version,row.payload_json,row.dedupe_key,row.doc_id)
claim=WorkClaim(original['id'],original['owner_id'],original['fence'],datetime.max.replace(tzinfo=timezone.utc),spec,s._maintenance_epoch)
class ProbeRollback(Exception):pass
rejected=False
try:
 with session_scope() as session:
  try:prepare_document_attempt(session,claim,s)
  except LostOwnership:rejected=True
  raise ProbeRollback()
except ProbeRollback:pass
print(json.dumps({'old_token_rejected':rejected,'probe_transaction_rolled_back':True}))
'''.replace("ORIGINAL", repr(receipt))
        rejected = self.kube.python(old["metadata"]["name"], source)
        if rejected != {"old_token_rejected": True, "probe_transaction_rolled_back": True} or self.doc_state() != doc:
            raise ValueError("Stale publication probe was accepted or changed persisted data")
        self.save(phase="confirmed-return", completion_kind="confirmed-provider-reply", returned_metrics=metrics,
            returned_connectivity=net, stale_return=doc, confirmed_work=work, confirmed_provider=state["provider"], old_token_probe=rejected,
            lease_losses_delta=metrics["okf_replica_process_lease_losses_total"] - before["okf_replica_process_lease_losses_total"])
        emit("confirmed-reply-stale-return-passed", same_original_process=True, old_token_rejected=True, old_generation="abandoned",
            published_fence=work["fence"], model_peak=state["provider"]["peak_active"], owned_slots=0)

    def restore(self):
        if self.journal["phase"] not in {"confirmed-return", "fixture-restoring"}:
            raise ValueError("Verified confirmed completion required before fixture restore")
        self.delete_policy()
        probe = FaultProbe(self.kube, self.value)
        require_quiescent(probe)
        current = json.loads(self.lab.kubectl("get", "deploy", "worker", "-o", "json"))
        original = self.journal["old_deployments"]["worker"]
        if current["metadata"]["uid"] != original["metadata"]["uid"] or current["spec"].get("replicas") != 2:
            raise ValueError("Worker deployment changed outside this fixture")
        template = current["spec"]["template"]
        self.save(phase="fixture-restoring")
        if template != original["spec"]["template"]:
            if template != self.journal["identified_template"]:
                raise ValueError("Worker instrumentation differs from its recorded template")
            current["spec"]["template"] = original["spec"]["template"]
            current.pop("status", None)
            current["metadata"].pop("managedFields", None)
            self.lab.write(current, verb="replace")
        self.kube.run("rollout", "status", "deployment/worker", "--timeout=45s")
        self.kube.run("set", "env", "deployment/model-stub", "MODEL_STUB_SCENARIO=success", "MODEL_STUB_DELAY_SECONDS-")
        self.kube.run("rollout", "status", "deployment/model-stub", "--timeout=45s")
        self.save(phase="normal-running")
        emit("partition-fixture-restored", normal_worker_entrypoint=True, model_scenario="success", maintenance_epoch_changed=False)

    def verify(self, report):
        if self.journal.get("completion_kind") != "confirmed-provider-reply":
            return super().verify(report)
        import httpx
        from datetime import datetime, timezone
        from test_scripts.replica_runtime_faults import download_matches, search_ids
        if self.journal["phase"] not in {"normal-running", "verified"}:
            raise ValueError("Normal fixture must be restored before functional verification")
        probe = FaultProbe(self.kube, self.value)
        if probe.identity["fingerprint"] != self.fingerprint:
            raise ValueError("Runtime fingerprint changed")
        require_quiescent(probe)
        objects = self.kube.inventory()
        docs = self.kube.python(role_pods(objects, "api")[0]["metadata"]["name"], BASELINE_SOURCE)
        document = self.journal["document"]
        state = probe.state([document["id"]])["documents"][document["id"]]
        assert_recovered(self.journal["original_work"], state["work"], state["active_count"], self.journal["baseline_documents"], docs)
        if len(docs) != len(self.journal["baseline_documents"]) + 1 or self.state()["epoch"] != self.journal["source_epoch"]:
            raise ValueError("Corpus scope or maintenance epoch changed")
        current_doc = next(d for d in docs if d["id"] == document["id"])
        if current_doc["status"] != "done" or current_doc["problem"] is not None:
            raise ValueError("Recovered document is incomplete")
        with httpx.Client(base_url=self.value["frontend_url"], trust_env=False, timeout=25) as client:
            client.post("/api/auth/simulate", json={"username": "replica.admin"}).raise_for_status()
            hits = search_ids(client)
            for d in self.journal["baseline_documents"] + [document]:
                if d.get("deleted_at"):
                    if d["id"] in hits or client.get(f"/api/documents/{d['id']}/download").status_code != 404:
                        raise ValueError("Trashed document became visible")
                else:
                    download_matches(client, d)
                    if d["id"] not in hits:
                        raise ValueError("A preserved or recovered document disappeared from search")
        ready = validate_inventory(self.kube.inventory(), self.value)
        public = {"status": "owned-postgres-partition-stale-return-passed", "scope": "single-node-managed-s3-synthetic-stub",
            "namespace": self.value["namespace"], "namespace_uid": self.value["namespace_uid"], "installation_id": self.config["installation_id"],
            "runtime_fingerprint": self.fingerprint, "fault_operation_id": self.journal["operation_id"], "maintenance_epoch": self.journal["source_epoch"],
            "partition": {"policy_uid": self.journal["policy"]["metadata"]["uid"], "policy_sha256": digest(self.journal["policy"]["spec"]),
                "selected_workers": 1, "port": 5432, "old_worker_live": True, "old_worker_ready": False,
                "healthy_worker_postgres_positive": True, "qdrant_model_positive": True, "provider_active_before_policy_patch": 1,
                "provider_active_at_network_sample": self.journal["network_sample"]["active"],
                "timing_limit": "first confirmed network sample followed provider finish; processing/publication still unfinished"},
            "stale_return": {"original_pod_uid": self.journal["selected_worker"]["metadata"]["uid"], "same_container_no_restart": True,
                "reclaimed_fence": self.journal["reclaimed_work"]["fence"], "original_generation_abandoned": True,
                "captured_old_owner_fence_publication_probe_rejected": True, "probe_transaction_rolled_back": True,
                "claims_total_at_return": self.journal["returned_metrics"]["okf_replica_process_claims_total"],
                "completed_total_at_return": self.journal["returned_metrics"]["okf_replica_process_completed_total"],
                "old_process_continued_work": True,
                "process_lease_losses_delta": self.journal["lease_losses_delta"]},
            "capacity": {"original_slot_retained_until_reconnect": True, "completion": "confirmed-provider-reply-acknowledged-normally",
                "offline_reconciliation_required": False, "provider_observations_after_reconnect": self.journal["confirmed_provider"], "owned_slots_at_finish": 0},
            "recovered_document": {**document, "status": "done", "problem": None, "original_fence": self.journal["original_work"]["fence"],
                "recovered_fence": self.journal["confirmed_work"]["fence"], "active_publications": 1, "search_download_sha256_verified": True},
            "preserved_existing_documents": len(self.journal["baseline_documents"]), "runtime": ready, "normal_entrypoint_restored": True,
            "policy_removed": True, "model_scenario": "success", "production_schedule": "disabled", "verified_at": datetime.now(timezone.utc).isoformat(),
            "test_instrumentation": "only runtime UUID source derived from Pod UID; full production worker entrypoint/queue/handlers/heartbeat unchanged",
            "remaining_runtime_faults": ["resource_load_qualification"], "limitations": ["not node or host failover", "not external provider certification", "no production SLA"]}
        Path(report).write_text(json.dumps(public, indent=2) + "\n")
        self.save(phase="verified", report=str(Path(report).resolve()))
        print(json.dumps(public), flush=True)

    def release(self):
        if self.journal["phase"] != "reconciled":
            raise ValueError("Physical provider reconciliation required first")
        self.delete_policy()
        current = json.loads(self.lab.kubectl("get", "deploy", "worker", "-o", "json"))
        original = self.journal["old_deployments"]["worker"]
        template = current["spec"]["template"]
        if current["spec"].get("replicas") != 0 or current["metadata"]["uid"] != original["metadata"]["uid"]:
            raise ValueError("Owned worker must remain stopped before restoring its entrypoint")
        if template != original["spec"]["template"]:
            if template != self.journal["identified_template"]:
                raise ValueError("Worker template changed outside the owned test instrumentation")
            current["spec"]["template"] = original["spec"]["template"]
            current.pop("status", None)
            current["metadata"].pop("managedFields", None)
            self.lab.write(current, verb="replace")
            self.kube.run("rollout", "status", "deployment/worker", "--timeout=45s")
        super().release()

    def report_result(self, public):
        public.pop("crashed_worker_containers")
        public.pop("provider_active_after_crash")
        public.update({"status": "owned-postgres-partition-stale-return-passed",
            "partition": {"policy_uid": self.journal["policy"]["metadata"]["uid"], "policy_sha256": digest(self.journal["policy"]["spec"]),
                "selected_workers": 1, "port": 5432, "provider_active_before_policy_patch": 1,
                "provider_active_at_network_sample": self.journal["network_sample"]["active"],
                "network_observation_may_follow_provider_finish": True,
                "healthy_worker_postgres_positive": True, "qdrant_model_positive": True, "old_worker_live": True, "old_worker_ready": False},
            "stale_return": {"original_pod_uid": self.journal["selected_worker"]["metadata"]["uid"], "same_container_no_restart": True,
                "reclaimed_fence": self.journal["reclaimed_work"]["fence"], "process_lease_losses_delta": self.journal["lease_losses_delta"],
                "late_active_publications": 0, "late_model_requests": 0, "uncertain_slot_retained": True, "normal_entrypoint_restored": True},
            "test_instrumentation": "only runtime UUID source derived from Pod UID; full production worker entrypoint/queue/handlers/heartbeat unchanged",
            "remaining_runtime_faults": ["resource_load_qualification"]})
        return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["inject", "confirm", "observe", "heal", "finish", "restore", "recover", "release", "verify"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--namespace-uid", required=True)
    parser.add_argument("--expected-fingerprint", required=True)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--report")
    parser.add_argument("--apply", action="store_true", required=True)
    args = parser.parse_args()
    try:
        config, journal = Path(args.config).resolve(strict=True), Path(args.journal).resolve()
        if config.parent != journal.parent or config == journal:
            raise ValueError("Separate protected original-token journal required")
        recipe = PostgresPartition(json.loads(config.read_bytes()), args.namespace_uid, journal, args.expected_fingerprint)
        if args.phase == "verify":
            if not args.report:
                raise ValueError("Public report path required")
            recipe.verify(args.report)
        else:
            getattr(recipe, args.phase)()
    except Exception as exc:
        print(json.dumps({"partition_refused": type(exc).__name__, "maintenance": "never-released-on-error",
            "locations": [Path(f.filename).name + ":" + str(f.lineno) for f in traceback.extract_tb(exc.__traceback__)[-3:]]}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
