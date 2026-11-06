"""Explicit active worker-pool loss and audited recovery in the owned kind lab.

Phases are separate so a timeout never starts another fault or acknowledges an
uncertain request. The protected journal retains original reservation identities.
Only the instrumented local model-stub is supported; no real provider is stopped.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import shlex
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replica_backup import ROOT, connections, sql_source
from replica_upgrade import UpgradeLab, corpus, operator_job, release_source
from test_scripts.replica_provider_stop import (
    observation_digest,
    stopped_cri_facts,
    stopped_facts,
)
from test_scripts.replica_runtime_acceptance import (
    Kubernetes,
    role_pods,
    validate_inventory,
    verify_runtime,
)
from test_scripts.replica_runtime_faults import (
    FaultProbe,
    assert_fault_scope,
    require_quiescent,
    upload,
)


class StopObservation:
    def __init__(self, fetch, value, binding):
        self.fetch, self.value, self.binding = fetch, value, binding

    def digest(self):
        items, nodes, containers = self.fetch()  # Fresh observations on EVERY call.
        return observation_digest(stopped_facts(items, nodes, self.value, self.binding),
            stopped_cri_facts(containers, self.value, self.binding))


def same_reservation(rows, original):
    return len(rows) == 1 and rows[0].get("release_required") is True and all(
        rows[0].get(k) == original.get(k) for k in ("id", "resource", "owner_id", "fence"))


def assert_recovered(original, work, active_count, baseline, observed):
    rows = [r for r in work if r["id"] == original["id"]]
    if (len(rows) != 1 or rows[0]["status"] != "succeeded" or rows[0]["fence"] <= original["fence"] or active_count != 1):
        raise ValueError("Recovered work must publish once with a newer ownership fence")
    by_id = {d["id"]: d for d in observed}
    if len(by_id) != len(observed) or any(by_id.get(d["id"]) != d for d in baseline):
        raise ValueError("Previously published corpus changed during worker recovery")


def assert_released_state(state, expected):
    if (state.get("enabled") is not False or state.get("audit_count") != 1
            or any(state.get(k) != expected[k] for k in ("epoch", "operation_id"))):
        raise ValueError("Verification belongs to a different or unreleased recovery operation")


def assert_recovery_role(current, original, *, model=False):
    expected_count = 1 if model else 2
    if (current["metadata"]["uid"] != original["metadata"]["uid"]
            or original["spec"].get("replicas") != expected_count
            or current["spec"].get("replicas") not in {0, expected_count}):
        raise ValueError("Recovery controller identity/count changed")
    observed, baseline = copy.deepcopy(current["spec"]), copy.deepcopy(original["spec"])
    observed["replicas"] = baseline["replicas"] = 0
    if observed == baseline:
        return
    if model and current["spec"]["replicas"] == 0:
        container = baseline["template"]["spec"]["containers"][0]
        env = container.setdefault("env", [])
        if len({entry["name"] for entry in env}) != len(env):
            raise ValueError("Ambiguous model environment")
        for name, value in (("MODEL_STUB_SCENARIO", "delay"), ("MODEL_STUB_DELAY_SECONDS", "30")):
            matches = [index for index, entry in enumerate(env) if entry["name"] == name]
            if matches:
                env[matches[0]] = {"name": name, "value": value}
            else:
                env.append({"name": name, "value": value})
        if observed == baseline:
            return
    raise ValueError("Recovery source template changed outside the owned fault")


def released_state_source(proof):
    return '''import json
from sqlalchemy import text
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
from app.db.session import session_scope
enforce_runtime_identity(get_settings())
with session_scope() as session:
 state=dict(session.execute(text('SELECT epoch,operation_id,enabled FROM replica_maintenance_state WHERE id=1')).mappings().one())
 state['audit_count']=session.scalar(text("SELECT count(*) FROM audit_log WHERE action_type='replica_model_reconcile' AND target_id=:proof"),{'proof':PROOF})
 print(json.dumps(state))
'''.replace("PROOF", repr(proof))


def crash_source(selected):
    if len(selected) not in {1, 2} or any(not re.fullmatch(r"[0-9a-f]{64}", c.get("container_id", ""))
            or c.get("node") != "okf-policy-control-plane"
            or not re.fullmatch(r"replica-test-[a-z0-9]{8,32}", c.get("namespace", "")) for c in selected):
        raise ValueError("Exact owned worker container identities required")
    return '''import json,subprocess
selected=SELECTED
for c in selected:
 p=subprocess.run(['docker','exec',c['node'],'crictl','inspect',c['container_id']],capture_output=True,check=True,timeout=8)
 s=json.loads(p.stdout)['status'];labels=s.get('labels',{})
 assert s['id']==c['container_id'] and s['state']=='CONTAINER_RUNNING' and s['metadata']['name']=='worker'
 assert labels.get('io.kubernetes.pod.uid')==c['pod_uid'] and labels.get('io.kubernetes.pod.namespace')==c['namespace']
p=subprocess.run(['docker','exec','okf-policy-control-plane','crictl','stop','--timeout=0']+[c['container_id'] for c in selected],capture_output=True,timeout=20)
assert p.returncode==0
print(json.dumps({'stopped_containers':len(selected)}))
'''.replace("SELECTED", repr(selected))


SQL_STATE = '''import json,urllib.request
from sqlalchemy import text
from app.config import get_settings
from app.db.session import session_scope
from app.deployment.runtime_identity import enforce_runtime_identity
s=get_settings();identity=enforce_runtime_identity(s)
assert s.model_mode=='stub' and s.auth_provider=='simulation' and s.embedding_provider=='fake' and s.llm_base_url=='http://model-stub:8000/v1'
with session_scope() as session:
 result={'fingerprint':identity.fingerprint,'db_now':session.scalar(text('SELECT clock_timestamp()')).isoformat(),
 'epoch':session.scalar(text('SELECT epoch FROM replica_maintenance_state WHERE id=1')),
 'leases':[dict(r) for r in session.execute(text("SELECT id,resource,owner_id,fence,expires_at,release_required FROM replica_capacity_slots WHERE resource='llm' AND owner_id IS NOT NULL")).mappings()],
 'work':[dict(r) for r in session.execute(text("SELECT id,status,owner_id,fence,cancel_requested FROM work_items WHERE kind='document' AND doc_id=:doc ORDER BY created_at,id"),{'doc':DOC_ID}).mappings()]}
with urllib.request.urlopen('http://model-stub:8000/test/status',timeout=3) as response:result['provider']=json.load(response)
print(json.dumps(result,default=lambda x:x.isoformat()))
'''

BASELINE_SOURCE = '''import json,hashlib
from sqlalchemy import text
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
from app.db.session import session_scope
enforce_runtime_identity(get_settings())
with session_scope() as session:
 rows=session.execute(text("SELECT d.id,d.status,d.problem,d.deleted_at,o.sha256,g.active_generation_id FROM documents d JOIN artifact_bindings b ON b.owner_type='original' AND b.owner_id=d.id AND b.logical_name='original' JOIN artifact_objects o ON o.object_id=b.object_id LEFT JOIN document_generation_states g ON g.doc_id=d.id ORDER BY d.id")).mappings()
 result=[]
 for r in rows:
  concepts=[dict(c) for c in session.execute(text('SELECT slug,title,content FROM okf_concepts WHERE doc_id=:id ORDER BY slug'),{'id':r['id']}).mappings()]
  result.append({**dict(r),'deleted_at':str(r['deleted_at']) if r['deleted_at'] else None,'concept_sha256':hashlib.sha256(json.dumps(concepts,sort_keys=True).encode()).hexdigest()})
 print(json.dumps(result))
'''


def emit(phase, **kwargs):
    print(json.dumps({"phase": phase, **kwargs}), flush=True)


def remote(command, *, source=None):
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={ROOT / 'tests/tmp/replica-lab-known_hosts'}", "ubuntu@10.10.1.52", shlex.join(command)],
        input=source, text=True, capture_output=True, timeout=45, check=False)
    if result.returncode:
        raise ValueError("Owned node observation or exact container operation failed")
    if len(result.stdout.encode()) > 8 * 1024 * 1024:
        raise ValueError("Node observation size bound exceeded")
    return json.loads(result.stdout)


class WorkerLoss:
    def __init__(self, config, namespace_uid, journal, expected_fingerprint, *, source_journal=None, archive=None, key_file=None):
        self.config, self.path = config, Path(journal)
        self.value = {**config, "namespace_uid": namespace_uid, "frontend_url": f"http://127.0.0.1:{config['ports']['frontend']}",
            "simulation_username": "replica.admin"}
        self.lab = UpgradeLab(config, namespace_uid)
        self.kube = Kubernetes(self.value, ssh_host="ubuntu@10.10.1.52", known_hosts=ROOT / "tests/tmp/replica-lab-known_hosts")
        assert_fault_scope(self.kube, self.value)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_fingerprint):
            raise ValueError("Expected installed runtime fingerprint required")
        self.fingerprint = expected_fingerprint
        self.journal = json.loads(self.path.read_bytes()) if self.path.exists() else None
        if self.journal and (self.journal["namespace_uid"] != namespace_uid or self.journal["fingerprint"] != self.fingerprint):
            raise ValueError("Journal belongs to another installation")
        self.authority = None
        if any(value is not None for value in (source_journal, archive, key_file)):
            if not all(value is not None for value in (source_journal, archive, key_file)):
                raise ValueError("Explicit verified source, independent archive and key required together")
            from replica_fault_authority import ScopedFaultAuthority
            self.authority = ScopedFaultAuthority(self, source_journal, archive, key_file)
            self.lab = self.authority.lab
        elif self.journal and self.journal.get("fault_authority"):
            raise ValueError("Resume requires the same scoped fault authority")

    def preflight(self, phase):
        if getattr(self, "authority", None) is not None:
            self.authority.check(inject=phase == "inject")
        elif phase == "inject":
            from replica_upgrade import require_legacy_operator_principal
            api = json.loads(self.kube.run("get", "deploy", "api", "-o", "json"))
            refs = [v["secretRef"]["name"] for v in api["spec"]["template"]["spec"]["containers"][0].get("envFrom", []) if "secretRef" in v]
            if len(refs) != 1:
                raise ValueError("One explicit legacy runtime Secret required")
            require_legacy_operator_principal(json.loads(self.kube.run("get", "secret", refs[0], "-o", "json")), "replica")
        if phase == "inject":
            probe = FaultProbe(self.kube, self.value)
            if probe.identity["fingerprint"] != self.fingerprint:
                raise ValueError("Installed runtime fingerprint differs")
            require_quiescent(probe)
            return probe

    def save(self, **updates):
        if self.authority is not None:
            if self.journal.get("fault_authority", self.authority.record) != self.authority.record:
                raise ValueError("Recorded fault authority cannot change")
            self.journal["fault_authority"] = self.authority.record
        self.journal.update(updates)
        temporary = self.path.with_suffix(".pending")
        temporary.write_text(json.dumps(self.journal, indent=2) + "\n")
        temporary.replace(self.path)

    def state(self):
        pod = self.journal["api_pod"]
        current = json.loads(self.kube.run("get", "pod", pod["metadata"]["name"], "-o", "json"))
        if current["metadata"]["uid"] != pod["metadata"]["uid"] or current["metadata"].get("deletionTimestamp"):
            raise ValueError("Selected observer API disappeared")
        result = self.kube.python(pod["metadata"]["name"], SQL_STATE.replace("DOC_ID", repr(self.journal["document"]["id"])))
        if result["fingerprint"] != self.fingerprint or result["epoch"] != self.journal["source_epoch"]:
            raise ValueError("Runtime scope changed")
        return result

    def inject(self):
        import httpx
        if self.journal is not None:
            raise ValueError("Existing journal prohibits repeating failure injection")
        self.preflight("inject")
        objects = self.lab.inventory()
        deployments = {o["metadata"]["name"]: o for o in objects if o["kind"] == "Deployment"}
        if set(deployments) != {"api", "worker", "frontend", "model-stub"}:
            raise ValueError("Unexpected controller set")
        self.journal = {"version": 1, "phase": "prepared", "namespace_uid": self.value["namespace_uid"],
            "fingerprint": self.fingerprint, "operation_id": uuid4().hex, "owner_id": "owned-lab-recovery",
            "old_deployments": deployments, "started_at": datetime.now(timezone.utc).isoformat()}
        self.journal["baseline_documents"] = self.kube.python(role_pods(self.kube.inventory(), "api")[0]["metadata"]["name"], BASELINE_SOURCE)
        self.save()
        self.kube.run("set", "env", "deployment/model-stub", "MODEL_STUB_SCENARIO=delay", "MODEL_STUB_DELAY_SECONDS=30")
        self.kube.run("rollout", "status", "deployment/model-stub", "--timeout=45s")
        objects = self.kube.inventory()
        validate_inventory(objects, self.value)
        models = role_pods(objects, "model-stub")
        if len(models) != 1:
            raise ValueError("Exactly one owned provider required")
        model = models[0]
        service = json.loads(self.kube.run("get", "service", "model-stub", "-o", "json"))
        nodes = json.loads(self.kube.run("get", "nodes", "-o", "json"))["items"]
        node_uids = {n["metadata"]["name"]: n["metadata"]["uid"] for n in nodes}
        writers = [p for role in ("api", "worker", "frontend") for p in role_pods(objects, role)]
        if {p["spec"]["nodeName"] for p in writers + models} != {"okf-policy-control-plane"}:
            raise ValueError("Only the owned single-node recipe is qualified")
        binding = {"service_uid": service["metadata"]["uid"], "service_selector": service["spec"]["selector"],
            "nodes": {"okf-policy-control-plane": node_uids["okf-policy-control-plane"]},
            "deployments": {r: o["metadata"]["uid"] for r, o in deployments.items()},
            "writer_pod_uids": [p["metadata"]["uid"] for p in writers], "provider_pod_uid": model["metadata"]["uid"],
            "provider_image_digest": model["status"]["containerStatuses"][0]["imageID"][-71:]}
        selected = [{"container_id": p["status"]["containerStatuses"][0]["containerID"].removeprefix("containerd://"),
            "pod_uid": p["metadata"]["uid"], "namespace": self.value["namespace"], "node": p["spec"]["nodeName"]}
            for p in role_pods(objects, "worker")]
        # Validate the node-side inspection command before uploading anything.
        crash_source(selected)
        self.save(binding=binding, selected=selected, old_worker_pods=role_pods(objects, "worker"), api_pod=role_pods(objects, "api")[0])
        with httpx.Client(base_url=self.value["frontend_url"], trust_env=False, timeout=25) as client:
            client.get("/").raise_for_status()
            client.post("/api/auth/simulate", json={"username": "replica.admin"}).raise_for_status()
            document = upload(client, "replica-worker-loss")
        self.save(document=document)
        deadline = time.monotonic() + 90
        while True:
            # This first read establishes the current source epoch, without adopting a changed process later.
            result = self.kube.python(self.journal["api_pod"]["metadata"]["name"], SQL_STATE.replace("DOC_ID", repr(document["id"])))
            if result["fingerprint"] != self.fingerprint:
                raise ValueError("Installed runtime changed before fault")
            running = [w for w in result["work"] if w["status"] == "running" and not w["cancel_requested"]]
            if len(result["leases"]) == 1 and result["provider"]["active"] == 1 and len(running) == 1:
                break
            if time.monotonic() > deadline:
                raise ValueError("No in-flight synthetic request observed; no worker was stopped")
            time.sleep(.5)
        self.save(source_epoch=result["epoch"], original_lease=result["leases"][0], original_work=running[0],
            provider_before=result["provider"], phase="fault-intent-recorded")
        # No general stop, PID1 kill, node stop, forced Pod deletion or TTL release.
        stopped = remote(["python3", "-"], source=crash_source(selected))
        self.save(crash_receipt=stopped, phase="workers-crashed")
        after = self.state()
        if not same_reservation(after["leases"], self.journal["original_lease"]) or after["provider"]["active"] != 1:
            raise ValueError("The required in-flight worker-loss observation was not obtained")
        self.save(after_crash=after, phase="active-loss-proven")
        emit("workers-lost-during-provider-call", containers=stopped["stopped_containers"], provider_active=1, retained_slots=1)
        for pod in self.journal["old_worker_pods"]:
            name, uid = pod["metadata"]["name"], pod["metadata"]["uid"]
            current = json.loads(self.kube.run("get", "pod", name, "-o", "json"))
            if current["metadata"]["uid"] != uid:
                raise ValueError("Old worker Pod identity changed")
            self.kube.run("delete", "pod", name, "--wait=false")  # Normal grace period; never --force.
        self.save(phase="waiting-for-quarantine")

    def observe(self):
        self.preflight("observe")
        if self.journal["phase"] not in {"waiting-for-quarantine", "quarantine-proven"}:
            raise ValueError("Recorded active loss must precede quarantine observations")
        deadline = time.monotonic() + 180
        while True:
            state = self.state()
            original = self.journal["original_lease"]
            if not same_reservation(state["leases"], original) or state["provider"]["started"] != self.journal["provider_before"]["started"]:
                raise ValueError("Uncertain request was reallocated or another provider call started")
            if datetime.fromisoformat(state["db_now"]) >= datetime.fromisoformat(original["expires_at"]) and state["provider"]["active"] == 0:
                break
            if time.monotonic() > deadline:
                raise ValueError("Observation timed out; inspect the same journal and reservation")
            time.sleep(3)
        objects = self.kube.inventory()
        validate_inventory(objects, self.value)
        old_uids = {p["metadata"]["uid"] for p in self.journal["old_worker_pods"]}
        if any(p["metadata"]["uid"] in old_uids for p in objects if p.get("kind") == "Pod"):
            raise ValueError("Old worker Pod is still present")
        # Independent API admissions must remain busy even after the DB-clock TTL.
        for pod in role_pods(objects, "api"):
            result = self.kube.python(pod["metadata"]["name"], '''import json
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
from app.services.work.model_budget import acquire_model_budget
from app.services.llm_client import LLMBusyError
s=get_settings();enforce_runtime_identity(s)
try:
 b=acquire_model_budget(s,max_wait=0)
except LLMBusyError:print(json.dumps({'busy':True}))
else:
 b.complete(confirmed=True)
 print(json.dumps({'busy':False}))
''')
            if result != {"busy": True}:
                raise ValueError("Expired uncertain reservation allowed another admission")
        self.save(expired_observation=state, phase="quarantine-proven")
        emit("quarantine-proven", provider_finished=True, original_slot_expired=True, slot_still_owned=True, busy_apis=2)

    def fetch_stop(self):
        assert_fault_scope(self.kube, self.value)
        items = json.loads(self.kube.run("get", "deploy,sts,rs,ds,rc,pods,jobs,cronjobs,services,endpointslices", "-o", "json"))["items"]
        items.append(json.loads(self.kube.run("get", "namespace", self.value["namespace"], "-o", "json")))
        nodes = json.loads(self.kube.run("get", "nodes", "-o", "json"))["items"]
        containers = remote(["docker", "exec", "okf-policy-control-plane", "crictl", "ps", "-a", "-o", "json"])["containers"]
        return items, nodes, containers

    def recover(self):
        self.preflight("recover")
        from app.services.work.coordination import CapacityLease
        from app.services.work.maintenance_control import (
            REQUIRED_WRITERS,
            MaintenanceOperator,
            enter_maintenance,
        )
        from app.services.work.provider_reconciliation import (
            ProviderStopProof,
            leases_digest,
            reconcile_stopped_provider,
        )
        from sqlalchemy import text
        if self.journal["phase"] not in {"quarantine-proven", "held", "reconciled"}:
            raise ValueError("Quarantine proof must precede recovery")
        operation, owner = self.journal["operation_id"], self.journal["owner_id"]
        engine, s3, q = connections(self.config)
        settings = SimpleNamespace(okf_deployment_schema="replicas-v1", app_role="api", audit_db_enabled=True, audit_stdout_mode="disabled",
            model_mode="stub", auth_provider="simulation", embedding_provider="fake", environment="development",
            llm_base_url="http://model-stub:8000/v1", deployment_namespace=self.value["namespace"])
        try:
            with MaintenanceOperator(engine, operation_id=operation, owner_id=owner, settings=settings) as op:
                with op.transaction() as session:
                    identity, _ = sql_source(session.connection(), self.config)
                    if identity["fingerprint"] != self.fingerprint:
                        raise ValueError("Installed runtime changed before recovery")
                    epoch = enter_maintenance(session, operation, kind="recovery", intended_counts={r: 2 for r in ("api", "worker", "frontend")})
                self.save(epoch=epoch, phase="held")
                objects = self.lab.inventory()
                binding = self.journal["binding"]
                uids = set(binding["writer_pod_uids"])
                for pod in objects:
                    if pod["kind"] == "Pod" and pod["metadata"].get("labels", {}).get("okf-role") in {"api", "worker", "frontend"}:
                        uids.add(pod["metadata"]["uid"])
                binding["writer_pod_uids"] = sorted(uids)
                self.save(binding=binding)
                for role in ("frontend", "api", "worker", "model-stub"):
                    obj = json.loads(self.lab.kubectl("get", "deploy", role, "-o", "json"))
                    before = obj["spec"].get("replicas")
                    if obj["metadata"]["uid"] != binding["deployments"][role] or before not in {0, 1 if role == "model-stub" else 2}:
                        raise ValueError("Workload identity or count changed")
                    if before:
                        self.lab.scale(role, before, 0)
                observer = StopObservation(self.fetch_stop, self.value, binding)
                deadline = time.monotonic() + 180
                while True:
                    try:
                        digest = observer.digest()
                        break
                    except ValueError:
                        if time.monotonic() > deadline:
                            raise ValueError("Physical stop is unproven; maintenance remains held") from None
                        time.sleep(2)
                original = self.journal["original_lease"]
                leases = [CapacityLease(**{**{k: original[k] for k in ("id", "resource", "owner_id", "fence")},
                    "expires_at": datetime.fromisoformat(original["expires_at"])})]
                def guard():
                    return ProviderStopProof(operation_id=operation, installation_id=self.config["installation_id"],
                        namespace=self.value["namespace"], namespace_uid=self.value["namespace_uid"], epoch=epoch,
                        provider_pod_uid=binding["provider_pod_uid"], provider_image_digest=binding["provider_image_digest"],
                        writer_pod_uids=binding["writer_pod_uids"], writers={k: "stopped" for k in REQUIRED_WRITERS},
                        unknown_writers=0, unreachable_nodes=0, provider_pods=0, provider_endpoints=0,
                        observation_sha256=observer.digest(), leases_sha256=leases_digest(leases))
                proof = guard()
                if proof.observation_sha256 != digest:
                    raise ValueError("Physical observations changed before reconciliation")
                with op.transaction() as session:
                    count = reconcile_stopped_provider(session, operation, leases, proof=proof, guard=guard)
                    if session.scalar(text("SELECT count(*) FROM replica_capacity_slots WHERE resource='llm' AND owner_id IS NOT NULL")) != 0:
                        raise ValueError("Provider reservation remains")
                with op.transaction() as session:
                    snapshot = corpus(session.connection())
                    if session.scalar(text("SELECT count(*) FROM audit_log WHERE action_type='replica_model_reconcile' AND target_id=:id"), {"id": proof.digest()}) != 1:
                        raise ValueError("Reconciliation audit missing")
                self.save(phase="reconciled", proof=proof.model_dump(mode="json"), proof_digest=proof.digest(), corpus=snapshot)
                emit("provider-reconciled", epoch=epoch, released_slots=count, proof_sha256=proof.digest(), writers_stopped=True)
        finally:
            engine.dispose(); s3.close(); q.close()

    def release(self):
        self.preflight("release")
        if self.journal["phase"] != "reconciled":
            raise ValueError("Audited reconciliation must precede release")
        # Recheck physical facts immediately before submitting the owned release Job.
        observer = StopObservation(self.fetch_stop, self.value, self.journal["binding"])
        if observer.digest() != self.journal["proof"]["observation_sha256"]:
            raise ValueError("Physical stop evidence changed")
        operation, owner = self.journal["operation_id"], self.journal["owner_id"]
        stem = "replica-recovery-" + operation[:12]
        api = self.journal["old_deployments"]["api"]
        container = api["spec"]["template"]["spec"]["containers"][0]
        runtime = next(e["configMapRef"]["name"] for e in container["envFrom"] if "configMapRef" in e)
        identity = next(v["configMap"]["name"] for v in api["spec"]["template"]["spec"]["volumes"] if v["name"] == "runtime-identity")
        if self.authority is not None:
            source, job = self.authority.release_job(api, name=stem + "-release", inputs=stem + "-inputs", operation=operation, owner=owner)
        else:
            source = release_source()
            job = operator_job(api, name=stem + "-release", image=container["image"], runtime=runtime,
                inputs=stem + "-inputs", new_identity=identity, command=["python", "-u", "/run/upgrade/release.py", operation, owner])
        self.lab.write({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": stem + "-inputs"}, "immutable": True,
            "data": {"release.py": source, "corpus.json": json.dumps(self.journal["corpus"])}})
        if self.authority is not None:
            self.authority.submit_job(job)
        else:
            self.lab.write(job)
        receipt = self.lab.job_result(stem + "-release", "REPLICA_UPGRADE_RELEASE:")
        if receipt != {"epoch": self.journal["epoch"], "corpus_unchanged": True, "released": True}:
            raise ValueError("Recovery release receipt differs")
        self.save(phase="released", release_receipt=receipt)
        self.start()

    def start(self):
        self.preflight("start")
        if (self.journal["phase"] not in {"released", "running"}
                or self.journal.get("release_receipt") != {"epoch": self.journal["epoch"], "corpus_unchanged": True, "released": True}):
            raise ValueError("The recorded verified release must precede application start")
        current = {}
        for role, original in self.journal["old_deployments"].items():
            current[role] = json.loads(self.kube.run("get", "deploy", role, "-o", "json"))
            assert_recovery_role(current[role], original, model=role == "model-stub")
        model, original = current["model-stub"], self.journal["old_deployments"]["model-stub"]
        if model["spec"]["template"] != original["spec"]["template"]:
            model["spec"]["template"] = copy.deepcopy(original["spec"]["template"])
            model.pop("status", None)
            model["metadata"].pop("managedFields", None)
            self.lab.write(model, verb="replace")
        for role in ("model-stub", "api", "worker", "frontend"):
            selected = json.loads(self.kube.run("get", "deploy", role, "-o", "json"))
            assert_recovery_role(selected, self.journal["old_deployments"][role], model=role == "model-stub")
            count = 1 if role == "model-stub" else 2
            if selected["spec"]["replicas"] == 0:
                self.lab.scale(role, 0, count)
            self.kube.run("rollout", "status", "deploy/" + role, "--timeout=45s")
        objects = self.kube.inventory()
        ready = validate_inventory(objects, self.value)
        if verify_runtime(self.kube, objects, self.value)["fingerprint"] != self.fingerprint:
            raise ValueError("Recovered identity changed")
        api = role_pods(objects, "api")[0]["metadata"]["name"]
        assert_released_state(self.kube.python(api, released_state_source(self.journal["proof_digest"])), self.journal)
        self.save(phase="running", runtime=ready)
        emit("recovery-running", epoch=self.journal["epoch"], replicas=ready["replicas"], functional_check="pending")

    def verification_client(self):
        import httpx
        return httpx.Client(base_url=self.value["frontend_url"], trust_env=False, timeout=25)

    def authenticate_verification_client(self, client):
        client.post("/api/auth/simulate", json={"username": "replica.admin"}).raise_for_status()

    def fault_summary(self):
        return {"crashed_worker_containers": self.journal["crash_receipt"]["stopped_containers"],
            "provider_active_after_crash": 1}

    def verify(self, report):
        self.preflight("verify")
        from test_scripts.replica_runtime_faults import verify_corpus_search_download
        if self.journal["phase"] not in {"running", "verified"}:
            raise ValueError("Running recovered installation required")
        probe = FaultProbe(self.kube, self.value)
        if probe.identity["fingerprint"] != self.fingerprint:
            raise ValueError("Recovered runtime identity differs")
        document = self.journal["document"]
        began, last_progress = time.monotonic(), time.monotonic()
        with self.verification_client() as client:
            client.get("/").raise_for_status()
            self.authenticate_verification_client(client)
            while True:
                response = client.get("/api/documents/" + document["id"])
                response.raise_for_status()
                state = response.json()
                if state["status"] == "done":
                    if state.get("problem") is not None:
                        raise ValueError("Recovered document has a processing problem")
                    break
                if time.monotonic() - began > 270 or state["status"] in {"paused", "error", "failed"}:
                    raise ValueError("Interrupted document did not recover automatically")
                if time.monotonic() - last_progress >= 30:
                    emit("waiting-for-owned-work-recovery", status=state["status"])
                    last_progress = time.monotonic()
                time.sleep(2)
            observed = probe.state([document["id"]])
            doc_state = observed["documents"][document["id"]]
            api_name = probe.api_pods()[0]["metadata"]["name"]
            released_state = self.kube.python(api_name, released_state_source(self.journal["proof_digest"]))
            assert_released_state(released_state, self.journal)
            docs = self.kube.python(api_name, BASELINE_SOURCE)
            assert_recovered(self.journal["original_work"], doc_state["work"], doc_state["active_count"],
                self.journal["baseline_documents"], docs)
            if len(docs) != len(self.journal["baseline_documents"]) + 1:
                raise ValueError("Unexpected document appeared during the owned scenario")
            verify_corpus_search_download(client, self.journal["baseline_documents"] + [document])
            require_quiescent(probe)
        ready = validate_inventory(self.kube.inventory(), self.value)
        work = next(w for w in doc_state["work"] if w["id"] == self.journal["original_work"]["id"])
        public = {"status": "owned-active-worker-loss-recovery-passed", "scope": "single-node-managed-s3-synthetic-stub",
            "namespace": self.value["namespace"], "namespace_uid": self.value["namespace_uid"],
            "installation_id": self.config["installation_id"], "runtime_fingerprint": self.fingerprint,
            "operation_id": self.journal["operation_id"], "source_epoch": self.journal["source_epoch"], "recovery_epoch": self.journal["epoch"],
            **self.fault_summary(),
            "provider_image_digest": self.journal["binding"]["provider_image_digest"],
            "quarantine": {"provider_finished": True, "db_clock_past_original_expiry": True,
                "original_reservation_retained": True, "busy_independent_apis": 2, "extra_provider_requests": 0},
            "reconciliation": {"proof_sha256": self.journal["proof_digest"], "observation_sha256": self.journal["proof"]["observation_sha256"],
                "live_kubernetes_and_cri_guards": True, "unreachable_nodes": 0, "unknown_writers": 0,
                "provider_pods": 0, "provider_endpoints": 0, "audit_confirmed": True, "held_corpus_verified_before_release": True},
            "recovered_document": {**document, "status": "done", "problem": None, "active_publications": 1,
                "original_fence": self.journal["original_work"]["fence"], "recovered_fence": work["fence"], "search_download_sha256_verified": True},
            "preserved_existing_documents": len(self.journal["baseline_documents"]), "runtime": ready,
            "production_schedule": "disabled", "verified_at": datetime.now(timezone.utc).isoformat(),
            "remaining_runtime_faults": ["postgres_partition", "stale_worker_return", "resource_load_qualification"],
            "limitations": ["not node or host failover", "not a real/external-provider recovery recipe", "no production SLA"]}
        public = self.report_result(public)
        Path(report).write_text(json.dumps(public, indent=2) + "\n")
        self.save(phase="verified", report=str(Path(report).resolve()))
        print(json.dumps(public), flush=True)

    def report_result(self, public):
        return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["preflight", "inject", "observe", "recover", "release", "start", "verify"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--namespace-uid", required=True)
    parser.add_argument("--expected-fingerprint", required=True)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--report")
    for option in ("source-journal", "archive", "key-file"):
        parser.add_argument("--" + option, type=Path)
    parser.add_argument("--apply", action="store_true", required=True)
    args = parser.parse_args()
    try:
        config_path, journal_path = Path(args.config).resolve(strict=True), Path(args.journal).resolve()
        if journal_path.parent != config_path.parent or journal_path == config_path:
            raise ValueError("Journal must use the separate protected config directory")
        recipe = WorkerLoss(json.loads(config_path.read_bytes()), args.namespace_uid, journal_path, args.expected_fingerprint,
            source_journal=args.source_journal, archive=args.archive, key_file=args.key_file)
        if args.phase == "preflight":
            recipe.preflight("inject")
            emit("fault-preflight-passed", fault_injected=False, scoped_authority=recipe.authority is not None)
        elif args.phase == "verify":
            if not args.report:
                raise ValueError("Public report path required")
            recipe.verify(args.report)
        else:
            getattr(recipe, args.phase)()
    except Exception as exc:  # noqa: BLE001 -- raw provider/credential errors stay private.
        frames = traceback.extract_tb(exc.__traceback__)
        print(json.dumps({"worker_loss_refused": type(exc).__name__, "maintenance": "never-released-on-error",
            "locations": [Path(f.filename).name + ":" + str(f.lineno) for f in frames[-3:]]}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
