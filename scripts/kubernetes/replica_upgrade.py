"""Explicit owned-kind same-schema image rollout; a failure retains maintenance.

All credentials stay in the protected config/Secret. The new image computes its
identity inside Linux. Normal role counts return only after held SQL verification.
"""
from __future__ import annotations

import argparse
import base64
import copy
import inspect
import json
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replica_backup import ROOT, Lab, connections, sql_source

CORPUS_TABLES = ("documents", "okf_concepts", "document_chunks", "document_generation_states",
    "document_generations", "document_sources", "document_staging", "artifact_objects", "artifact_bindings", "work_items", "work_progress")


def corpus(connection):
    from hashlib import sha256

    from sqlalchemy import text
    result, total = {}, 0
    for table in CORPUS_TABLES:
        rows = list(connection.execute(text('SELECT * FROM "' + table + '" LIMIT 10001')).mappings())
        if len(rows) > 10000:
            raise ValueError("Owned lab corpus bound exceeded")
        values = sorted(json.dumps(dict(row), sort_keys=True, separators=(",", ":"), default=str) for row in rows)
        total += sum(len(row.encode()) for row in values)
        if total > 64 * 1024 * 1024:
            raise ValueError("Owned lab corpus byte bound exceeded")
        result[table] = {"count": len(rows), "sha256": sha256("\n".join(values).encode()).hexdigest()}
    return result


def stage_role(value, *, old_runtime, new_runtime, old_identity, new_identity, image):
    result = copy.deepcopy(value)
    if result["spec"].get("replicas") != 0:
        raise ValueError("Role must be stopped before staging its upgrade")
    containers = result["spec"]["template"]["spec"]["containers"]
    if len(containers) != 1:
        raise ValueError("Explicit single-container role required")
    container = containers[0]
    refs = [e["configMapRef"] for e in container.get("envFrom", []) if "configMapRef" in e and e["configMapRef"]["name"] in {old_runtime, new_runtime}]
    if len(refs) != 1 and result["metadata"]["name"] != "frontend":
        raise ValueError("Old runtime configuration binding differs")
    for ref in refs:
        ref["name"] = new_runtime
    if result["metadata"]["name"] in {"api", "worker"}:
        volumes = [v for v in result["spec"]["template"]["spec"]["volumes"] if v["name"] == "runtime-identity"]
        if len(volumes) != 1 or volumes[0].get("configMap", {}).get("name") not in {old_identity, new_identity}:
            raise ValueError("Old identity binding differs")
        volumes[0]["configMap"]["name"] = new_identity
        container["image"] = image
    result.pop("status", None)
    result["metadata"].pop("managedFields", None)
    return result


class UpgradeLab(Lab):
    def write(self, resource, *, verb="create"):
        self.assert_namespace()
        value = copy.deepcopy(resource)
        if value["kind"] not in {"ConfigMap", "Job", "Deployment"}:
            raise ValueError("Unsupported upgrade resource")
        meta = value["metadata"]
        if meta.get("namespace", self.config["namespace"]) != self.config["namespace"]:
            raise ValueError("Foreign upgrade resource")
        meta["namespace"] = self.config["namespace"]
        if value["kind"] == "Deployment" and meta.get("labels", {}).get("app.kubernetes.io/instance") != self.config["application_release"]:
            raise ValueError("Foreign upgrade workload")
        meta.setdefault("labels", {})["okf-run-id"] = self.config["run_id"]
        if verb == "create" and value["kind"] in {"ConfigMap", "Job"}:
            existing = self.kubectl("get", value["kind"], meta["name"], "--ignore-not-found", "-o", "json")
            if existing.strip():
                previous = json.loads(existing)
                if previous["metadata"].get("labels", {}).get("okf-run-id") != self.config["run_id"]:
                    raise ValueError("Existing upgrade resource is foreign")
                if value["kind"] == "ConfigMap" and previous.get("immutable") is True and previous.get("data") == value.get("data"):
                    return
                if value["kind"] == "Job" and previous.get("status", {}).get("succeeded") == 1:
                    old = previous["spec"]["template"]["spec"]["containers"][0]
                    new = value["spec"]["template"]["spec"]["containers"][0]
                    if (old["image"], old.get("command")) == (new["image"], new.get("command")):
                        return
                raise ValueError("Existing upgrade resource differs")
        command = ["kubectl", "--context", self.config["context"], "-n", self.config["namespace"], verb, "-f", "-"]
        result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o",
            f"UserKnownHostsFile={ROOT / 'tests/tmp/replica-lab-known_hosts'}", "ubuntu@10.10.1.52", shlex.join(command)],
            input=json.dumps(value).encode(), capture_output=True, timeout=75, check=False)
        if result.returncode:
            raise ValueError("Owned upgrade resource operation refused")

    def job_result(self, name, prefix):
        deadline = time.monotonic() + 150
        while True:
            value = json.loads(self.kubectl("get", "job", name, "-o", "json"))
            if any(c["type"] == "Failed" and c["status"] == "True" for c in value.get("status", {}).get("conditions", [])):
                raise ValueError("Offline upgrade Job failed; maintenance stays held")
            if value.get("status", {}).get("succeeded") == 1:
                break
            if time.monotonic() > deadline:
                raise ValueError("Offline upgrade Job observation timed out; inspect the same Job")
            time.sleep(1)
        logs = self.kubectl("logs", "job/" + name).decode()
        lines = [json.loads(line[len(prefix):]) for line in logs.splitlines() if line.startswith(prefix)]
        if len(lines) != 1:
            raise ValueError("Offline upgrade public receipt missing")
        return lines[0]


def operator_job(api, *, name, image, runtime, inputs, command, new_identity=None):
    template = copy.deepcopy(api["spec"]["template"])
    template["metadata"].pop("annotations", None)
    template["metadata"]["labels"]["okf-role"] = "bootstrap"
    pod = template["spec"]
    pod["restartPolicy"] = "Never"
    container = pod["containers"][0]
    container["image"], container["command"] = image, command
    for key in ("args", "startupProbe", "livenessProbe", "readinessProbe", "ports"):
        container.pop(key, None)
    for ref in container.get("envFrom", []):
        if "configMapRef" in ref:
            ref["configMapRef"]["name"] = runtime
    pod["volumes"] = [v for v in pod["volumes"] if v["name"] != "runtime-identity"]
    container["volumeMounts"] = [v for v in container["volumeMounts"] if v["name"] != "runtime-identity"]
    container["env"] = [e for e in container["env"] if e["name"] != "RUNTIME_IDENTITY_FILE"]
    output = "/run/new-identity/identity.json" if new_identity else "/scratch/identity.json"
    container["env"].append({"name": "RUNTIME_IDENTITY_FILE", "value": output})
    pod["volumes"].append({"name": "upgrade-inputs", "configMap": {"name": inputs}})
    container["volumeMounts"].append({"name": "upgrade-inputs", "mountPath": "/run/upgrade", "readOnly": True})
    if new_identity:
        pod["volumes"].append({"name": "new-identity", "configMap": {"name": new_identity}})
        container["volumeMounts"].append({"name": "new-identity", "mountPath": "/run/new-identity", "readOnly": True})
    return {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name},
        "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 120, "template": template}}


def release_source():
    return ("import json,sys\nsys.path.insert(0,'/app')\nCORPUS_TABLES=" + repr(CORPUS_TABLES) + "\n"
        + inspect.getsource(corpus) + '''
from pathlib import Path
from app.deployment.bootstrap import OperatorSettings
from app.deployment.runtime_identity import validate_identity,enforce_session_identity
from app.db.session import configure_database,get_engine
from app.services.work.maintenance_control import MaintenanceOperator,leave_maintenance,REQUIRED_WRITERS
try:
 s=OperatorSettings(_env_file=None);s._runtime_identity=validate_identity(s)
 configure_database(s.db_url)
 expected=json.loads(Path('/run/upgrade/corpus.json').read_bytes())
 with MaintenanceOperator(get_engine(),operation_id=sys.argv[1],owner_id=sys.argv[2],settings=s) as op:
  with op.transaction() as session:
   enforce_session_identity(session,s,require_installed=True)
   if corpus(session.connection())!=expected:raise ValueError('Held corpus changed')
   epoch=leave_maintenance(session,sys.argv[1],writer_receipts={k:'stopped' for k in REQUIRED_WRITERS})
 print('REPLICA_UPGRADE_RELEASE:'+json.dumps({'epoch':epoch,'corpus_unchanged':True,'released':True}),flush=True)
except Exception as exc:
 print(json.dumps({'release_refused':type(exc).__name__}),file=sys.stderr)
 raise SystemExit(2)
''')


def require_legacy_operator_principal(secret, operator_user):
    from sqlalchemy.engine import make_url
    url = make_url(base64.b64decode(secret["data"]["DATABASE_URL"], validate=True).decode())
    if url.username != operator_user:
        raise ValueError("The legacy upgrade recipe cannot use scoped credentials; use replica_image_upgrade.py")


def run_upgrade(config, *, namespace_uid, image, report, resume=None):
    from app.services.work.maintenance_control import (
        MaintenanceOperator,
        enter_maintenance,
    )
    from sqlalchemy import text
    sys.path.insert(0, str(ROOT / "backend"))
    from test_scripts.replica_runtime_acceptance import (
        Kubernetes,
    )
    from test_scripts.replica_runtime_faults import FaultProbe, require_quiescent
    if not re.fullmatch(r"okf-backend:replicas-[0-9a-f]{7}-[0-9a-f]{12}", image):
        raise ValueError("Explicit selected lab backend image required")
    lab = UpgradeLab(config, namespace_uid)
    lab.assert_namespace()
    if resume is not None:
        return resume_upgrade(lab, config, image=image, report=report, journal_path=resume)
    value = {**config, "namespace_uid": namespace_uid, "frontend_url": f"http://127.0.0.1:{config['ports']['frontend']}",
        "simulation_username": "replica.admin"}
    kube = Kubernetes(value, ssh_host="ubuntu@10.10.1.52", known_hosts=ROOT / "tests/tmp/replica-lab-known_hosts")
    probe = FaultProbe(kube, value)
    require_quiescent(probe)
    objects = lab.inventory()
    deployments = {o["metadata"]["name"]: o for o in objects if o["kind"] == "Deployment"}
    if set(deployments) != {"api", "worker", "frontend", "model-stub"}:
        raise ValueError("Unknown deployment writer")
    old_api = deployments["api"]
    old_identity_name = next(v["configMap"]["name"] for v in old_api["spec"]["template"]["spec"]["volumes"] if v["name"] == "runtime-identity")
    old_runtime_name = next(e["configMapRef"]["name"] for e in old_api["spec"]["template"]["spec"]["containers"][0]["envFrom"] if "configMapRef" in e)
    for role in ("api", "worker", "frontend"):
        preview = copy.deepcopy(deployments[role])
        preview["spec"]["replicas"] = 0
        stage_role(preview, old_runtime=old_runtime_name, new_runtime="planned-runtime", old_identity=old_identity_name,
            new_identity="planned-identity", image=image)
    old_identity = json.loads(json.loads(lab.kubectl("get", "cm", old_identity_name, "-o", "json"))["data"]["identity.json"])
    runtime = json.loads(lab.kubectl("get", "cm", old_runtime_name, "-o", "json"))["data"]
    runtime_secret = next(e["secretRef"]["name"] for e in old_api["spec"]["template"]["spec"]["containers"][0]["envFrom"] if "secretRef" in e)
    require_legacy_operator_principal(json.loads(lab.kubectl("get", "secret", runtime_secret, "-o", "json")), "replica")
    operation, owner = uuid4().hex, "owned-lab-upgrade"
    stem = "replica-upgrade-" + operation[:12]
    next_runtime, next_identity = stem + "-runtime", stem + "-identity"
    private = Path(config["ca_file"]).parent
    journal = {"operation_id": operation, "owner_id": owner, "namespace_uid": namespace_uid, "image": image,
        "old_identity": old_identity, "old_deployments": deployments, "next_runtime": next_runtime, "next_identity": next_identity}
    journal_path = private / (operation + "-upgrade-journal.json")
    with journal_path.open("x") as output:
        json.dump(journal, output)
    engine, s3, q = connections(config)
    settings = SimpleNamespace(okf_deployment_schema="replicas-v1", app_role="api",
        audit_db_enabled=runtime.get("AUDIT_DB_ENABLED", "true").lower() == "true", audit_stdout_mode=runtime.get("AUDIT_STDOUT_MODE", "disabled"))
    with MaintenanceOperator(engine, operation_id=operation, owner_id=owner, settings=settings) as op:
        with op.transaction() as session:
            identity, _control = sql_source(session.connection(), config)
            if identity["fingerprint"] != old_identity["runtime_fingerprint"]:
                raise ValueError("Expected installed source identity differs")
            epoch = enter_maintenance(session, operation, kind="upgrade", intended_counts={"frontend": 2, "api": 2, "worker": 2})
        print(json.dumps({"phase": "upgrade-fenced", "epoch": epoch, "operation_id": operation}), flush=True)
        for role in ("frontend", "api", "worker", "model-stub"):
            lab.scale(role, 1 if role == "model-stub" else 2, 0)
        deadline = time.monotonic() + 180
        while True:
            try:
                lab.drained()
                break
            except ValueError:
                if time.monotonic() > deadline:
                    raise ValueError("Upgrade writer stop unverified") from None
                time.sleep(1)
        with op.transaction() as session:
            if session.scalar(text("SELECT count(*) FROM replica_capacity_slots WHERE resource='llm' AND owner_id IS NOT NULL")):
                raise ValueError("Provider requests prevent upgrade")
            snapshot = corpus(session.connection())
    q.close(); s3.close(); engine.dispose()
    return finish_upgrade(lab, config, journal, snapshot, epoch, runtime, report)


def resume_upgrade(lab, config, *, image, report, journal_path):
    from app.deployment.runtime_upgrade import _recorded
    from app.services.work.maintenance_control import MaintenanceOperator
    from sqlalchemy import text
    journal = json.loads(Path(journal_path).read_bytes())
    if (journal["namespace_uid"] != lab.namespace_uid or journal["image"] != image
            or journal["old_identity"]["installation_id"] != config["installation_id"]):
        raise ValueError("Resume journal does not bind this source")
    operation, owner = journal["operation_id"], journal["owner_id"]
    stem = "replica-upgrade-" + operation[:12]
    for suffix in ("-compute", "-release"):
        raw = lab.kubectl("get", "job", stem + suffix, "--ignore-not-found", "-o", "json")
        if not raw.strip():
            continue
        job = json.loads(raw)
        if (job["metadata"].get("labels", {}).get("okf-run-id") != config["run_id"]
                or job["spec"]["template"]["spec"]["containers"][0]["image"] != image):
            raise ValueError("Existing offline Job is foreign")
        if any(c["type"] == "Failed" and c["status"] == "True" for c in job.get("status", {}).get("conditions", [])):
            pods = json.loads(lab.kubectl("get", "pods", "-l", "job-name=" + stem + suffix, "-o", "json"))["items"]
            if any(p.get("status", {}).get("phase") not in {"Succeeded", "Failed"} for p in pods):
                raise ValueError("Failed Job process is not terminated")
            lab.kubectl("delete", "job", stem + suffix, "--wait=true", "--timeout=30s")
    lab.drained()
    inputs = json.loads(lab.kubectl("get", "cm", stem + "-inputs", "-o", "json"))["data"]
    snapshot = json.loads(inputs["corpus.json"])
    old_api = journal["old_deployments"]["api"]
    old_runtime = next(e["configMapRef"]["name"] for e in old_api["spec"]["template"]["spec"]["containers"][0]["envFrom"] if "configMapRef" in e)
    runtime = json.loads(lab.kubectl("get", "cm", old_runtime, "-o", "json"))["data"]
    settings = SimpleNamespace(okf_deployment_schema="replicas-v1", app_role="api",
        audit_db_enabled=runtime.get("AUDIT_DB_ENABLED", "true").lower() == "true", audit_stdout_mode=runtime.get("AUDIT_STDOUT_MODE", "disabled"))
    engine, s3, q = connections(config)
    with MaintenanceOperator(engine, operation_id=operation, owner_id=owner, settings=settings) as op, op.transaction() as session:
        identity, _control = sql_source(session.connection(), config)
        state = session.execute(text("SELECT * FROM replica_maintenance_state WHERE id=1")).mappings().one()
        if (not state["enabled"] or state["operation_id"] != operation or state["owner_id"] != owner or state["operation_kind"] != "upgrade"
                or corpus(session.connection()) != snapshot
                or session.scalar(text("SELECT count(*) FROM replica_capacity_slots WHERE resource='llm' AND owner_id IS NOT NULL"))):
            raise ValueError("Resume source is not the same held operation")
        if identity["fingerprint"] != journal["old_identity"]["runtime_fingerprint"] and not _recorded(session, identity["fingerprint"], state["epoch"]):
            raise ValueError("Resume installed identity is unverified")
        epoch = state["epoch"]
    q.close(); s3.close(); engine.dispose()
    print(json.dumps({"phase": "held-upgrade-resumed", "epoch": epoch, "operation_id": operation}), flush=True)
    return finish_upgrade(lab, config, journal, snapshot, epoch, runtime, report)


def finish_upgrade(lab, config, journal, snapshot, epoch, runtime, report):
    from app.deployment.runtime_identity import IDENTITY_KEYS
    from test_scripts.replica_runtime_acceptance import (
        Kubernetes,
        validate_inventory,
        verify_runtime,
    )
    operation, owner, image = journal["operation_id"], journal["owner_id"], journal["image"]
    stem = "replica-upgrade-" + operation[:12]
    next_runtime, next_identity, inputs = journal["next_runtime"], journal["next_identity"], stem + "-inputs"
    deployments, old_identity = journal["old_deployments"], journal["old_identity"]
    old_api = deployments["api"]
    namespace_uid = lab.namespace_uid
    old_identity_name = next(v["configMap"]["name"] for v in old_api["spec"]["template"]["spec"]["volumes"] if v["name"] == "runtime-identity")
    old_runtime_name = next(e["configMapRef"]["name"] for e in old_api["spec"]["template"]["spec"]["containers"][0]["envFrom"] if "configMapRef" in e)
    value = {**config, "namespace_uid": namespace_uid, "frontend_url": f"http://127.0.0.1:{config['ports']['frontend']}", "simulation_username": "replica.admin"}
    kube = Kubernetes(value, ssh_host="ubuntu@10.10.1.52", known_hosts=ROOT / "tests/tmp/replica-lab-known_hosts")
    data = {**runtime, "RUNTIME_IMAGE_ID": image}
    lab.write({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": next_runtime}, "immutable": True, "data": data})
    lab.write({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": inputs}, "immutable": True,
        "data": {"old-identity.json": json.dumps(old_identity), "corpus.json": json.dumps(snapshot), "release.py": release_source()}})
    command = ["python", "-u", "-c", "from pathlib import Path;import runpy,sys;Path('/scratch/runtime.env').write_text('# Explicit envFrom inputs\\n');sys.argv="
        + repr(["scripts/upgrade_replica_runtime.py", "--runtime-env-file", "/scratch/runtime.env", "--expected-identity", "/run/upgrade/old-identity.json",
            "--operation-id", operation, "--owner-id", owner, "--apply"]) + ";runpy.run_path('scripts/upgrade_replica_runtime.py',run_name='__main__')"]
    job_name = stem + "-compute"
    lab.write(operator_job(old_api, name=job_name, image=image, runtime=next_runtime, inputs=inputs, command=command))
    result = lab.job_result(job_name, "REPLICA_UPGRADE_IDENTITY:")
    document = result["identity"]
    if (set(document) != IDENTITY_KEYS or result["operation_id"] != operation or result["jobs_held"] is not True
            or document["installation_id"] != config["installation_id"] or document["runtime_fingerprint"] == old_identity["runtime_fingerprint"]):
        raise ValueError("Upgraded public identity differs")
    lab.write({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": next_identity}, "immutable": True,
        "data": {"identity.json": json.dumps(document, sort_keys=True)}})
    for role in ("api", "worker", "frontend"):
        current = json.loads(lab.kubectl("get", "deploy", role, "-o", "json"))
        if current["metadata"]["uid"] != deployments[role]["metadata"]["uid"]:
            raise ValueError("Upgrade deployment UID changed")
        lab.write(stage_role(current, old_runtime=old_runtime_name, new_runtime=next_runtime, old_identity=old_identity_name,
            new_identity=next_identity, image=image), verb="replace")
    lab.drained()
    release_name = stem + "-release"
    lab.write(operator_job(old_api, name=release_name, image=image, runtime=next_runtime, inputs=inputs,
        new_identity=next_identity, command=["python", "-u", "/run/upgrade/release.py", operation, owner]))
    released = lab.job_result(release_name, "REPLICA_UPGRADE_RELEASE:")
    if released != {"epoch": epoch, "corpus_unchanged": True, "released": True}:
        raise ValueError("Upgrade release receipt differs")
    for role in ("model-stub", "api", "worker", "frontend"):
        lab.scale(role, 0, 1 if role == "model-stub" else 2)
    for role in ("model-stub", "api", "worker", "frontend"):
        lab.kubectl("rollout", "status", "deploy/" + role, "--timeout=45s")
    ready = validate_inventory(kube.inventory(), value)
    verified = verify_runtime(kube, kube.inventory(), value)
    if verified["fingerprint"] != document["runtime_fingerprint"]:
        raise ValueError("Restarted runtime identity differs")
    public = {"status": "owned-same-schema-upgrade-passed", "namespace": config["namespace"], "namespace_uid": namespace_uid,
        "operation_id": operation, "epoch": epoch, "installation_id": config["installation_id"], "image": image,
        "old_fingerprint": old_identity["runtime_fingerprint"], "new_fingerprint": document["runtime_fingerprint"],
        "identity_configmap": next_identity, "runtime_configmap": next_runtime, "held_corpus_unchanged": True,
        "schema_revision": verified["schema_revision"], "runtime": ready, "observed_at": datetime.now(timezone.utc).isoformat(),
        "production_schedule": "disabled", "functional_check": "pending"}
    Path(report).write_text(json.dumps(public, indent=2) + "\n")
    print(json.dumps(public), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--namespace-uid", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--resume", type=Path, help="Resume the same protected held operation journal")
    args = parser.parse_args()
    try:
        run_upgrade(json.loads(args.config.read_bytes()), namespace_uid=args.namespace_uid, image=args.image, report=args.report, resume=args.resume)
        return 0
    except Exception as exc:  # noqa: BLE001 -- private exceptions must never become public report fields.
        import traceback
        with (args.config.parent / "upgrade-error.log").open("w", encoding="utf-8") as errors:
            traceback.print_exc(file=errors)
        frames = traceback.extract_tb(exc.__traceback__)
        print(json.dumps({"status": "upgrade-refused", "type": type(exc).__name__, "maintenance": "inspect-existing-operation",
            "locations": [{"function": f.name, "line": f.lineno} for f in frames if Path(f.filename).resolve() == Path(__file__).resolve()]}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
