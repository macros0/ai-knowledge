"""Explicit owned-lab smoke; missing fault/recovery scenarios remain required."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import ipaddress
import json
from pathlib import Path
import re
import shlex
import subprocess
import time
from urllib.parse import urlsplit
from uuid import uuid4

ROLES = ("frontend", "api", "worker")


def validate_live_inputs(value):
    run = value.get("run_id", "")
    if (not isinstance(run, str) or not re.fullmatch(r"[a-z0-9]{8,32}", run)
            or value.get("namespace") != f"replica-test-{run}"):
        raise ValueError("Explicit isolated run required")
    for key in ("context", "application_release"):
        item = value.get(key)
        if not isinstance(item, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", item):
            raise ValueError("Explicit Kubernetes identity required")
    if not isinstance(value.get("installation_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", value["installation_id"]):
        raise ValueError("Explicit installed identity required")
    url = urlsplit(value.get("frontend_url", ""))
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port
            or url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment):
        raise ValueError("Owned loopback frontend tunnel required")
    username = value.get("simulation_username")
    if not isinstance(username, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", username):
        raise ValueError("Explicit simulation identity required")


def role_pods(objects, role):
    return [p for p in objects if p.get("kind") == "Pod"
        and p["metadata"].get("labels", {}).get("okf-role") == role
        and not p["metadata"].get("deletionTimestamp")]


def validate_inventory(objects, value, *, expected_counts=None):
    counts = {r: 2 for r in ROLES} if expected_counts is None else expected_counts
    if set(counts) != set(ROLES) or any(type(n) is not int or not 1 <= n <= 2 for n in counts.values()):
        raise ValueError("Explicit one-or-two role counts required")
    release = value["application_release"]
    evidence = {"replicas": {}, "api_endpoints": 0, "images": {}, "restarts": {}}
    for role in ROLES:
        count = counts[role]
        deployments = [o for o in objects if o.get("kind") == "Deployment" and o["metadata"]["name"] == role]
        if len(deployments) != 1:
            raise ValueError("Role deployment missing")
        deployment = deployments[0]
        labels = deployment["metadata"].get("labels", {})
        status = deployment.get("status", {})
        if (labels.get("app.kubernetes.io/instance") != release or labels.get("okf-run-id") != value["run_id"]
                or deployment["spec"].get("replicas") != count
                or status.get("observedGeneration") != deployment["metadata"].get("generation")
                or any(status.get(k) != count for k in ("readyReplicas", "updatedReplicas", "availableReplicas"))):
            raise ValueError("Owned expected-count rollout is not ready")
        pods = role_pods(objects, role)
        if len(pods) != count:
            raise ValueError("Expected current role Pods required")
        images, restarts = set(), 0
        for pod in pods:
            statuses = pod.get("status", {}).get("containerStatuses", [])
            if (pod["metadata"].get("labels", {}).get("app.kubernetes.io/instance") != release
                    or pod.get("status", {}).get("phase") != "Running" or len(statuses) != 1
                    or statuses[0].get("ready") is not True):
                raise ValueError("Role Pod is not ready or belongs to another release")
            image = statuses[0].get("imageID", "")
            if not re.search(r"sha256:[0-9a-f]{64}$", image):
                raise ValueError("Runtime image digest missing")
            images.add(image[-71:])
            restarts += statuses[0].get("restartCount", 0)
        if len(images) != 1:
            raise ValueError("Role images differ")
        evidence["replicas"][role] = count
        evidence["images"][role] = next(iter(images))
        evidence["restarts"][role] = restarts
    expected = {p["metadata"]["uid"] for p in role_pods(objects, "api")}
    actual = set()
    for obj in objects:
        if obj.get("kind") == "EndpointSlice" and obj["metadata"].get("labels", {}).get("kubernetes.io/service-name") == "backend":
            for endpoint in obj.get("endpoints", []):
                if endpoint.get("conditions", {}).get("ready") is not True:
                    continue
                ref = endpoint.get("targetRef", {})
                if ref.get("kind") != "Pod" or ref.get("uid") not in expected:
                    raise ValueError("Backend Service contains a non-API endpoint")
                actual.add(ref["uid"])
    if actual != expected:
        raise ValueError("Backend Service does not contain both APIs")
    evidence["api_endpoints"] = len(actual)
    return evidence


class Kubernetes:
    def __init__(self, value, *, ssh_host=None, known_hosts=None):
        self.value = value
        self.ssh_host, self.known_hosts = ssh_host, known_hosts
        if ssh_host is not None:
            if (not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*@[a-zA-Z0-9][a-zA-Z0-9.-]*", ssh_host)
                    or known_hosts is None or not Path(known_hosts).is_file()):
                raise ValueError("Explicit SSH identity and pinned known hosts required")

    def run(self, *args, source=None):
        argv = ["kubectl", "--context", self.value["context"], "-n", self.value["namespace"], *args]
        if self.ssh_host:
            argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=yes",
                "-o", f"UserKnownHostsFile={Path(self.known_hosts).absolute()}", self.ssh_host, shlex.join(argv)]
        result = subprocess.run(argv, input=source, text=True, capture_output=True, timeout=55)
        if result.returncode:
            raise ValueError("Owned Kubernetes probe failed")
        if len(result.stdout.encode()) > 4 * 1024 * 1024:
            raise ValueError("Probe output limit exceeded")
        return result.stdout

    def scope(self):
        obj = json.loads(self.run("get", "namespace", self.value["namespace"], "-o", "json"))
        labels = obj["metadata"].get("labels", {})
        if (labels.get("okf-run-id") != self.value["run_id"]
                or labels.get("okf-purpose") != "replica-test"
                or labels.get("app.kubernetes.io/managed-by") != "okf-replica-acceptance"):
            raise ValueError("Namespace ownership differs")

    def inventory(self):
        return json.loads(self.run("get", "deployments,pods,endpointslices", "-o", "json"))["items"]

    def python(self, pod, source):
        return json.loads(self.run("exec", "-i", pod, "--", "python", "-", source=source))


def verify_runtime(kube, objects, value):
    expected = {k: value[k] for k in ("namespace", "run_id", "installation_id")}
    source = '''import json
from sqlalchemy import select,func
from sqlalchemy.engine import make_url
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity,SCHEMA_REVISION
from app.db.session import session_scope
from app.db.models import ReplicaCapacitySlot
s=get_settings()
expected=json.loads(EXPECTED)
assert s.deployment_namespace==expected['namespace']
assert make_url(s.db_url).database=='replica_test_'+expected['run_id']
assert s.qdrant_collection=='replica_test_'+expected['run_id']
assert s.artifact_s3_bucket==expected['namespace']
assert s.artifact_s3_prefix in {'replica_test/'+expected['run_id']+'/', 'replica_test/'+expected['run_id']+'/application/'}
assert s.model_mode=='stub' and s.auth_provider=='simulation' and s.embedding_provider=='fake'
assert s.llm_base_url=='http://model-stub:8000/v1'
identity=enforce_runtime_identity(s)
assert identity.installation_id==expected['installation_id']
with session_scope() as session:
 owners=session.scalar(select(func.count()).select_from(ReplicaCapacitySlot).where(ReplicaCapacitySlot.resource=='llm',ReplicaCapacitySlot.owner_id.is_not(None)))
print(json.dumps({'fingerprint':identity.fingerprint,'schema_revision':SCHEMA_REVISION,'model_owners':owners}))
'''.replace("EXPECTED", repr(json.dumps(expected)))
    receipts = [kube.python(p["metadata"]["name"], source) for p in role_pods(objects, "api") + role_pods(objects, "worker")]
    if len({r["fingerprint"] for r in receipts}) != 1 or len({r["schema_revision"] for r in receipts}) != 1:
        raise ValueError("Runtime identities differ")
    return {"fingerprint": receipts[0]["fingerprint"], "schema_revision": receipts[0]["schema_revision"]}


def shared_session(kube, objects, value):
    addresses = [str(ipaddress.ip_address(p["status"]["podIP"])) for p in role_pods(objects, "api")]
    if len(set(addresses)) != 2:
        raise ValueError("Distinct API addresses required")
    # Cookies stay inside the frontend process and never enter the report/stdout.
    source = '''const ips=IPS; const username=USERNAME;
const response=await fetch(`http://${ips[0]}:8000/api/auth/simulate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username})});
if(response.status!==200)throw Error('Login refused');
const cookie=response.headers.getSetCookie().map(x=>x.split(';')[0]).join('; ');
for(const ip of ips){const r=await fetch(`http://${ip}:8000/api/auth/me`,{headers:{cookie}});const v=await r.json();if(r.status!==200||v.user?.username!==username)throw Error('Shared session failed');}
console.log(JSON.stringify({shared_session:true,distinct_apis:2}));
'''.replace("IPS", json.dumps(addresses)).replace("USERNAME", json.dumps(value["simulation_username"]))
    pod = role_pods(objects, "frontend")[0]["metadata"]["name"]
    result = json.loads(kube.run("exec", "-i", pod, "--", "node", "--input-type=module", source=source))
    if result != {"shared_session": True, "distinct_apis": 2}:
        raise ValueError("Shared session probe failed")
    return result


def resources(kube, objects):
    """Container cgroup samples, including probe overhead; no sizing guarantee."""
    samples = {}
    files = {"memory_current_bytes": "memory.current", "memory_peak_bytes": "memory.peak"}
    for role in ROLES:
        samples[role] = []
        for pod in role_pods(objects, role):
            if role == "frontend":
                source = "const fs=require('node:fs');const files=" + json.dumps(files) + ";const out={};for(const [key,file] of Object.entries(files))out[key]=Number(fs.readFileSync('/sys/fs/cgroup/'+file,'utf8'));console.log(JSON.stringify(out));"
                result = json.loads(kube.run("exec", pod["metadata"]["name"], "--", "node", "-e", source))
            else:
                source = "import json;from pathlib import Path;files=" + repr(files) + ";print(json.dumps({key:int((Path('/sys/fs/cgroup')/file).read_text()) for key,file in files.items()}))"
                result = json.loads(kube.run("exec", pod["metadata"]["name"], "--", "python", "-c", source))
            if set(result) != set(files) or any(type(v) is not int or v <= 0 for v in result.values()):
                raise ValueError("Unknown cgroup measurements")
            samples[role].append(result)
    return {"container_samples": samples, "includes_probe_overhead": True, "production_sizing": False}


def storage_network_boundary(kube, objects, value):
    targets = []
    for component, port in (("postgres", 5432), ("qdrant", 6333), ("artifacts", 9000)):
        pods = [o for o in objects if o.get("kind") == "Pod"
            and o["metadata"].get("labels", {}).get("okf-storage-component") == component
            and o["metadata"].get("labels", {}).get("okf-installation-id") == value["installation_id"]
            and not o["metadata"].get("deletionTimestamp")]
        if len(pods) != 1 or pods[0].get("status", {}).get("phase") != "Running":
            raise ValueError("Owned managed storage Pod missing")
        address = ipaddress.ip_address(pods[0]["status"]["podIP"])
        if address.is_loopback or not address.is_private:
            raise ValueError("Owned Pod network address required")
        targets.append([str(address), port])
    # A refused connection to a dead server must not masquerade as a policy
    # denial. First establish positive TCP access to these exact Pod endpoints.
    source = "import socket,json;targets=" + repr(targets) + ";\nfor host,port in targets:\n with socket.create_connection((host,port),timeout=3):pass\nprint(json.dumps({'reachable':len(targets)}))"
    result = kube.python(role_pods(objects, "api")[0]["metadata"]["name"], source)
    if result != {"reachable": 3}:
        raise ValueError("Storage TCP positive control failed")
    source = '''import net from 'node:net';const targets=TARGETS;
for(const [host,port] of targets){const denied=await new Promise(resolve=>{const socket=net.createConnection({host,port});socket.setTimeout(1500);socket.once('connect',()=>{socket.destroy();resolve(false);});socket.once('error',()=>{socket.destroy();resolve(true);});socket.once('timeout',()=>{socket.destroy();resolve(true);});});if(!denied)throw Error('Frontend can reach storage');}
console.log(JSON.stringify({denied:targets.length}));
'''.replace("TARGETS", json.dumps(targets))
    for pod in role_pods(objects, "frontend"):
        result = json.loads(kube.run("exec", "-i", pod["metadata"]["name"], "--", "node", "--input-type=module", source=source))
        if result != {"denied": 3}:
            raise ValueError("Frontend storage boundary failed")
    return {"api_positive_endpoints": 3, "frontend_denied_endpoints": 6, "frontend_pods": 2}


def document_roundtrip(value):
    import httpx
    from docx import Document

    documents, latencies = [], []
    started = time.monotonic()
    with httpx.Client(base_url=value["frontend_url"], trust_env=False, timeout=20, follow_redirects=False) as client:
        client.get("/").raise_for_status()
        client.post("/api/auth/simulate", json={"username": value["simulation_username"]}).raise_for_status()
        for index in range(2):
            doc = Document()
            marker = uuid4().hex
            doc.add_heading(f"Replica acceptance {marker}", 0)
            doc.add_paragraph((f"ТЕСТОВЫЙ концепт проверяет публикацию документа номер {index}, {marker}. "
                "Учебные данные для поиска и целостности исходного документа. ") * 8)
            stream = BytesIO()
            doc.save(stream)
            body = stream.getvalue()
            before = time.monotonic()
            response = client.post("/api/documents", data={"allow_similar": "true"}, files={
                "file": (f"replica-{marker}.docx", body, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
            response.raise_for_status()
            doc_id = response.json()["id"]
            if not re.fullmatch(r"[0-9a-f]{16}", doc_id):
                raise ValueError("Invalid document receipt")
            documents.append((doc_id, sha256(body).hexdigest(), marker))
            latencies.append(time.monotonic() - before)
        deadline = time.monotonic() + 120
        while True:
            states = []
            for doc_id, _digest, _marker in documents:
                response = client.get(f"/api/documents/{doc_id}")
                response.raise_for_status()
                doc = response.json()
                if doc["status"] in {"failed", "error", "paused"} or doc.get("problem") is not None:
                    raise ValueError("Document processing failed or degraded")
                states.append(doc["status"])
            if all(s == "done" for s in states):
                break
            if time.monotonic() >= deadline:
                raise ValueError("Document completion deadline exceeded")
            time.sleep(2)
        for doc_id, digest, marker in documents:
            response = client.get(f"/api/documents/{doc_id}/download")
            response.raise_for_status()
            if sha256(response.content).hexdigest() != digest:
                raise ValueError("Source integrity differs")
            response = client.post("/api/search", json={"query": marker, "mode": "bm25", "top_k": 40})
            response.raise_for_status()
            if doc_id not in {hit["doc_id"] for hit in response.json()["hits"]}:
                raise ValueError("Completed document missing from targeted search")
    duration = time.monotonic() - started
    return {"documents": 2, "download_sha256_matches": 2, "search_matches": 2,
        "duration_seconds": duration, "upload_seconds": latencies,
        "documents_per_minute": 120 / duration, "model": "stub", "embeddings": "fake"}


def idle_rollout(kube, value):
    """Normal owned Pod deletion only; never a drain/active-provider proof."""
    def idle():
        kube.scope()
        objects = kube.inventory()
        validate_inventory(objects, value)
        source = '''import json
from sqlalchemy import select,func
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
from app.db.session import session_scope
from app.db.models import WorkItem,ReplicaCapacitySlot
enforce_runtime_identity(get_settings())
with session_scope() as s:
 print(json.dumps({'documents':s.scalar(select(func.count()).select_from(WorkItem).where(WorkItem.kind=='document',WorkItem.status=='running')),'providers':s.scalar(select(func.count()).select_from(ReplicaCapacitySlot).where(ReplicaCapacitySlot.resource=='llm',ReplicaCapacitySlot.owner_id.is_not(None)))}))
'''
        state = kube.python(role_pods(objects, "api")[0]["metadata"]["name"], source)
        if state != {"documents": 0, "providers": 0}:
            raise ValueError("Active or uncertain provider work prevents idle fault probe")
        return objects

    idle()
    for role in ("api", "worker"):
        kube.scope()
        kube.run("scale", f"deployment/{role}", "--current-replicas=2", "--replicas=1")
        kube.run("rollout", "status", f"deployment/{role}", "--timeout=45s")
        kube.run("scale", f"deployment/{role}", "--current-replicas=1", "--replicas=2")
        kube.run("rollout", "status", f"deployment/{role}", "--timeout=45s")
        # Scaling down can leave terminating Pods briefly; these never count
        # as two ready API endpoints in the following ownership checks.
    replaced = []
    for role in ("api", "worker"):
        objects = idle()
        pod = role_pods(objects, role)[0]
        name, uid = pod["metadata"]["name"], pod["metadata"]["uid"]
        current = json.loads(kube.run("get", "pod", name, "-o", "json"))
        if current["metadata"]["uid"] != uid:
            raise ValueError("Selected owned Pod changed")
        kube.run("delete", "pod", name, "--wait=true", "--timeout=45s")
        kube.run("rollout", "status", f"deployment/{role}", "--timeout=45s")
        objects = kube.inventory()
        validate_inventory(objects, value)
        if uid in {p["metadata"]["uid"] for p in role_pods(objects, role)}:
            raise ValueError("Selected Pod was not replaced")
        replaced.append(role)
    return {"manual_scale": {"api": [2, 1, 2], "worker": [2, 1, 2]},
        "idle_pod_replacements": replaced, "intended_counts": {role: 2 for role in ROLES},
        "active_provider_loss_tested": False}


def probe_runtime(value, *, ssh_host=None, known_hosts=None, idle_faults=False, active_faults=False):
    checks = []
    name = "runtime.scope"
    try:
        validate_live_inputs(value)
        kube = Kubernetes(value, ssh_host=ssh_host, known_hosts=known_hosts)
        kube.scope()
        checks.append({"name": name, "status": "passed"})
        name = "runtime.role_inventory"
        objects = kube.inventory()
        evidence = validate_inventory(objects, value)
        checks.append({"name": name, "status": "passed", "evidence": evidence})
        name = "runtime.common_identity"
        evidence = verify_runtime(kube, objects, value)
        checks.append({"name": name, "status": "passed", "evidence": evidence})
        name = "runtime.shared_session"
        evidence = shared_session(kube, objects, value)
        checks.append({"name": name, "status": "passed", "evidence": evidence})
        name = "runtime.frontend_storage_boundary"
        evidence = storage_network_boundary(kube, objects, value)
        checks.append({"name": name, "status": "passed", "evidence": evidence})
        name = "runtime.document_roundtrip"
        evidence = document_roundtrip(value)
        checks.append({"name": name, "status": "passed", "evidence": evidence})
        name = "runtime.memory_samples"
        evidence = resources(kube, kube.inventory())
        checks.append({"name": name, "status": "passed", "evidence": evidence})
        if idle_faults:
            name = "runtime.idle_rollout"
            evidence = idle_rollout(kube, value)
            checks.append({"name": name, "status": "passed", "evidence": evidence})
            # Verify the same corpus flow against the replacements. A ready
            # replacement alone is not evidence of functional storage/auth.
            name = "runtime.after_idle_replacement"
            evidence = document_roundtrip(value)
            checks.append({"name": name, "status": "passed", "evidence": evidence})
        if active_faults:
            from test_scripts.replica_runtime_faults import probe_active_faults
            checks.extend(probe_active_faults(kube, value))
    except Exception:
        # Remote stderr, raw HTTP responses and Settings/provider errors may
        # contain credentials. They never become report fields or tracebacks.
        checks.append({"name": name, "status": "failed", "reason": "live_probe_failed"})
    return checks
