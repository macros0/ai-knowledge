"""Explicit owned-lab active publication/capacity probes; no real model mutations."""
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
import re
import time
from uuid import uuid4

from test_scripts.replica_runtime_acceptance import (role_pods, validate_inventory, validate_live_inputs, verify_runtime)


def assert_fault_scope(kube, value):
    validate_live_inputs(value)
    uid = value.get("namespace_uid", "")
    if not re.fullmatch(r"[0-9a-f-]{36}", uid):
        raise ValueError("Recorded namespace identity required")
    kube.scope()
    current = json.loads(kube.run("get", "namespace", value["namespace"], "-o", "json"))
    if current["metadata"].get("uid") != uid or current["metadata"].get("deletionTimestamp"):
        raise ValueError("Namespace identity changed")


def require_quiescent(probe):
    state = probe.state()
    if any(state[k] for k in ("model_owners", "running_documents", "running_chats")) or state["provider"]["active"]:
        raise ValueError("An active or uncertain request prevents fixture rollout")
    return state


def assert_interrupted(before, after, *, operation, work_ids):
    if (operation not in {"cancel", "delete"} or not work_ids or before.get("active_count") != 1
            or any(before.get(k) != after.get(k) for k in ("active_generation", "concept_sha256", "active_count"))):
        raise ValueError("Interrupted attempt changed its published base")
    rows = {r["id"]: r for r in after["work"]}
    if any(work_id not in rows or not rows[work_id]["cancel_requested"] for work_id in work_ids):
        raise ValueError("Interrupted work was not fenced")
    if (operation == "delete" and not after.get("deleted")
            or operation == "cancel" and (after.get("deleted") or after.get("status") != "done")):
        raise ValueError("Interrupted document became visible or lost its base")


STATE_SOURCE = '''import json,hashlib,urllib.request
from sqlalchemy import text
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
from app.db.session import session_scope
s=get_settings();enforce_runtime_identity(s)
assert s.model_mode=='stub' and s.auth_provider=='simulation' and s.embedding_provider=='fake'
assert s.llm_base_url=='http://model-stub:8000/v1'
ids=json.loads(DOC_IDS)
with session_scope() as session:
 result={'model_owners':session.scalar(text("SELECT count(*) FROM replica_capacity_slots WHERE resource='llm' AND owner_id IS NOT NULL")),
 'running_documents':session.scalar(text("SELECT count(*) FROM work_items WHERE kind='document' AND status='running' AND NOT cancel_requested")),
 'running_chats':session.scalar(text("SELECT count(*) FROM chat_runs WHERE status='running'")),
 'limit':s.llm_global_max_concurrency,'documents':{}}
 for doc_id in ids:
  doc=session.execute(text("SELECT status,deleted_at FROM documents WHERE id=:id"),{'id':doc_id}).mappings().one()
  pointer=session.execute(text("SELECT active_generation_id,candidate_generation_id FROM document_generation_states WHERE doc_id=:id"),{'id':doc_id}).mappings().one_or_none()
  concepts=session.execute(text("SELECT slug,title,content FROM okf_concepts WHERE doc_id=:id ORDER BY slug"),{'id':doc_id}).mappings()
  result['documents'][doc_id]={'status':doc['status'],'deleted':doc['deleted_at'] is not None,
   'active_generation':pointer['active_generation_id'] if pointer else None,'candidate_generation':pointer['candidate_generation_id'] if pointer else None,
   'active_count':session.scalar(text("SELECT count(*) FROM document_generations WHERE doc_id=:id AND phase='active'"),{'id':doc_id}),
   'concept_sha256':hashlib.sha256(json.dumps([dict(r) for r in concepts],sort_keys=True).encode()).hexdigest(),
   'work':[dict(r) for r in session.execute(text("SELECT id,status,cancel_requested,owner_id,fence FROM work_items WHERE kind='document' AND doc_id=:id ORDER BY created_at,id"),{'id':doc_id}).mappings()]}
with urllib.request.urlopen('http://model-stub:8000/test/status',timeout=3) as response:
 result['provider']=json.load(response)
print(json.dumps(result))
'''


class FaultProbe:
    def __init__(self, kube, value, *, progress=None):
        self.kube, self.value = kube, value
        self.progress = progress or (lambda _event: None)
        self.scope()
        objects = kube.inventory()
        validate_inventory(objects, value)
        self.identity = verify_runtime(kube, objects, value)
        model = json.loads(kube.run("get", "deploy", "model-stub", "-o", "json"))
        containers = model["spec"]["template"]["spec"]["containers"]
        if (model["metadata"].get("labels", {}).get("app.kubernetes.io/instance") != value["application_release"]
                or model["spec"].get("replicas") != 1 or len(containers) != 1
                or not re.fullmatch(r"okf-model-stub:r5-active-[0-9a-f]{12}", containers[0]["image"])):
            raise ValueError("Instrumented owned synthetic model required")
        self.model_container = containers[0]["name"]
        self.model_image = containers[0]["image"]

    def scope(self):
        assert_fault_scope(self.kube, self.value)

    def api_pods(self):
        self.scope()
        objects = self.kube.inventory()
        validate_inventory(objects, self.value)
        return role_pods(objects, "api")

    def run(self, *args):
        self.scope()
        return self.kube.run(*args)

    def state(self, doc_ids=()):
        if any(not re.fullmatch(r"[0-9a-f]{16}", doc) for doc in doc_ids):
            raise ValueError("Owned document receipts required")
        pod = self.api_pods()[0]["metadata"]["name"]
        state = self.kube.python(pod, STATE_SOURCE.replace("DOC_IDS", repr(json.dumps(list(doc_ids)))))
        provider = state["provider"]
        if (set(provider) != {"test_only", "active", "peak_active", "started", "finished"} or provider["test_only"] is not True
                or state["limit"] != 1 or state["model_owners"] > 1 or provider["peak_active"] > 1):
            raise ValueError("Actual provider concurrency exceeded the selected limit")
        return state

    def wait(self, predicate, *, doc_ids=(), seconds=180):
        deadline = time.monotonic() + seconds
        while True:
            state = self.state(doc_ids)
            if predicate(state):
                return state
            if time.monotonic() >= deadline:
                raise ValueError("Required active fault observation timed out")
            time.sleep(.5)

    @contextmanager
    def delayed_model(self):
        require_quiescent(self)
        deployment = json.loads(self.run("get", "deploy", "model-stub", "-o", "json"))
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        if container["image"] != self.model_image or container["name"] != self.model_container:
            raise ValueError("Synthetic provider changed")
        old = {e["name"]: e.get("value") for e in container.get("env", [])
            if e["name"] in {"MODEL_STUB_SCENARIO", "MODEL_STUB_DELAY_SECONDS"}}
        if old.get("MODEL_STUB_SCENARIO", "success") != "success":
            raise ValueError("Successful idle provider baseline required")
        self.run("set", "env", "deployment/model-stub", "MODEL_STUB_SCENARIO=delay", "MODEL_STUB_DELAY_SECONDS=30")
        self.run("rollout", "status", "deployment/model-stub", "--timeout=45s")
        try:
            yield
        finally:
            # Never clear a slot or restart a provider to make a failed test pass.
            require_quiescent(self)
            restored = [f"{name}={old[name]}" if name in old else name + "-"
                for name in ("MODEL_STUB_SCENARIO", "MODEL_STUB_DELAY_SECONDS")]
            self.run("set", "env", "deployment/model-stub", *restored)
            self.run("rollout", "status", "deployment/model-stub", "--timeout=45s")


def upload(client, name):
    from docx import Document
    doc = Document()
    marker = uuid4().hex
    doc.add_heading("Active replica acceptance " + marker, 0)
    doc.add_paragraph(("ТЕСТОВЫЙ концепт проверяет fencing, отмену и общий лимит модели. " + marker + " ") * 10)
    stream = BytesIO()
    doc.save(stream)
    body = stream.getvalue()
    response = client.post("/api/documents", data={"allow_similar": "true"}, files={
        "file": (name + ".docx", body, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    response.raise_for_status()
    doc_id = response.json()["id"]
    if not re.fullmatch(r"[0-9a-f]{16}", doc_id):
        raise ValueError("Upload returned an invalid document receipt")
    return {"id": doc_id, "sha256": sha256(body).hexdigest(), "search_query": marker}


def download_matches(client, doc):
    response = client.get(f"/api/documents/{doc['id']}/download")
    response.raise_for_status()
    if sha256(response.content).hexdigest() != doc["sha256"]:
        raise ValueError("Original bytes changed during fault probe")
    return response.content


def search_ids(client, *, query="ТЕСТОВЫЙ концепт"):
    response = client.post("/api/search", json={"query": query, "mode": "bm25", "top_k": 40})
    response.raise_for_status()
    return {h["doc_id"] for h in response.json()["hits"]}


def source_search_query(body):
    """Use the verified synthetic source, never its ranked shared top-N query."""
    from docx import Document
    try:
        paragraphs = [p.text.strip() for p in Document(BytesIO(body)).paragraphs if p.text.strip()]
    except Exception as exc:
        raise ValueError("Synthetic source cannot supply a search query") from exc
    markers = set(re.findall(r"\b[0-9a-f]{32}\b", "\n".join(paragraphs)))
    if len(markers) == 1:
        return markers.pop()
    # The original recovery fixtures predate UUID markers. Their source heading
    # remains a useful query, but an actual matching doc_id is still mandatory.
    if markers or not paragraphs or len(paragraphs[0]) > 1000:
        raise ValueError("An unambiguous bounded synthetic source query is required")
    return paragraphs[0]


def verify_corpus_search_download(client, documents):
    deleted = [doc for doc in documents if doc.get("deleted_at")]
    broad = search_ids(client) if deleted else set()
    for doc in documents:
        if doc.get("deleted_at"):
            # Retain the historical negative control and the independent 404.
            # New receipts additionally permit a document-specific negative query.
            targeted = search_ids(client, query=doc["search_query"]) if doc.get("search_query") else set()
            if doc["id"] in broad | targeted or client.get(f"/api/documents/{doc['id']}/download").status_code != 404:
                raise ValueError("Trashed document became visible")
        else:
            body = download_matches(client, doc)
            query = source_search_query(body)
            if doc["id"] not in search_ids(client, query=query):
                raise ValueError("A preserved document disappeared from targeted search")


def interruptions(probe, client, bases):
    evidence = []
    for operation, base in zip(("cancel", "delete"), bases, strict=True):
        doc_id = base["id"]
        original = probe.state([doc_id])["documents"][doc_id]
        response = client.post(f"/api/documents/{doc_id}/regenerate")
        response.raise_for_status()
        update = response.json()["update_id"]
        if not isinstance(update, str) or not re.fullmatch(r"[0-9a-f]{32}", update):
            raise ValueError("Explicit update identity required")
        active = probe.wait(lambda s: s["model_owners"] == s["provider"]["active"] == 1
            and any(w["status"] == "running" and not w["cancel_requested"] for w in s["documents"][doc_id]["work"]), doc_ids=[doc_id])
        work_ids = {w["id"] for w in active["documents"][doc_id]["work"] if w["status"] == "running" and not w["cancel_requested"]}
        probe.progress({"phase": "provider-active-before-" + operation, "provider_active": 1, "owned_work": len(work_ids)})
        if operation == "cancel":
            response = client.post(f"/api/documents/{doc_id}/cancel-update", json={"update_id": update})
        else:
            response = client.delete(f"/api/documents/{doc_id}")
        response.raise_for_status()
        after = probe.wait(lambda s: s["model_owners"] == s["provider"]["active"] == 0, doc_ids=[doc_id])
        assert_interrupted(original, after["documents"][doc_id], operation=operation, work_ids=work_ids)
        # Observe again after the real provider has returned; a ready/status check alone cannot prove this.
        observed = probe.state([doc_id])
        assert_interrupted(original, observed["documents"][doc_id], operation=operation, work_ids=work_ids)
        hits = search_ids(client, query=base["search_query"])
        if (operation == "cancel" and doc_id not in hits) or (operation == "delete" and doc_id in hits):
            raise ValueError("Interrupted document has wrong search visibility")
        if operation == "cancel":
            download_matches(client, base)
        elif client.get(f"/api/documents/{doc_id}/download").status_code != 404:
            raise ValueError("Deleted original remains downloadable")
        evidence.append({"operation": operation, "provider_was_active": True, "work_cancel_fenced": True,
            "base_generation_preserved": True, "base_content_sha256_preserved": True, "active_publications": 1,
            "search_visibility_verified": True, "document_id": doc_id})
        probe.progress({"phase": operation + "-publication-fenced", "provider_active": 0})
    return {"cases": evidence, "late_publications": 0, "limit": 1}


def capacity(probe, client):
    require_quiescent(probe)
    started = time.monotonic()
    documents = [upload(client, "replica-capacity-" + str(i)) for i in range(2)]
    ids = [d["id"] for d in documents]
    saturated = probe.wait(lambda s: s["model_owners"] == s["provider"]["active"] == 1
        and sum(any(w["status"] == "running" and not w["cancel_requested"] for w in s["documents"][doc_id]["work"]) for doc_id in ids) == 2,
        doc_ids=ids)
    probe.progress({"phase": "capacity-saturated", "running_documents": 2, "active_provider_calls": 1})
    busy = []
    for pod in probe.api_pods():
        result = probe.kube.python(pod["metadata"]["name"], '''import json
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
from app.services.work.model_budget import acquire_model_budget
from app.services.llm_client import LLMBusyError
s=get_settings();enforce_runtime_identity(s)
try:
 budget=acquire_model_budget(s,max_wait=0)
except LLMBusyError:
 print(json.dumps({'busy':True}))
else:
 budget.complete(confirmed=True) # No provider request was made by this probe.
 print(json.dumps({'busy':False}))
''')
        if result != {"busy": True}:
            raise ValueError("Independent API process over-admitted occupied model capacity")
        busy.append(True)
    # The probes above only acquire/refuse a SQL budget; they never call the
    # provider. Workers may legitimately complete one RPC and start the next
    # while remote Python processes import their dependencies. Lifetime peak
    # concurrency, not an unchanged cumulative start counter, proves the limit.
    occupied = probe.state(ids)
    before, after = saturated["provider"], occupied["provider"]
    if (after["peak_active"] != 1 or after["started"] < before["started"]
            or after["finished"] < before["finished"]
            or after["started"] - after["finished"] != after["active"]):
        raise ValueError("Provider counters changed identity or lost concurrency evidence")
    finished = probe.wait(lambda s: all(s["documents"][doc_id]["status"] == "done" for doc_id in ids)
        and s["model_owners"] == s["provider"]["active"] == 0, doc_ids=ids, seconds=300)
    verify_corpus_search_download(client, documents)
    return {"running_documents_when_saturated": 2, "api_processes_rejected_without_provider_call": len(busy),
        "global_limit": 1, "peak_provider_calls": finished["provider"]["peak_active"], "completed_documents": 2,
        "download_sha256_matches": 2, "search_matches": 2, "seconds": time.monotonic() - started,
        "provider_calls_started_during_admission_observation": after["started"] - before["started"],
        "provider_calls_active_after_admission": after["active"],
        "document_ids": ids}


def probe_active_faults(kube, value, *, progress=None):
    import httpx
    checks = []
    name = "runtime.active_fault_scope"
    try:
        probe = FaultProbe(kube, value, progress=progress)
        require_quiescent(probe)
        checks.append({"name": name, "status": "passed", "evidence": {"namespace_uid": value["namespace_uid"],
            "model_image": probe.model_image, **probe.identity}})
        with httpx.Client(base_url=value["frontend_url"], trust_env=False, timeout=25, follow_redirects=False) as client:
            client.post("/api/auth/simulate", json={"username": value["simulation_username"]}).raise_for_status()
            name = "runtime.active_fault_baseline"
            bases = [upload(client, "replica-interrupt-" + str(i)) for i in range(2)]
            ids = [d["id"] for d in bases]
            probe.wait(lambda s: all(s["documents"][doc_id]["status"] == "done" for doc_id in ids)
                and s["model_owners"] == s["provider"]["active"] == 0, doc_ids=ids)
            with probe.delayed_model():
                name = "runtime.concurrent_cancel_delete"
                evidence = interruptions(probe, client, bases)
                checks.append({"name": name, "status": "passed", "evidence": evidence})
                name = "runtime.model_capacity_exhaustion"
                evidence = capacity(probe, client)
                checks.append({"name": name, "status": "passed", "evidence": evidence})
        checks.append({"name": "runtime.active_fixture_restored", "status": "passed"})
    except Exception as exc:  # noqa: BLE001 -- never report raw Settings/HTTP/remote errors.
        checks.append({"name": name, "status": "failed", "reason": "active_fault_probe_failed", "error_type": type(exc).__name__})
    return checks
