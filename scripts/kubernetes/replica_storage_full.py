"""Bounded native RustFS ENOSPC contract in a separate synthetic memory-only Pod.

This does not certify physical disks, PVC quotas, application publication or HA.
No working namespace, provider, volume or application configuration is mutated.
"""
from __future__ import annotations

import argparse
import base64
import errno
import inspect
import json
import os
import re
import secrets
import sys
import textwrap
import traceback
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replica_backup import ROOT
from replica_ca_rotation import tls_material
from test_scripts.replica_runtime_acceptance import Kubernetes

RUN = "full20261102"
NAMESPACE = "replica-test-" + RUN
CAPACITY = 128 * 1024 * 1024
SERVER_IMAGE = "rustfs/rustfs:1.0.4@sha256:e287e99762a4a3e7b706526e01f2ff755f67db165e9be9f6b907adc6fb6d6ee1"
CLIENT_IMAGE = "okf-backend:replicas-40b7972-559d6cb1e83e"
CLIENT_DIGEST = "43015b36d8bc0d70355d96fbd5c8eaa25e6bd93f0bcf9204af241d71146b03c8"


def require_bounded_tmpfs(mountinfo, capacity):
    matches = [line.split(" - ") for line in mountinfo.splitlines() if len(line.split()) > 5 and line.split()[4] == "/full"]
    if (capacity != 128 * 1024 * 1024 or len(matches) != 1 or len(matches[0]) != 2
            or matches[0][1].split()[:2] != ["tmpfs", "tmpfs"] or "rw" not in matches[0][0].split()[5].split(",")):
        raise ValueError("Only the exact128Mi writable /full tmpfs may be filled")


def write_bounded(write, limit):
    total = 0
    while total < limit:
        try:
            size = write(b"F" * min(1024 * 1024, limit - total))
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                return total
            raise
        if type(size) is not int or not 0 < size <= min(1024 * 1024, limit - total):
            raise ValueError("Filler write stalled or exceeded its bound")
        total += size
    raise ValueError("Filler reached its bound without a real ENOSPC")


def run_cached(root, operation, action, recipe_sha, execute):
    if (not re.fullmatch(r"[0-9a-f]{32}", operation) or not re.fullmatch(r"[0-9a-f]{64}", recipe_sha)
            or action not in {"seed", "arm", "fill", "check-single", "check-multipart", "recover", "verify"}):
        raise ValueError("Invalid native receipt scope")
    root = Path(root).absolute()
    if root.resolve() != root or root.is_symlink():raise ValueError("Native receipt path changed")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stem = root / (operation + "-" + action)
    receipt, started = stem.with_suffix(".json"), stem.with_suffix(".started")
    binding = {"operation": operation, "action": action, "recipe_sha256": recipe_sha}
    if receipt.exists():
        if receipt.is_symlink():raise ValueError("Native receipt is not a private regular file")
        observed = json.loads(receipt.read_bytes())
        if any(observed.get(k) != v for k, v in binding.items()) or not isinstance(observed.get("result"), dict):
            raise ValueError("Native receipt belongs to different inputs")
        return observed["result"]
    try:
        with started.open("x") as stream:
            stream.write(json.dumps(binding)); stream.flush(); os.fsync(stream.fileno())
    except FileExistsError:
        raise ValueError("Unknown native outcome; inspect the same process and receipt") from None
    result = execute()
    if not isinstance(result, dict):raise TypeError("Native result is incomplete")
    pending = stem.with_suffix(".pending")
    with pending.open("x") as stream:
        stream.write(json.dumps({**binding, "result": result})); stream.flush(); os.fsync(stream.fileno())
    pending.replace(receipt)
    return result


def probe_pod(namespace, operation):
    if namespace != NAMESPACE or not re.fullmatch(r"[0-9a-f]{32}", operation):
        raise ValueError("Explicit owned memory-only test scope required")
    stem = "storage-full-" + operation[:12]
    security = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}
    resources = {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "500m", "memory": "512Mi"}}
    return {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": stem, "namespace": namespace,
        "labels": {"okf-run-id": RUN, "okf-operation-id": operation, "okf-purpose": "replica-test", "okf-installation-id": "6" * 32}},
        "spec": {"automountServiceAccountToken": False, "restartPolicy": "Never", "terminationGracePeriodSeconds": 30,
            "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001,
                "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{"name": "artifacts", "image": SERVER_IMAGE, "imagePullPolicy": "IfNotPresent", "args": ["/data"],
                "securityContext": security, "resources": resources,
                "env": [{"name": "RUSTFS_ADDRESS", "value": ":9000"}, {"name": "RUSTFS_CONSOLE_ENABLE", "value": "false"},
                    {"name": "RUSTFS_OBS_LOG_DIRECTORY", "value": "/logs"}, {"name": "RUSTFS_TLS_PATH", "value": "/run/tls"},
                    *[{"name": name, "valueFrom": {"secretKeyRef": {"name": stem + "-auth", "key": key}}} for name, key in
                        (("RUSTFS_ACCESS_KEY", "S3_ROOT_ACCESS_KEY"), ("RUSTFS_SECRET_KEY", "S3_ROOT_SECRET_KEY"))]],
                "volumeMounts": [{"name": "data", "mountPath": "/data"}, {"name": "logs", "mountPath": "/logs"},
                    {"name": "temp", "mountPath": "/tmp"}, {"name": "tls", "mountPath": "/run/tls", "readOnly": True}],
                "readinessProbe": {"httpGet": {"path": "/health", "port": 9000, "scheme": "HTTPS"},
                    "periodSeconds": 3, "timeoutSeconds": 1, "successThreshold": 1, "failureThreshold": 3}},
                {"name": "probe", "image": CLIENT_IMAGE, "imagePullPolicy": "IfNotPresent", "securityContext": security, "resources": resources,
                    "command": ["python", "-u", "-c", "import time;time.sleep(7200)"],
                    "env": [{"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
                        *[{"name": key, "valueFrom": {"secretKeyRef": {"name": stem + "-auth", "key": key}}}
                            for key in ("S3_ROOT_ACCESS_KEY", "S3_ROOT_SECRET_KEY")]],
                    "volumeMounts": [{"name": "data", "mountPath": "/full"}, {"name": "scratch", "mountPath": "/scratch"},
                        {"name": "trust", "mountPath": "/run/trust", "readOnly": True}]}],
            "volumes": [{"name": name, "emptyDir": {"medium": "Memory", "sizeLimit": size}} for name, size in
                (("data", "128Mi"), ("logs", "16Mi"), ("temp", "16Mi"), ("scratch", "32Mi"))]
                + [{"name": "tls", "secret": {"secretName": stem + "-tls", "defaultMode": 288,
                    "items": [{"key": "tls.crt", "path": "rustfs_cert.pem"}, {"key": "tls.key", "path": "rustfs_key.pem"}]}},
                   {"name": "trust", "configMap": {"name": stem + "-roots", "defaultMode": 292}}]}}


def require_original_pod(value, expected, *, ready=True):
    status = value.get("status", {})
    containers = status.get("containerStatuses", [])
    expected_images = {c["name"]: c.get("imageID") for c in expected.get("status", {}).get("containerStatuses", [])}
    if (value["metadata"]["uid"] != expected["metadata"]["uid"] or value["spec"] != expected["spec"]
            or value["metadata"].get("labels") != expected["metadata"].get("labels") or value["metadata"].get("deletionTimestamp")
            or status.get("phase") != "Running" or len(containers) != 2 or any(c.get("restartCount") != 0 for c in containers)
            or any(c.get("imageID") != expected_images.get(c["name"]) for c in containers)
            or ready and not any(c["type"] == "Ready" and c["status"] == "True" for c in status.get("conditions", []))
            or not ready and any("running" not in c.get("state", {}) for c in containers)):
        raise ValueError("Original unchanged fault Pod/processes required")


def native_source(action, journal):
    data = {"action": action, "operation": journal["operation_id"], "filler": journal.get("filler"), "seed": journal.get("seed")}
    source = "import errno,hashlib,io,json,os,re,stat\nfrom pathlib import Path\n"
    source += inspect.getsource(require_bounded_tmpfs) + "\n" + inspect.getsource(write_bounded) + "\n"
    source += "data=json.loads(" + repr(json.dumps(data)) + ")\n"
    source += '''root=Path('/full')
assert root.resolve()==root and not root.is_symlink()
fs=os.statvfs(root);capacity=fs.f_blocks*fs.f_frsize
require_bounded_tmpfs(Path('/proc/self/mountinfo').read_text(),capacity)
assert root.stat().st_dev!=Path('/scratch').stat().st_dev
path=root/('.okf-full-'+data['operation'])
assert re.fullmatch('[0-9a-f]{32}',data['operation'])
def facts():
 value=path.lstat()
 assert stat.S_ISREG(value.st_mode) and value.st_nlink==1 and value.st_uid==os.getuid()
 if data['filler']:assert value.st_ino==data['filler']['inode'] and value.st_dev==data['filler']['device']
 return {'inode':value.st_ino,'device':value.st_dev,'size':value.st_size,'capacity':capacity}
def free():
 value=os.statvfs(root);return value.f_bavail*value.f_frsize
action=data['action']
if action=='arm':
 try:
  descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600);os.close(descriptor)
 except FileExistsError:assert path.lstat().st_size==0 or data['filler']
 result=facts()
elif action=='fill':
 before=facts();assert before['size']<=capacity
 descriptor=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW)
 try:
  opened=os.fstat(descriptor);assert opened.st_ino==before['inode'] and opened.st_dev==before['device']
  written=write_bounded(lambda block:os.write(descriptor,block),capacity-before['size']+1)
  os.fsync(descriptor)
 finally:os.close(descriptor)
 result={**facts(),'written':written,'errno':errno.ENOSPC,'free_bytes':free()};assert result['free_bytes']==0
elif action=='recover':
 if path.exists():facts();path.unlink()
 result={'filler_removed':not path.exists(),'free_bytes':free()};assert result['free_bytes']>64*1024*1024
else:
 import boto3
 from botocore.config import Config
 from app.services.artifacts.contract import BlobUnavailable
 from app.services.artifacts.s3 import S3BlobStore
 from app.services.storage import StorageFullError
 client=boto3.client('s3',endpoint_url='https://127.0.0.1:9000',aws_access_key_id=os.environ['S3_ROOT_ACCESS_KEY'],
  aws_secret_access_key=os.environ['S3_ROOT_SECRET_KEY'],region_name='us-east-1',verify='/run/trust/ca.pem',
  config=Config(signature_version='s3v4',s3={'addressing_style':'path'},retries={'max_attempts':0},proxies={},connect_timeout=5,read_timeout=8))
 protocol=[]
 def record(model,http_response,parsed,**_kwargs):
  code=parsed.get('Error',{}).get('Code')
  if code:
   message=parsed.get('Error',{}).get('Message','').lower()
   protocol.append({'operation':model.name,'status':http_response.status_code,'code':code if re.fullmatch('[A-Za-z0-9_.-]{1,80}',code) else 'redacted',
    'message_has_enospc':'no space left on device' in message,'message_has_errno28':'os error 28' in message})
 client.meta.events.register('after-call.s3',record)
 bucket='replica-test-full20261102'
 store=S3BlobStore(client=client,endpoint='https://127.0.0.1:9000',bucket=bucket,prefix='replica_test/full20261102/',
  store_id='owned-bounded-full',scratch_root=Path('/scratch/spool'))
 def body(kind):return hashlib.shake_256((data['operation']+kind).encode()).digest({'seed':8192,'single':1024*1024,'multipart':8*1024*1024+1}[kind])
 def identity(kind):return hashlib.sha256((data['operation']+kind).encode()).hexdigest()[:32]
 def put(kind):return store.put(io.BytesIO(body(kind)),object_id=identity(kind),media_type='application/octet-stream')
 def check(ref,kind):
  with store.open(ref) as stream:assert stream.read()==body(kind)
  with store.open(ref,start=19,end=80) as stream:assert stream.read()==body(kind)[19:80]
 if action=='seed':
  try:client.create_bucket(Bucket=bucket)
  except client.exceptions.BucketAlreadyOwnedByYou:pass
  ref=put('seed');check(ref,'seed');result=vars(ref)
 elif action.startswith('check-'):
  facts();assert free()==0
  kind=action.removeprefix('check-')
  try:put(kind)
  except (StorageFullError,BlobUnavailable) as exc:failure=type(exc).__name__
  else:raise ValueError('Full provider unexpectedly acknowledged publication')
  from app.services.artifacts.contract import BlobRef
  check(BlobRef(**data['seed']),'seed')
  result={'kind':kind,'acknowledged':False,'failure':failure,'protocol':protocol,'existing_bytes_unchanged':True,'free_bytes':free()}
 elif action=='verify':
  from app.services.artifacts.contract import BlobRef
  check(BlobRef(**data['seed']),'seed')
  refs={kind:put(kind) for kind in ('single','multipart')}
  for kind,ref in refs.items():check(ref,kind);assert put(kind)==ref
  uploads=client.list_multipart_uploads(Bucket=bucket,Prefix='replica_test/full20261102/').get('Uploads',[])
  assert not uploads
  result={'seed_unchanged':True,'same_ids_retried':True,'conditional_replay':True,'verified_range':True,'pending_multipart_uploads':len(uploads),
   'object_sha256':{kind:ref.sha256 for kind,ref in refs.items()}}
 else:raise ValueError('Unknown native phase')
 client.close()
return result
'''
    recipe = sha256(source.encode()).hexdigest()
    code = "import json,os,re,traceback\nfrom pathlib import Path\n" + inspect.getsource(run_cached) + "\ndef execute():\n" + textwrap.indent(source, " ")
    code += "\ntry:\n recipe=" + repr(recipe) + "\n result=run_cached(Path('/scratch/receipts')," + repr(data["operation"]) + "," + repr(action) + ",recipe,execute)\n"
    code += " print(json.dumps({'result':result,'recipe_sha256':recipe,'operation':" + repr(data["operation"]) + ",'action':" + repr(action) + "}),flush=True)\n" + '''
except Exception as exc:
 print(json.dumps({'native_refused':type(exc).__name__,'locations':[str(f.lineno) for f in traceback.extract_tb(exc.__traceback__)[-3:]]}),flush=True)
 raise SystemExit(2)
'''
    return code, recipe


def full_report(journal):
    full, verified, recovered = (journal.get(key, {}) for key in ("full_receipt", "verification", "recovery_receipt"))
    refusals = [journal.get(key, {}) for key in ("single_refusal", "multipart_refusal")]
    if (journal.get("phase") not in {"verified", "stopped"} or full.get("capacity") != CAPACITY or full.get("errno") != errno.ENOSPC
            or full.get("free_bytes") != 0 or recovered.get("filler_removed") is not True or recovered.get("free_bytes", 0) <= CAPACITY // 2
            or any(value.get("acknowledged") is not False or value.get("existing_bytes_unchanged") is not True
                or value.get("failure") not in {"StorageFullError", "BlobUnavailable"} or not value.get("protocol") for value in refusals)
            or any(verified.get(key) is not True for key in ("seed_unchanged", "same_ids_retried", "conditional_replay", "verified_range"))
            or verified.get("pending_multipart_uploads") != 0):
        raise ValueError("Required native ENOSPC/refusal/integrity/recovery evidence is incomplete")
    return {"status": "owned-bounded-s3-enospc-contract-passed", "namespace": NAMESPACE, "namespace_uid": journal["namespace_uid"],
        "pod_uid": journal["pod_uid"], "operation_id": journal["operation_id"], "images": journal["images"],
        "scope": "native-rustfs-s3blobstore-memory-only-synthetic-contract", "full": full,
        "single_refusal": refusals[0], "multipart_refusal": refusals[1], "recovery": recovered, "verification": verified,
        "native_recipe_sha256": journal.get("native_recipe_sha256", {}),
        "provider_capacity_error_classification": "structured-storage-full" if all(v["failure"] == "StorageFullError" for v in refusals) else "opaque-server-error",
        "physical_disk_or_pvc_quota_certified": False, "application_publication_under_full_storage_certified": False,
        "production_schedule": "disabled", "verified_at": datetime.now(timezone.utc).isoformat(),
        "limitations": ["isolated128Mi tmpfs, not production PVC quota or physical disk failure", "not PostgreSQL/Qdrant/RWX/external/mixed or node/host HA",
            "RustFS1.0.4 returns opaque500 InternalError; do not classify every such error as capacity exhaustion",
            "native S3 adapter contract only; full API/worker publication acceptance remains separate"]}


class StorageFull:
    def __init__(self, journal):
        self.path = Path(journal).resolve()
        if self.path.parent != ROOT / "tests/tmp/replica-recovery-private":
            raise ValueError("The protected owned lab journal directory is required")
        self.journal = json.loads(self.path.read_bytes()) if self.path.exists() else None
        self.kube = Kubernetes({"context": "kind-okf-policy", "namespace": NAMESPACE, "run_id": RUN},
            ssh_host="ubuntu@10.10.1.52", known_hosts=ROOT / "tests/tmp/replica-lab-known_hosts")

    def save(self, **values):
        self.journal.update(values)
        pending = self.path.with_suffix(".pending"); pending.write_text(json.dumps(self.journal, indent=2) + "\n"); pending.replace(self.path)

    def namespace(self):
        value = json.loads(self.kube.run("get", "ns", NAMESPACE, "-o", "json"))
        labels = value["metadata"].get("labels", {})
        if (value["metadata"]["uid"] != self.journal["namespace_uid"] or value["metadata"].get("deletionTimestamp")
                or labels.get("okf-operation-id") != self.journal["operation_id"] or labels.get("okf-run-id") != RUN
                or labels.get("okf-purpose") != "replica-test" or labels.get("app.kubernetes.io/managed-by") != "okf-replica-acceptance"):
            raise ValueError("Owned fault namespace changed")

    def create(self, value):
        value["metadata"].setdefault("labels", {})["okf-operation-id"] = self.journal["operation_id"]
        observed = self.kube.run("get", value["kind"], value["metadata"]["name"], "--ignore-not-found", "-o", "json")
        if observed.strip():
            previous = json.loads(observed)
            if previous["metadata"].get("labels", {}).get("okf-operation-id") != self.journal["operation_id"]:
                raise ValueError("Existing fault resource belongs to another operation")
            for key in ("data", "immutable"):
                if value.get(key) != previous.get(key):raise ValueError("Fault material changed")
            return previous
        return json.loads(self.kube.run("create", "-f", "-", "-o", "json", source=json.dumps(value)))

    def prepare(self):
        if self.journal is None:
            if self.kube.run("get", "ns", NAMESPACE, "--ignore-not-found", "-o", "json").strip():
                raise ValueError("Existing namespace is never adopted")
            operation = uuid4().hex
            self.journal = {"phase": "preparing", "operation_id": operation, "namespace": NAMESPACE,
                "material": tls_material({"namespace": NAMESPACE, "storage_release": "replica-storage-full"}, operation),
                "access": "OKFFULL" + uuid4().hex.upper(), "secret": secrets.token_urlsafe(36)}
            self.save()
        if self.journal["phase"] != "preparing":raise ValueError("Resume the recorded native fault phase")
        namespace = self.create({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE,
            "labels": {"okf-purpose": "replica-test", "okf-run-id": RUN, "app.kubernetes.io/managed-by": "okf-replica-acceptance",
                "pod-security.kubernetes.io/enforce": "restricted", "pod-security.kubernetes.io/enforce-version": "v1.36"}}})
        if self.journal.get("namespace_uid", namespace["metadata"]["uid"]) != namespace["metadata"]["uid"]:
            raise ValueError("Fault namespace replaced during preparation")
        self.save(namespace_uid=namespace["metadata"]["uid"]); self.namespace()
        self.create({"apiVersion": "v1", "kind": "ResourceQuota", "metadata": {"name": "bounded-full"},
            "spec": {"hard": {"pods": "1", "persistentvolumeclaims": "0", "requests.cpu": "1", "limits.cpu": "2", "requests.memory": "1Gi", "limits.memory": "2Gi"}}})
        self.create({"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "isolated"},
            "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"], "ingress": [], "egress": []}})
        stem = "storage-full-" + self.journal["operation_id"][:12]
        self.create({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": stem + "-roots"}, "immutable": True,
            "data": {"ca.pem": self.journal["material"]["ca"]}})
        for suffix, data in (("tls", self.journal["material"]["leaves"]["artifacts"]),
                ("auth", {"S3_ROOT_ACCESS_KEY": base64.b64encode(self.journal["access"].encode()).decode(), "S3_ROOT_SECRET_KEY": base64.b64encode(self.journal["secret"].encode()).decode()})):
            self.create({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": stem + "-" + suffix}, "immutable": True, "data": data})
        desired = probe_pod(NAMESPACE, self.journal["operation_id"])
        pod = self.create(desired)
        if self.journal.get("pod_uid", pod["metadata"]["uid"]) != pod["metadata"]["uid"]:
            raise ValueError("Fault Pod replaced during preparation")
        self.save(pod_name=stem, pod_uid=pod["metadata"]["uid"])
        self.kube.run("wait", "--for=condition=Ready", "pod/" + stem, "--timeout=45s")
        pod = json.loads(self.kube.run("get", "pod", stem, "-o", "json"))
        if pod["metadata"]["uid"] != self.journal["pod_uid"]:raise ValueError("Fault Pod replaced during preparation")
        if any(pod["spec"].get(k) for k in ("hostNetwork", "hostPID", "hostIPC", "shareProcessNamespace")):
            raise ValueError("Fault Pod may not join another process/network namespace")
        for key in ("automountServiceAccountToken", "volumes", "securityContext", "restartPolicy"):
            if pod["spec"][key] != desired["spec"][key]:raise ValueError("Admitted fault isolation changed")
        for observed, expected in zip(pod["spec"]["containers"], desired["spec"]["containers"], strict=True):
            if any(observed.get(key) != value for key, value in expected.items()):raise ValueError("Admitted probe inputs changed")
        images = {c["name"]: c["imageID"] for c in pod["status"]["containerStatuses"]}
        if not images["probe"].endswith(CLIENT_DIGEST):raise ValueError("Deployed backend digest differs")
        self.save(phase="prepared", pod=pod, images=images)
        self.current()
        print(json.dumps({"phase": "bounded-full-prepared", "namespace_uid": self.journal["namespace_uid"], "capacity": CAPACITY}), flush=True)

    def current(self, *, ready=True):
        self.namespace()
        pod = json.loads(self.kube.run("get", "pod", self.journal["pod_name"], "-o", "json"))
        require_original_pod(pod, self.journal["pod"], ready=ready)
        self.ready_node(pod)
        return pod

    def ready_node(self, pod):
        node = json.loads(self.kube.run("get", "node", pod["spec"]["nodeName"], "-o", "json"))
        if not any(c["type"] == "Ready" and c["status"] == "True" for c in node.get("status", {}).get("conditions", [])):
            raise ValueError("Fault Pod node/process outcome is uncertain")

    def native(self, action):
        self.current(ready=action in {"seed", "arm", "verify"})
        source, recipe = native_source(action, self.journal)
        raw = self.kube.run("exec", "-i", self.journal["pod_name"], "-c", "probe", "--", "python", "-",
            source=source)
        envelope = json.loads(raw)
        if (envelope.get("native_refused") or envelope.get("recipe_sha256") != recipe
                or envelope.get("operation") != self.journal["operation_id"] or envelope.get("action") != action or not isinstance(envelope.get("result"), dict)):
            raise ValueError("Native fault receipt refused")
        self.save(native_recipe_sha256={**self.journal.get("native_recipe_sha256", {}), action: recipe})
        return envelope["result"]

    def run_phase(self, action):
        phases = {"seed": ({"prepared", "seed-requested"}, "seeded", "seed"), "arm": ({"seeded", "arm-requested"}, "armed", "filler"),
            "fill": ({"armed", "fill-requested", "full"}, "full", "full_receipt"),
            "check-single": ({"full", "check-single-requested"}, "single-checked", "single_refusal"),
            "check-multipart": ({"single-checked", "check-multipart-requested"}, "checked", "multipart_refusal"),
            "recover": ({"checked", "recover-requested"}, "recovered", "recovery_receipt"), "verify": ({"recovered", "verify-requested", "verified"}, "verified", "verification")}
        allowed, next_phase, key = phases[action]
        if self.journal["phase"] not in allowed:raise ValueError("Recorded fault phase differs")
        self.save(phase=action + "-requested")
        result = self.native(action)
        self.save(phase=next_phase, **{key: result})
        print(json.dumps({"phase": "bounded-full-" + next_phase, "result": result}), flush=True)

    def report(self, report):
        self.current()
        result = full_report(self.journal)
        path = Path(report).resolve()
        if path.parent != ROOT / "docs/superpowers/reports":raise ValueError("Owned public report directory required")
        if path.exists() and json.loads(path.read_bytes()).get("operation_id") != self.journal["operation_id"]:
            raise ValueError("Refusing to replace another report")
        pending = path.with_suffix(".pending"); pending.write_text(json.dumps(result, indent=2) + "\n"); pending.replace(path)
        self.save(report=str(path))
        print(json.dumps({"phase": "bounded-full-contract-passed", "application_full_gate_closed": False}), flush=True)

    def stop(self):
        if self.journal["phase"] not in {"verified", "stopped"} or not self.journal.get("report"):
            raise ValueError("Verified native contract report required before normal cleanup")
        self.namespace()
        raw = self.kube.run("get", "pod", self.journal["pod_name"], "--ignore-not-found", "-o", "json")
        if raw.strip():
            pod = json.loads(raw)
            if (pod["metadata"]["uid"] != self.journal["pod_uid"] or pod["spec"] != self.journal["pod"]["spec"]
                    or pod["metadata"].get("labels") != self.journal["pod"]["metadata"].get("labels")):
                raise ValueError("Fault cleanup Pod identity changed")
            self.ready_node(pod)
            if not pod["metadata"].get("deletionTimestamp"):
                self.kube.run("delete", "pod/" + self.journal["pod_name"], "--wait=false")
            self.kube.run("wait", "--for=delete", "pod/" + self.journal["pod_name"], "--timeout=45s")
        self.save(phase="stopped")
        print(json.dumps({"phase": "bounded-full-stopped", "only_ephemeral_test_data_removed": True}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "seed", "arm", "fill", "check-single", "check-multipart", "recover", "verify", "report", "stop"])
    parser.add_argument("--journal", type=Path, required=True); parser.add_argument("--apply", action="store_true", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        operation = StorageFull(args.journal)
        if args.phase == "prepare":operation.prepare()
        elif args.phase == "report":operation.report(args.report)
        elif args.phase == "stop":operation.stop()
        else:operation.run_phase(args.phase)
    except Exception as exc:  # noqa: BLE001 -- no provider/auth/private exception text.
        print(json.dumps({"storage_full_refused": type(exc).__name__, "locations": [Path(f.filename).name + ":" + str(f.lineno) for f in traceback.extract_tb(exc.__traceback__)[-3:]]}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
