"""Bounded three-DOCX worker comparison and authenticated per-frontend bursts.

Only the owned synthetic lab is supported. Measurements include observer cost,
stub inference and fake embeddings; they are not customer sizing or linear-scale
certification. No production schedules, resource limits or model limits change.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replica_worker_loss import BASELINE_SOURCE, WorkerLoss, emit
from test_scripts.replica_runtime_acceptance import (
    role_pods,
    validate_inventory,
    verify_runtime,
)
from test_scripts.replica_runtime_faults import (
    FaultProbe,
    require_quiescent,
    upload,
    verify_corpus_search_download,
)


def latencies(values):
    if not values or any(type(n) not in {int, float} or not math.isfinite(n) or n < 0 for n in values):
        raise ValueError("Finite nonnegative latency samples required")
    ordered = sorted(values)
    return {"count": len(values), "p50_ms": ordered[math.ceil(len(values) * .5) - 1],
        "p95_ms": ordered[math.ceil(len(values) * .95) - 1], "max_ms": ordered[-1]}


def resource_delta(before, after):
    elapsed = after["at"] - before["at"]
    if (before["uid"] != after["uid"] or elapsed <= 0 or after["cpu_usage_usec"] < before["cpu_usage_usec"]
            or after["cpu_throttled_usec"] < before["cpu_throttled_usec"] or after["oom_kill"] != before["oom_kill"]
            or after["memory_limit"] != before["memory_limit"] or after["memory_peak"] > after["memory_limit"]):
        raise ValueError("A replaced/reset/OOM container is not a valid resource comparison")
    return {"average_cpu_millicores": round((after["cpu_usage_usec"] - before["cpu_usage_usec"]) / elapsed / 1000, 3),
        "cpu_throttled_delta_usec": after["cpu_throttled_usec"] - before["cpu_throttled_usec"],
        "memory_current_bytes": after["memory_current"], "memory_peak_bytes": after["memory_peak"],
        "memory_limit_bytes": after["memory_limit"], "memory_peak_fraction_of_limit": round(after["memory_peak"] / after["memory_limit"], 4),
        "oom_kill_delta": 0, "sample_interval_seconds": round(elapsed, 3)}


def assert_complete(ids, documents):
    if ({d["id"] for d in documents} != set(ids) or len(documents) != len(ids)
            or any(d["status"] != "done" or d.get("problem") is not None or d["active_count"] != 1 for d in documents)):
        raise ValueError("Only a full nondegraded single-publication batch counts as throughput")


def resource_policies(cases):
    policies = {}
    for case in cases:
        for pod in case["pods"]:
            role = pod["metadata"]["labels"]["okf-role"]
            resources = pod["spec"]["containers"][0]["resources"]
            if role in policies and policies[role] != resources:
                raise ValueError("Resource policy changed between worker-count cases")
            policies[role] = resources
    if not policies:
        raise ValueError("Measured Pod resource policies required")
    return policies


STATS = '''import json,urllib.request
from sqlalchemy import text
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
from app.db.session import session_scope
s=get_settings();identity=enforce_runtime_identity(s)
ids=IDS
with session_scope() as session:
 rows=[dict(r) for r in session.execute(text("SELECT d.id,d.status,d.problem,d.size AS size_bytes,(SELECT count(*) FROM document_generations g WHERE g.doc_id=d.id AND g.phase='active') AS active_count FROM documents d WHERE d.id=ANY(:ids)"),{'ids':ids}).mappings()] if ids else []
 owners=session.scalar(text("SELECT count(*) FROM replica_capacity_slots WHERE resource='llm' AND owner_id IS NOT NULL"))
 running=session.execute(text("SELECT doc_id,owner_id FROM work_items WHERE kind='document' AND status='running' AND NOT cancel_requested")).mappings().all()
 result={'documents':rows,'model_owners':owners,'running_documents':len(running),
 'own_running':sum(r['doc_id'] in ids for r in running),'own_distinct_owners':len({r['owner_id'] for r in running if r['doc_id'] in ids}),
 'epoch':s._maintenance_epoch,'fingerprint':identity.fingerprint,'model_limit':s.llm_global_max_concurrency,
 'pipeline_workers_per_pod':s.pipeline_max_workers}
with urllib.request.urlopen('http://model-stub:8000/test/status',timeout=3) as response:result['provider']=json.load(response)
print(json.dumps(result))
'''

CGROUP_PY = '''import json
from pathlib import Path
p=Path('/sys/fs/cgroup')
def n(f):return int((p/f).read_text().strip())
def kv(f):return {a:int(b) for a,b in (line.split() for line in (p/f).read_text().splitlines())}
cpu=kv('cpu.stat');events=kv('memory.events')
print(json.dumps({'memory_current':n('memory.current'),'memory_peak':n('memory.peak'),'memory_limit':n('memory.max'),
'cpu_usage_usec':cpu['usage_usec'],'cpu_throttled_usec':cpu.get('throttled_usec',0),'oom_kill':events['oom_kill']}))
'''

CGROUP_JS = "const fs=require('node:fs');const r=f=>fs.readFileSync('/sys/fs/cgroup/'+f,'utf8').trim();const n=f=>Number(r(f));const kv=f=>Object.fromEntries(r(f).split(String.fromCharCode(10)).map(s=>{const [k,v]=s.split(' ');return [k,Number(v)];}));const cpu=kv('cpu.stat'),events=kv('memory.events');console.log(JSON.stringify({memory_current:n('memory.current'),memory_peak:n('memory.peak'),memory_limit:n('memory.max'),cpu_usage_usec:cpu.usage_usec,cpu_throttled_usec:cpu.throttled_usec||0,oom_kill:events.oom_kill}));"

FRONTEND_JS = '''const root='http://127.0.0.1:3000';
const login=await fetch(root+'/api/auth/simulate',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({username:'replica.admin'}),signal:AbortSignal.timeout(10000)});
if(!login.ok)throw Error('Synthetic login failed');
const cookies=login.headers.getSetCookie().map(s=>s.split(';')[0]).join('; ');if(!cookies)throw Error('Session cookie missing');
const times=[],statuses=[];let index=0;
await Promise.all(Array.from({length:4},async()=>{while(index++<20){const at=performance.now();const response=await fetch(root+'/api/documents?limit=20',{headers:{cookie:cookies},cache:'no-store',signal:AbortSignal.timeout(10000)});const body=await response.json();statuses.push(response.status);if(response.status!==200||!Array.isArray(body.documents))throw Error('Authenticated list failed');times.push(performance.now()-at);}}));
console.log(JSON.stringify({milliseconds:times,statuses,concurrency:4,session_authenticated:true}));
'''


class ReplicaLoad(WorkerLoss):
    def sql(self, ids=()):
        pod = self.journal["api_pod"]
        current = json.loads(self.kube.run("get", "pod", pod["metadata"]["name"], "-o", "json"))
        if current["metadata"]["uid"] != pod["metadata"]["uid"] or current["metadata"].get("deletionTimestamp"):
            raise ValueError("Load observer API was replaced")
        result = self.kube.python(pod["metadata"]["name"], STATS.replace("IDS", repr(list(ids))))
        if (result["fingerprint"] != self.fingerprint or result["epoch"] != self.journal["epoch"] or result["model_limit"] != 1
                or result["pipeline_workers_per_pod"] != 1
                or result["model_owners"] > 1 or result["provider"]["peak_active"] > 1):
            raise ValueError("Identity, epoch or global model capacity changed")
        return result

    def idle(self):
        state = self.sql()
        if state["model_owners"] or state["running_documents"] or state["provider"]["active"]:
            raise ValueError("Idle confirmed provider and document workers required before scaling")

    def sample(self, pods):
        objects = self.kube.inventory()
        current = {p["metadata"]["uid"]: p for p in objects if p.get("kind") == "Pod"}
        result = {}
        for pod in pods:
            uid, name = pod["metadata"]["uid"], pod["metadata"]["name"]
            now = current.get(uid, {})
            original = pod["status"]["containerStatuses"][0]
            actual = now.get("status", {}).get("containerStatuses", [{}])[0]
            if (now.get("metadata", {}).get("deletionTimestamp") or any(actual.get(k) != original.get(k)
                    for k in ("containerID", "imageID", "restartCount")) or actual.get("ready") is not True):
                raise ValueError("Load container changed, restarted or became unready")
            if pod["metadata"]["labels"]["okf-role"] == "frontend":
                data = json.loads(self.kube.run("exec", "-i", name, "--", "node", "-", source=CGROUP_JS))
            else:
                data = self.kube.python(name, CGROUP_PY)
            if (set(data) != {"memory_current", "memory_peak", "memory_limit", "cpu_usage_usec", "cpu_throttled_usec", "oom_kill"}
                    or any(type(x) is not int or x < 0 for x in data.values()) or not data["memory_limit"]):
                raise ValueError("Bounded cgroup-v2 counters required")
            result[uid] = {**data, "uid": uid, "at": time.monotonic(), "role": pod["metadata"]["labels"]["okf-role"]}
        return result

    def prepare(self):
        if self.journal is not None:
            raise ValueError("Existing load journal must be observed, not recreated")
        probe = FaultProbe(self.kube, self.value)
        initial = require_quiescent(probe)
        if probe.identity["fingerprint"] != self.fingerprint:
            raise ValueError("Installed runtime differs")
        objects = self.kube.inventory()
        api = role_pods(objects, "api")[0]
        epoch = self.kube.python(api["metadata"]["name"], "from app.config import get_settings;from app.deployment.runtime_identity import enforce_runtime_identity;import json;s=get_settings();enforce_runtime_identity(s);print(json.dumps(s._maintenance_epoch))")
        self.journal = {"version": 1, "phase": "prepared", "namespace_uid": self.value["namespace_uid"], "fingerprint": self.fingerprint,
            "epoch": epoch, "api_pod": api, "cases": {}, "baseline_documents": self.kube.python(api["metadata"]["name"], BASELINE_SOURCE),
            "provider_baseline": initial["provider"]}
        self.save()

    def case(self, workers):
        import httpx
        name = "workers-" + str(workers)
        if workers not in {1, 2} or name in self.journal["cases"]:
            raise ValueError("Each measured case runs once; observe a started case without reupload")
        if workers == 2 and self.journal["cases"].get("workers-1", {}).get("status") != "passed":
            raise ValueError("Single-worker comparison must finish first")
        self.idle()
        before = json.loads(self.lab.kubectl("get", "deploy", "worker", "-o", "json"))["spec"]["replicas"]
        if before != workers:
            self.lab.scale("worker", before, workers)
        self.kube.run("rollout", "status", "deployment/worker", "--timeout=45s")
        objects = self.kube.inventory()
        counts = {"frontend": 2, "api": 2, "worker": workers}
        inventory = validate_inventory(objects, self.value, expected_counts=counts)
        if verify_runtime(self.kube, objects, self.value)["fingerprint"] != self.fingerprint:
            raise ValueError("Scaled runtime identity differs")
        pods = [p for role in counts for p in role_pods(objects, role)]
        first = self.sample(pods)
        self.journal["cases"][name] = {"status": "uploading", "workers": workers, "documents": [], "started": datetime.now(timezone.utc).isoformat(),
            "first_samples": first, "pods": pods, "inventory": inventory, "observations": [], "http_upload_ms": []}
        self.save(phase=name)
        at = time.monotonic()
        with httpx.Client(base_url=self.value["frontend_url"], trust_env=False, timeout=25) as client:
            client.post("/api/auth/simulate", json={"username": "replica.admin"}).raise_for_status()
            for i in range(3):
                tick = time.monotonic()
                document = upload(client, "replica-load-" + name + "-" + str(i))
                case = self.journal["cases"][name]
                case["documents"].append(document)
                case["http_upload_ms"].append((time.monotonic() - tick) * 1000)
                self.save()
        self.journal["cases"][name]["status"] = "running"
        self.save()
        self.observe(workers, measured_from=at)

    def observe(self, workers, *, measured_from=None):
        import httpx
        name = "workers-" + str(workers)
        case = self.journal["cases"][name]
        if case["status"] != "running" or len(case["documents"]) != 3:
            raise ValueError("Exactly the recorded batch must be observed")
        ids = [d["id"] for d in case["documents"]]
        deadline = time.monotonic() + 300
        def frontend(pod):
            value = json.loads(self.kube.run("exec", "-i", pod["metadata"]["name"], "--", "node", "--input-type=module", source=FRONTEND_JS))
            if value["statuses"] != [200] * 20 or value["session_authenticated"] is not True:
                raise ValueError("A frontend burst failed")
            return {"pod_uid": pod["metadata"]["uid"], "concurrency": 4, "authenticated": True, **latencies(value["milliseconds"])}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(frontend, p) for p in case["pods"] if p["metadata"]["labels"]["okf-role"] == "frontend"]
            while True:
                state = self.sql(ids)
                samples = self.sample(case["pods"])
                case["last_samples"] = samples
                case["observations"].append({"own_running": state["own_running"], "distinct_owners": state["own_distinct_owners"],
                    "model_owners": state["model_owners"], "provider": state["provider"]})
                self.save()
                if any(d["status"] in {"paused", "error", "failed"} or d["problem"] is not None for d in state["documents"]):
                    raise ValueError("Load document failed or degraded")
                if len(state["documents"]) == 3 and all(d["status"] == "done" for d in state["documents"]):
                    assert_complete(ids, state["documents"])
                    break
                if time.monotonic() > deadline:
                    raise ValueError("Observation timed out; inspect the same recorded documents")
                emit("load-observation", workers=workers, done=sum(d["status"] == "done" for d in state["documents"]), running=state["own_running"])
                time.sleep(2)
            bursts = [f.result() for f in futures]
        if measured_from is None:
            raise ValueError("Completion observed after interruption; do not publish interrupted timing as throughput")
        elapsed = time.monotonic() - measured_from
        with httpx.Client(base_url=self.value["frontend_url"], trust_env=False, timeout=25) as client:
            client.post("/api/auth/simulate", json={"username": "replica.admin"}).raise_for_status()
            verify_corpus_search_download(client, case["documents"])
        self.idle()
        case.update({"status": "passed", "seconds": elapsed, "documents_per_minute": 180 / elapsed,
            "upload_latency": latencies(case["http_upload_ms"]), "frontend_bursts": bursts,
            "documents_state": state["documents"], "peak_running": max(x["own_running"] for x in case["observations"]),
            "peak_distinct_owners": max(x["distinct_owners"] for x in case["observations"]),
            "resources": [{"pod_uid": uid, "role": first["role"], **resource_delta(first, case["last_samples"][uid])}
                for uid, first in case["first_samples"].items()]})
        self.save()
        emit("load-case-passed", workers=workers, documents=3, seconds=elapsed, documents_per_minute=case["documents_per_minute"])

    def verify(self, report):
        import httpx
        if set(self.journal["cases"]) != {"workers-1", "workers-2"} or any(c["status"] != "passed" for c in self.journal["cases"].values()):
            raise ValueError("Both measured cases must pass")
        if self.journal["cases"]["workers-2"]["peak_distinct_owners"] != 2:
            raise ValueError("Two worker owners were not observed doing document work")
        self.idle()
        objects = self.kube.inventory()
        ready = validate_inventory(objects, self.value)
        if verify_runtime(self.kube, objects, self.value)["fingerprint"] != self.fingerprint:
            raise ValueError("Final identity differs")
        docs = self.kube.python(self.journal["api_pod"]["metadata"]["name"], BASELINE_SOURCE)
        by_id = {d["id"]: d for d in docs}
        if any(by_id.get(d["id"]) != d for d in self.journal["baseline_documents"]):
            raise ValueError("Existing corpus changed under load")
        batches = [d for c in self.journal["cases"].values() for d in c["documents"]]
        if len(docs) != len(self.journal["baseline_documents"]) + len(batches):
            raise ValueError("Unexpected document appeared under load")
        with httpx.Client(base_url=self.value["frontend_url"], trust_env=False, timeout=25) as client:
            client.post("/api/auth/simulate", json={"username": "replica.admin"}).raise_for_status()
            verify_corpus_search_download(client, self.journal["baseline_documents"] + batches)
        public_cases = []
        for case in self.journal["cases"].values():
            public_cases.append({k: case[k] for k in ("workers", "documents", "seconds", "documents_per_minute", "upload_latency",
                "frontend_bursts", "documents_state", "peak_running", "peak_distinct_owners", "resources")})
        ratio = self.journal["cases"]["workers-2"]["documents_per_minute"] / self.journal["cases"]["workers-1"]["documents_per_minute"]
        policies = resource_policies(self.journal["cases"].values())
        public = {"status": "owned-small-load-passed", "namespace": self.value["namespace"], "namespace_uid": self.value["namespace_uid"],
            "installation_id": self.config["installation_id"], "fingerprint": self.fingerprint, "epoch": self.journal["epoch"],
            "cases": public_cases, "tested_resources_by_role": policies, "consumer_threads_per_worker": 1,
            "observed_throughput_ratio_two_to_one_workers": ratio, "global_model_limit": 1,
            "preserved_existing_documents": len(self.journal["baseline_documents"]), "runtime": ready, "production_schedule": "disabled",
            "includes_observer_overhead": True, "memory_peak_is_container_lifetime": True, "verified_at": datetime.now(timezone.utc).isoformat(),
            "limitations": ["three synthetic DOCX per case, one sequential trial each", "model stub and fake embeddings", "single-node lab",
                "frontend latency measured inside each Pod through its BFF and API Service", "application containers only; storage sizing remains separate",
                "not production sizing, model quality/capacity, statistical speedup or linear scalability certification"]}
        Path(report).write_text(json.dumps(public, indent=2) + "\n")
        self.save(phase="verified", report=str(Path(report).resolve()))
        print(json.dumps(public), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "single", "dual", "observe-single", "observe-dual", "verify"])
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
            raise ValueError("Separate protected journal required")
        recipe = ReplicaLoad(json.loads(config.read_bytes()), args.namespace_uid, journal, args.expected_fingerprint)
        if args.phase == "prepare":
            recipe.prepare()
        elif args.phase == "verify":
            if not args.report:
                raise ValueError("Public report path required")
            recipe.verify(args.report)
        elif args.phase.startswith("observe-"):
            recipe.observe(1 if args.phase.endswith("single") else 2)
        else:
            recipe.case(1 if args.phase == "single" else 2)
    except Exception as exc:  # noqa: BLE001 -- redact provider errors and preserve the same operation.
        print(json.dumps({"load_refused": type(exc).__name__, "action": "inspect_same_journal_no_automatic_capacity_clear",
            "locations": [Path(f.filename).name + ":" + str(f.lineno) for f in traceback.extract_tb(exc.__traceback__)[-3:]]}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
