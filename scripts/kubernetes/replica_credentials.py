"""Explicit scoped credentials for the selected owned PG17/RustFS1.0.4 lab.

Runtime and operator principals remain separate. Provisioning/testing precede
explicit held activation; each phase retains the same protected operation journal.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import re
import secrets
import ssl
import sys
import textwrap
import time
import traceback
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replica_backup import ROOT, connections, sql_source, validate_drained
from replica_upgrade import corpus, operator_job, release_source, stage_role
from replica_worker_loss import BASELINE_SOURCE, WorkerLoss, emit
from test_scripts.replica_runtime_acceptance import role_pods
from test_scripts.replica_runtime_faults import FaultProbe, require_quiescent

LOCK_TABLES = {"artifact_storage_control", "replica_runtime_identity", "replica_maintenance_state"}
READ_TABLES = {"alembic_version", "artifact_storage_migrations"}
CREDENTIAL_KEYS = {"DATABASE_URL", "ARTIFACT_S3_ACCESS_KEY", "ARTIFACT_S3_SECRET_KEY"}


def s3_policy(bucket, prefix):
    match = re.fullmatch(r"replica-test-([a-z0-9]{8,32})", bucket)
    if match is None or prefix != "replica_test/" + match[1] + "/application/":
        raise ValueError("Exact owned bucket and application object prefix required")
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "DedicatedBucketMetadata", "Effect": "Allow", "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
            "Resource": ["arn:aws:s3:::" + bucket]},
        {"Sid": "ApplicationObjects", "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"],
            "Resource": ["arn:aws:s3:::" + bucket + "/" + prefix + "*"]}]}


def grant_plan(tables):
    if not tables or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table) for table in tables):
        raise ValueError("Known SQL table identifiers required")
    return {table: {"privileges": ["SELECT"] if table in LOCK_TABLES | READ_TABLES else ["SELECT", "INSERT"]
        if table == "audit_log" else ["SELECT", "INSERT", "UPDATE", "DELETE"],
        "update_columns": ["id"] if table in LOCK_TABLES else []} for table in sorted(tables)}


def policy_digest(value):
    def normalized(item):
        if isinstance(item, dict):
            return {k: normalized(v) for k, v in item.items()}
        if isinstance(item, list):
            return sorted((normalized(v) for v in item), key=lambda v: json.dumps(v, sort_keys=True))
        return item
    return sha256(json.dumps(normalized(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def bucket_discovery(names, expected):
    if set(names) - {expected}:
        raise ValueError("Runtime bucket discovery exposed another scope")
    return {"mode": "filtered", "visible_bucket_count": len(names)}


def bind_runtime_secret(deployment, previous, selected):
    result = copy.deepcopy(deployment)
    if result["spec"].get("replicas") != 0:
        raise ValueError("Runtime secret binding changes require stopped roles")
    containers = result["spec"]["template"]["spec"]["containers"]
    if len(containers) != 1:
        raise ValueError("One explicit application container required")
    refs = [r["secretRef"] for r in containers[0].get("envFrom", []) if "secretRef" in r]
    if len(refs) != 1 or refs[0]["name"] not in {previous, selected}:
        raise ValueError("Expected runtime Secret differs")
    if any(e["name"].upper() in CREDENTIAL_KEYS for e in containers[0].get("env", [])):
        raise ValueError("Literal or indirect credential override refused")
    refs[0]["name"] = selected
    result.pop("status", None)
    result.get("metadata", {}).pop("managedFields", None)
    return result


def credential_job(api, *, name, runtime, inputs, runtime_secret, operator_secret, command, new_identity=None):
    if runtime_secret == operator_secret:
        raise ValueError("Separate runtime and operator Secrets required")
    prepared = copy.deepcopy(api)
    prepared["spec"]["replicas"] = 0
    refs = [r["secretRef"]["name"] for r in prepared["spec"]["template"]["spec"]["containers"][0].get("envFrom", []) if "secretRef" in r]
    if len(refs) != 1:
        raise ValueError("One runtime credential reference required")
    prepared = bind_runtime_secret(prepared, refs[0], runtime_secret)
    image = prepared["spec"]["template"]["spec"]["containers"][0]["image"]
    result = operator_job(prepared, name=name, image=image, runtime=runtime, inputs=inputs, command=command, new_identity=new_identity)
    pod = result["spec"]["template"]["spec"]
    if any(v["name"] == "operator-database" for v in pod["volumes"]):
        raise ValueError("Existing operator mount refused")
    pod["volumes"].append({"name": "operator-database", "secret": {"secretName": operator_secret,
        "defaultMode": 0o440, "items": [{"key": "url", "path": "url"}]}})
    pod["containers"][0]["volumeMounts"].append({"name": "operator-database", "mountPath": "/run/operator-db", "readOnly": True})
    return result


def credential_sources():
    compute = '''import json,sys
from pathlib import Path
sys.path.insert(0,'/app')
from sqlalchemy import create_engine
from app.deployment.bootstrap import OperatorSettings,write_identity
from app.deployment.runtime_upgrade import rebind_runtime
from app.services.work.maintenance_control import MaintenanceOperator
try:
 s=OperatorSettings(_env_file=None)
 engine=create_engine(Path('/run/operator-db/url').read_text().strip())
 old=json.loads(Path('/run/upgrade/old-identity.json').read_bytes())
 with MaintenanceOperator(engine,operation_id=sys.argv[1],owner_id=sys.argv[2],settings=s) as op:
  with op.transaction() as session:
   document=rebind_runtime(session,s,old,operation_id=sys.argv[1])
 write_identity(s.runtime_identity_file,document)
 print('REPLICA_CREDENTIAL_IDENTITY:'+json.dumps({'identity':document,'operation_id':sys.argv[1],'jobs_held':True}),flush=True)
except Exception as exc:
 print(json.dumps({'compute_refused':type(exc).__name__}),file=sys.stderr)
 raise SystemExit(2)
'''
    release = release_source()
    if release.count("configure_database(s.db_url)") != 1:
        raise ValueError("Trusted release source changed")
    release = release.replace("configure_database(s.db_url)", "configure_database(Path('/run/operator-db/url').read_text().strip())")
    return compute, release


def credential_secret_data(previous, database_url, access, secret):
    expected = CREDENTIAL_KEYS | {"APP_SECRET_KEY", "DIAGNOSTICS_INGEST_TOKEN", "DIAG_TOKEN", "LLM_API_KEY", "QDRANT_API_KEY"}
    if set(previous) != expected:
        raise ValueError("Unexpected source credential inventory")
    for value in previous.values():
        if not base64.b64decode(value, validate=True):
            raise ValueError("Empty source credential")
    selected = dict(previous)
    selected.update({key: base64.b64encode(value.encode()).decode() for key, value in {
        "DATABASE_URL": database_url, "ARTIFACT_S3_ACCESS_KEY": access, "ARTIFACT_S3_SECRET_KEY": secret}.items()})
    return selected, {"url": previous["DATABASE_URL"]}


def runtime_proof_source(expected):
    return '''import hashlib,json,tempfile
from pathlib import Path
from sqlalchemy import text
from app.config import get_settings
from app.db.session import session_scope
from app.deployment.runtime_identity import enforce_runtime_identity,validate_identity
p=json.loads(PRIVATE_INPUT)
s=get_settings();identity=enforce_runtime_identity(s)
assert identity.fingerprint==p['fingerprint']
assert hashlib.sha256(s.artifact_s3_access_key.encode()).hexdigest()==p['access_sha256']
assert hashlib.sha256(s.artifact_s3_secret_key.encode()).hexdigest()==p['secret_sha256']
with session_scope() as session:
 assert session.scalar(text('SELECT current_user'))==p['role']
 flags=session.execute(text('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit,rolconnlimit FROM pg_roles WHERE rolname=current_user')).one()
 assert tuple(flags)==(False,False,False,False,False,False,64)
 assert session.scalar(text('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()')) is True
 state=session.execute(text('SELECT enabled,epoch,operation_id FROM replica_maintenance_state WHERE id=1')).one()
 assert tuple(state)==(False,p['epoch'],p['operation'])
 for action,target in [('replica_runtime_upgrade',p['fingerprint']),('replica_maintenance_enter',p['operation']),('replica_maintenance_leave',p['operation'])]:
  assert session.scalar(text('SELECT count(*) FROM audit_log WHERE action_type=:action AND target_id=:target'),{'action':action,'target':target})==1
 assert not session.scalar(text("SELECT has_column_privilege(current_user,'replica_runtime_identity','fingerprint','UPDATE')"))
 assert not session.scalar(text("SELECT has_column_privilege(current_user,'replica_maintenance_state','epoch','UPDATE')"))
old=json.loads(Path(s.runtime_identity_file).read_bytes());old['runtime_fingerprint']=p['old_fingerprint']
with tempfile.TemporaryDirectory(dir='/scratch',prefix='credential-proof-') as directory:
 path=Path(directory)/'old-identity.json';path.write_text(json.dumps(old))
 try:validate_identity(s.model_copy(update={'runtime_identity_file':path}))
 except ValueError:pass
 else:raise AssertionError('Retired identity accepted')
print(json.dumps({'ok':True,'actual_scoped_sql_user':True,'actual_scoped_s3_keys':True,'tls_verified':True,'audits_verified':True,'old_identity_denied':True}))
'''.replace("PRIVATE_INPUT", repr(json.dumps(expected)), 1)


def credential_wait_inventory(items, job):
    """An observation retry may wait for its existing Job, never create another."""
    uid = job["metadata"]["uid"]
    return [item for item in items if not (
        item["kind"] == "Job" and item["metadata"]["uid"] == uid
        or item["kind"] == "Pod" and any(ref.get("kind") == "Job" and ref.get("uid") == uid and ref.get("controller") is True
            for ref in item["metadata"].get("ownerReferences", [])))]


def assert_credential_stopped(items, deployments):
    observed = {item["metadata"]["name"]: item for item in items if item["kind"] == "Deployment"}
    if set(observed) != set(deployments) or any(observed[name]["metadata"]["uid"] != original["metadata"]["uid"]
            or observed[name]["spec"].get("replicas") != 0 for name, original in deployments.items()):
        raise ValueError("All original application/provider controllers must stay stopped")


class RustAdmin:
    """SigV4 over verified TLS; native RustFS JSON, not MinIO encrypted payloads."""
    def __init__(self, config, access=None, secret=None):
        import httpx
        from botocore.credentials import Credentials
        self.credentials = Credentials(access or config["credentials"]["S3_ROOT_ACCESS_KEY"], secret or config["credentials"]["S3_ROOT_SECRET_KEY"])
        self.url = f"https://127.0.0.1:{config['ports']['artifacts']}/rustfs/admin/v3/"
        self.client = httpx.Client(verify=ssl.create_default_context(cafile=config["ca_file"]), trust_env=False, timeout=15)

    def call(self, method, operation, *, query=None, body=None):
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        allowed = {("GET", "info-canned-policy"), ("GET", "list-canned-policies"), ("GET", "user-info"), ("GET", "list-users"),
            ("PUT", "add-canned-policy"), ("PUT", "add-user"), ("PUT", "set-user-or-group-policy")}
        if (method, operation) not in allowed:
            raise ValueError("Unsupported native admin operation")
        data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode() if body is not None else b""
        url = self.url + operation + ("?" + urlencode(query) if query else "")
        request = AWSRequest(method=method, url=url, data=data,
            headers={"Content-Type": "application/json", "X-Amz-Content-Sha256": sha256(data).hexdigest()})
        SigV4Auth(self.credentials, "s3", "us-east-1").add_auth(request)
        return self.client.request(method, url, headers=dict(request.headers.items()), content=data)

    def close(self):
        self.client.close()


def provision_pg(connection, config, record):
    """Only a dedicated selected PG17 database; no foreign role adoption."""
    from app.db import (
        models as _models,  # noqa: F401 -- register the complete allowlist.
    )
    from app.db.base import Base
    from psycopg import sql
    from sqlalchemy import inspect, text
    identity, _ = sql_source(connection, config)
    if identity["fingerprint"] != record["old_identity"]["runtime_fingerprint"]:
        raise ValueError("Installed source changed before credential provisioning")
    raw = connection.connection.driver_connection
    version = int(raw.execute("SHOW server_version_num").fetchone()[0])
    if not 170000 <= version < 180000:
        raise ValueError("Selected PostgreSQL17 required")
    other = raw.execute("SELECT datname FROM pg_database WHERE NOT datistemplate AND datname NOT IN ('postgres',%s)", (config["database"],)).fetchall()
    if other:
        raise ValueError("This role recipe requires the dedicated selected PostgreSQL instance")
    tables = set(inspect(connection).get_table_names(schema="public"))
    if tables - set(Base.metadata.tables) - {"alembic_version"} or not LOCK_TABLES <= tables:
        raise ValueError("Unknown or incomplete SQL schema")
    role, password, marker = record["pg_role"], record["pg_password"], record["marker"]
    existing = raw.execute("SELECT shobj_description(oid,'pg_authid') FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
    if existing and existing[0] != marker:
        raise ValueError("Existing SQL role is not owned by this credential operation")
    ident = sql.Identifier(role)
    if not existing:
        raw.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT 64").format(ident, sql.Literal(password)))
        raw.execute(sql.SQL("COMMENT ON ROLE {} IS {}").format(ident, sql.Literal(marker)))
    row = raw.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit,rolcanlogin,rolconnlimit FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
    memberships = raw.execute("SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=%s", (role,)).fetchone()[0]
    if tuple(row) != (False, False, False, False, False, False, True, 64) or memberships:
        raise ValueError("Unexpected role attributes or inherited membership")
    raw.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(config["database"])))
    raw.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(config["database"]), ident))
    raw.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    raw.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident))
    plan = grant_plan(tables)
    for table, grants in plan.items():
        target = sql.Identifier("public", table)
        if table in LOCK_TABLES:
            checks = inspect(connection).get_check_constraints(table, schema="public")
            if not any(c["sqltext"].replace(" ", "").strip("()") == "id=1" for c in checks):
                raise ValueError("Singleton id is not constrained immutable")
        raw.execute(sql.SQL("REVOKE ALL PRIVILEGES ON TABLE {} FROM {}").format(target, ident))
        columns = [c["name"] for c in inspect(connection).get_columns(table, schema="public")]
        raw.execute(sql.SQL("REVOKE ALL PRIVILEGES ({}) ON TABLE {} FROM {}").format(
            sql.SQL(",").join(sql.Identifier(c) for c in columns), target, ident))
        raw.execute(sql.SQL("GRANT {} ON TABLE {} TO {}").format(sql.SQL(",").join(sql.SQL(p) for p in grants["privileges"]), target, ident))
        if grants["update_columns"]:
            raw.execute(sql.SQL("GRANT UPDATE (id) ON TABLE {} TO {}").format(target, ident))
    raw.execute(sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {}").format(ident))
    raw.execute(sql.SQL("ALTER ROLE {} IN DATABASE {} SET search_path=public").format(ident, sql.Identifier(config["database"])))
    if connection.scalar(text("SELECT current_database()")) != config["database"]:
        raise ValueError("SQL scope changed")
    return {"tables": len(plan), "plan_sha256": sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest(),
        "superuser": False, "ddl": False, "protected_singletons": sorted(LOCK_TABLES), "audit_append_only": True}


class ReplicaCredentials(WorkerLoss):
    def sealed_secret(self, name, data):
        self.lab.assert_namespace()
        labels = {"okf-run-id": self.config["run_id"], "okf-operation-id": self.journal["operation_id"],
            "app.kubernetes.io/instance": self.config["application_release"]}
        resource = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": self.value["namespace"],
            "labels": labels, "annotations": {"okf-namespace-uid": self.value["namespace_uid"]}}, "immutable": True, "type": "Opaque", "data": data}
        previous = self.kube.run("get", "secret", name, "--ignore-not-found", "-o", "json")
        if previous.strip():
            previous = json.loads(previous)
            if (previous.get("immutable") is not True or previous.get("type") != "Opaque" or previous.get("data") != data
                    or any(previous["metadata"].get("labels", {}).get(k) != v for k, v in labels.items())
                    or previous["metadata"].get("annotations", {}).get("okf-namespace-uid") != self.value["namespace_uid"]):
                raise ValueError("Existing credential Secret is not this exact owned operation")
        else:
            self.kube.run("create", "-f", "-", source=json.dumps(resource))
            previous = json.loads(self.kube.run("get", "secret", name, "-o", "json"))
            if previous.get("data") != data or previous.get("immutable") is not True:
                raise ValueError("Created credential Secret differs")
        return previous["metadata"]["uid"]

    def secrets(self):
        if self.journal["phase"] not in {"rights-tested", "secrets-ready"}:
            raise ValueError("Native scoped-rights evidence required")
        if not self.journal.get("pg_rights", {}).get("ok") or len(self.journal.get("s3_rights", {}).get("denied", [])) != 9:
            raise ValueError("Incomplete scoped-rights evidence")
        old = self.journal["old_secret"]
        current = json.loads(self.kube.run("get", "secret", old["metadata"]["name"], "-o", "json"))
        if current["metadata"]["uid"] != old["metadata"]["uid"] or current.get("data") != old["data"]:
            raise ValueError("Original runtime Secret changed")
        runtime, operator = credential_secret_data(old["data"], self.journal["new_db_runtime_url"], self.journal["s3_access"], self.journal["s3_secret"])
        stem = "replica-credentials-" + self.journal["operation_id"][:12]
        bindings = {"runtime_secret": stem + "-runtime", "operator_secret": stem + "-operator", "next_identity": stem + "-identity", "inputs": stem + "-inputs"}
        runtime_uid = self.sealed_secret(bindings["runtime_secret"], runtime)
        operator_uid = self.sealed_secret(bindings["operator_secret"], operator)
        self.save(phase="secrets-ready", **bindings, runtime_secret_uid=runtime_uid, operator_secret_uid=operator_uid)
        emit("credential-secrets-ready", immutable=True, signing_key_preserved=True, runtime_switched=False)

    def fence(self):
        from app.services.work.maintenance_control import (
            MaintenanceOperator,
            enter_maintenance,
        )
        from sqlalchemy import text
        if self.journal["phase"] not in {"secrets-ready", "fence-requested", "fenced"}:
            raise ValueError("Prepared immutable Secrets required")
        if self.journal["phase"] == "secrets-ready":
            probe = FaultProbe(self.kube, self.value)
            require_quiescent(probe)
            if probe.identity["fingerprint"] != self.fingerprint:
                raise ValueError("Source runtime changed")
            if self.kube.python(probe.api_pods()[0]["metadata"]["name"], BASELINE_SOURCE) != self.journal["baseline_documents"]:
                raise ValueError("Source documents changed")
            for role, old in self.journal["old_deployments"].items():
                current = json.loads(self.kube.run("get", "deploy", role, "-o", "json"))
                if current["metadata"]["uid"] != old["metadata"]["uid"] or current["spec"] != old["spec"]:
                    raise ValueError("Source workload changed")
            api = self.journal["old_deployments"]["api"]
            container = api["spec"]["template"]["spec"]["containers"][0]
            refs = [r["configMapRef"]["name"] for r in container["envFrom"] if "configMapRef" in r]
            if len(refs) != 1:
                raise ValueError("One immutable runtime configuration required")
            runtime = json.loads(self.kube.run("get", "cm", refs[0], "-o", "json"))
            if runtime.get("immutable") is not True or CREDENTIAL_KEYS & {k.upper() for k in runtime["data"]}:
                raise ValueError("Runtime configuration overrides credentials or is mutable")
            # Validate both stopped templates before taking the durable fence.
            for role in ("api", "worker"):
                planned = copy.deepcopy(self.journal["old_deployments"][role]); planned["spec"]["replicas"] = 0
                bind_runtime_secret(planned, self.journal["old_secret"]["metadata"]["name"], self.journal["runtime_secret"])
            self.save(phase="fence-requested", runtime_name=refs[0])
        engine, s3, q = connections(self.config)
        settings = SimpleNamespace(okf_deployment_schema="replicas-v1", app_role="api", audit_db_enabled=True, audit_stdout_mode="disabled")
        try:
            with MaintenanceOperator(engine, operation_id=self.journal["operation_id"], owner_id=self.journal["owner_id"], settings=settings) as op:
                with op.transaction() as session:
                    identity, _ = sql_source(session.connection(), self.config)
                    if identity["fingerprint"] != self.fingerprint:
                        raise ValueError("Installed source identity changed")
                    epoch = enter_maintenance(session, self.journal["operation_id"], kind="upgrade", intended_counts={r: 2 for r in ("api", "worker", "frontend")})
                self.save(phase="fenced", epoch=epoch)
                for role, old in self.journal["old_deployments"].items():
                    current = json.loads(self.kube.run("get", "deploy", role, "-o", "json"))
                    count = current["spec"].get("replicas")
                    if current["metadata"]["uid"] != old["metadata"]["uid"] or count not in {0, 1 if role == "model-stub" else 2}:
                        raise ValueError("Workload identity or count changed")
                    if count:
                        self.lab.scale(role, count, 0)
                deadline = time.monotonic() + 150
                while True:
                    try:
                        self.lab.drained(); break
                    except ValueError:
                        if time.monotonic() > deadline:
                            raise ValueError("Writer stop unverified; maintenance remains held") from None
                        time.sleep(1)
                with op.transaction() as session:
                    if session.scalar(text("SELECT count(*) FROM replica_capacity_slots WHERE resource='llm' AND owner_id IS NOT NULL")):
                        raise ValueError("Uncertain model reservations prevent credential transition")
                    snapshot = corpus(session.connection())
                self.save(phase="held", corpus=snapshot)
                emit("credential-transition-held", epoch=epoch, writers_stopped=True, provider_owners=0)
        finally:
            engine.dispose(); s3.close(); q.close()

    def job(self, name, command, *, identity=None):
        assert_credential_stopped(self.lab.inventory(), self.journal["old_deployments"])
        desired = credential_job(self.journal["old_deployments"]["api"], name=name, runtime=self.journal["runtime_name"],
            inputs=self.journal["inputs"], runtime_secret=self.journal["runtime_secret"], operator_secret=self.journal["operator_secret"],
            command=command, new_identity=identity)
        existing = self.kube.run("get", "job", name, "--ignore-not-found", "-o", "json")
        if existing.strip():
            previous = json.loads(existing)
            old_pod, new_pod = previous["spec"]["template"]["spec"], desired["spec"]["template"]["spec"]
            old, new = old_pod["containers"][0], new_pod["containers"][0]
            if (previous["metadata"].get("labels", {}).get("okf-run-id") != self.config["run_id"]
                    or any(old.get(k) != new.get(k) for k in ("image", "command", "envFrom", "env", "volumeMounts"))):
                raise ValueError("Existing credential Job differs; do not restart it")
            for volume in new_pod["volumes"]:
                if volume["name"] not in {"operator-database", "upgrade-inputs", "new-identity"}:
                    continue
                matches = [v for v in old_pod["volumes"] if v["name"] == volume["name"]]
                if len(matches) != 1:
                    raise ValueError("Credential Job input volume changed")
                observed = copy.deepcopy(matches[0])
                if "configMap" in observed:
                    observed["configMap"].pop("defaultMode", None)
                if observed != volume:
                    raise ValueError("Credential Job Secret/configuration binding changed")
            items = credential_wait_inventory(self.lab.inventory(), previous)
            nodes = json.loads(self.lab.kubectl("get", "nodes", "-o", "json", namespaced=False))["items"]
            ready = {n["metadata"]["name"]: any(c["type"] == "Ready" and c["status"] == "True" for c in n.get("status", {}).get("conditions", [])) for n in nodes}
            validate_drained(items, self.config, ready)
        else:
            self.lab.drained()
            self.lab.write(desired)

    def compute(self):
        from app.deployment.runtime_identity import IDENTITY_KEYS
        if self.journal["phase"] not in {"held", "computed"}:
            raise ValueError("Held credential operation required")
        compute, release = credential_sources()
        self.lab.write({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": self.journal["inputs"]}, "immutable": True,
            "data": {"compute.py": compute, "release.py": release, "old-identity.json": json.dumps(self.journal["old_identity"]),
                "corpus.json": json.dumps(self.journal["corpus"])}})
        name = self.journal["inputs"].removesuffix("-inputs") + "-compute"
        self.job(name, ["python", "-u", "/run/upgrade/compute.py", self.journal["operation_id"], self.journal["owner_id"]])
        receipt = self.lab.job_result(name, "REPLICA_CREDENTIAL_IDENTITY:")
        document = receipt["identity"]
        old = self.journal["old_identity"]
        if (set(document) != IDENTITY_KEYS or receipt["operation_id"] != self.journal["operation_id"] or receipt["jobs_held"] is not True
                or {k: v for k, v in old.items() if k != "runtime_fingerprint"} != {k: v for k, v in document.items() if k != "runtime_fingerprint"}
                or document["runtime_fingerprint"] == self.fingerprint):
            raise ValueError("Credential identity receipt differs")
        self.lab.write({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": self.journal["next_identity"]}, "immutable": True,
            "data": {"identity.json": json.dumps(document, sort_keys=True)}})
        self.save(phase="computed", new_identity=document)
        emit("credential-identity-computed", fingerprint=document["runtime_fingerprint"], jobs_held=True)

    def stage(self):
        if self.journal["phase"] not in {"computed", "staged"}:
            raise ValueError("Computed credential identity required")
        self.lab.drained()
        for role in ("api", "worker"):
            current = json.loads(self.kube.run("get", "deploy", role, "-o", "json"))
            old = self.journal["old_deployments"][role]
            if current["metadata"]["uid"] != old["metadata"]["uid"]:
                raise ValueError("Credential workload UID changed")
            selected = stage_role(current, old_runtime=self.journal["runtime_name"], new_runtime=self.journal["runtime_name"],
                old_identity=self.journal["old_identity_name"], new_identity=self.journal["next_identity"],
                image=old["spec"]["template"]["spec"]["containers"][0]["image"])
            selected = bind_runtime_secret(selected, self.journal["old_secret"]["metadata"]["name"], self.journal["runtime_secret"])
            if self.journal["operator_secret"] in json.dumps(selected["spec"]["template"]):
                raise ValueError("Operator credential leaked to a normal workload")
            self.lab.write(selected, verb="replace")
        self.save(phase="staged")
        emit("credential-role-bindings-staged", replicas=0, operator_secret_in_application=False)

    def release(self):
        if self.journal["phase"] not in {"staged", "released"}:
            raise ValueError("Stopped staged credential operation required")
        name = self.journal["inputs"].removesuffix("-inputs") + "-release"
        self.job(name, ["python", "-u", "/run/upgrade/release.py", self.journal["operation_id"], self.journal["owner_id"]], identity=self.journal["next_identity"])
        receipt = self.lab.job_result(name, "REPLICA_UPGRADE_RELEASE:")
        if receipt != {"epoch": self.journal["epoch"], "corpus_unchanged": True, "released": True}:
            raise ValueError("Credential release receipt differs")
        self.save(phase="released", release_receipt=receipt)
        emit("credential-maintenance-released", epoch=receipt["epoch"], held_corpus_unchanged=True)

    def start(self):
        from test_scripts.replica_runtime_acceptance import (
            validate_inventory,
            verify_runtime,
        )
        if self.journal["phase"] not in {"released", "running"}:
            raise ValueError("Verified offline release required")
        for role in ("model-stub", "api", "worker", "frontend"):
            current = json.loads(self.kube.run("get", "deploy", role, "-o", "json"))
            desired = 1 if role == "model-stub" else 2
            if current["metadata"]["uid"] != self.journal["old_deployments"][role]["metadata"]["uid"] or current["spec"]["replicas"] not in {0, desired}:
                raise ValueError("Start workload identity or count differs")
            if current["spec"]["replicas"] == 0:
                self.lab.scale(role, 0, desired)
        for role in ("model-stub", "api", "worker", "frontend"):
            self.kube.run("rollout", "status", "deploy/" + role, "--timeout=45s")
        objects = self.kube.inventory()
        ready = validate_inventory(objects, self.value)
        if verify_runtime(self.kube, objects, self.value)["fingerprint"] != self.journal["new_identity"]["runtime_fingerprint"]:
            raise ValueError("Started runtime identity differs")
        self.save(phase="running", runtime=ready)
        emit("scoped-runtime-running", replicas=ready["replicas"], functional_check="pending")

    def verify(self, report):
        from io import BytesIO

        import httpx
        from docx import Document
        from test_scripts.replica_runtime_acceptance import (
            shared_session,
            validate_inventory,
        )
        from test_scripts.replica_runtime_faults import download_matches, search_ids
        if self.journal["phase"] not in {"running", "verified"} or Path(report).resolve().parent != ROOT / "docs/superpowers/reports":
            raise ValueError("Running credential operation and owned public report required")
        probe = FaultProbe(self.kube, self.value)
        expected_fp = self.journal["new_identity"]["runtime_fingerprint"]
        if probe.identity["fingerprint"] != expected_fp:
            raise ValueError("Scoped runtime fingerprint differs")
        require_quiescent(probe)
        objects = self.kube.inventory()
        proofs = []
        source = runtime_proof_source({"role": self.journal["pg_role"], "access_sha256": sha256(self.journal["s3_access"].encode()).hexdigest(),
            "secret_sha256": sha256(self.journal["s3_secret"].encode()).hexdigest(), "epoch": self.journal["epoch"],
            "operation": self.journal["operation_id"], "fingerprint": expected_fp, "old_fingerprint": self.fingerprint})
        for role in ("api", "worker"):
            current = next(o for o in objects if o["kind"] == "Deployment" and o["metadata"]["name"] == role)
            spec = current["spec"]["template"]["spec"]
            refs = [r["secretRef"]["name"] for r in spec["containers"][0]["envFrom"] if "secretRef" in r]
            if refs != [self.journal["runtime_secret"]] or self.journal["operator_secret"] in json.dumps(spec):
                raise ValueError("Normal runtime contains an unexpected credential reference")
            for pod in role_pods(objects, role):
                receipt = self.kube.python(pod["metadata"]["name"], source)
                if receipt.get("ok") is not True:
                    raise ValueError("Actual scoped runtime principal verification failed")
                proofs.append(receipt)
        shared = shared_session(self.kube, objects, self.value)
        with httpx.Client(base_url=self.value["frontend_url"], trust_env=False, timeout=25) as client:
            client.get("/").raise_for_status()
            client.post("/api/auth/simulate", json={"username": "replica.admin"}).raise_for_status()
            if "probe_body" not in self.journal:
                document = Document()
                document.add_heading("Scoped credential acceptance " + self.journal["operation_id"], 0)
                document.add_paragraph(("ТЕСТОВЫЙ концепт проверяет ограниченные права БД и объектного хранилища. " + self.journal["operation_id"] + " ") * 10)
                stream = BytesIO(); document.save(stream)
                self.save(probe_body=base64.b64encode(stream.getvalue()).decode())
            body = base64.b64decode(self.journal["probe_body"], validate=True)
            if "document" not in self.journal:
                response = client.post("/api/documents", headers={"Idempotency-Key": "credentials-" + self.journal["operation_id"]},
                    data={"allow_similar": "true"}, files={"file": ("credentials-" + self.journal["operation_id"] + ".docx", body,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
                response.raise_for_status()
                doc_id = response.json()["id"]
                if not re.fullmatch(r"[0-9a-f]{16}", doc_id):
                    raise ValueError("Invalid credential upload receipt")
                self.save(document={"id": doc_id, "sha256": sha256(body).hexdigest()}, upload_started_at=time.time())
            document = self.journal["document"]
            deadline = time.monotonic() + 240
            while True:
                response = client.get("/api/documents/" + document["id"]); response.raise_for_status()
                state = response.json()
                if state["status"] == "done" and state.get("problem") is None:
                    break
                if time.monotonic() > deadline or state["status"] in {"done", "paused", "error", "failed"}:
                    raise ValueError("Scoped credential document did not complete cleanly")
                time.sleep(2)
            observed = probe.state([document["id"]])
            if observed["documents"][document["id"]]["active_count"] != 1:
                raise ValueError("Expected one publication under scoped credentials")
            docs = self.kube.python(probe.api_pods()[0]["metadata"]["name"], BASELINE_SOURCE)
            by_id = {d["id"]: d for d in docs}
            if len(docs) != len(self.journal["baseline_documents"]) + 1 or any(by_id.get(d["id"]) != d for d in self.journal["baseline_documents"]):
                raise ValueError("Existing corpus changed during credential transition")
            hits = search_ids(client)
            for doc in self.journal["baseline_documents"] + [document]:
                if doc.get("deleted_at"):
                    if doc["id"] in hits or client.get(f"/api/documents/{doc['id']}/download").status_code != 404:
                        raise ValueError("Trashed document became visible")
                else:
                    download_matches(client, doc)
                    if doc["id"] not in hits:
                        raise ValueError("Document disappeared from search")
            require_quiescent(probe)
        ready = validate_inventory(self.kube.inventory(), self.value)
        if any(ready["restarts"].values()):
            raise ValueError("Unexpected runtime container restart")
        public = {"status": "owned-scoped-runtime-credentials-passed", "scope": "single-node-managed-s3-synthetic-stub",
            "namespace": self.value["namespace"], "namespace_uid": self.value["namespace_uid"], "installation_id": self.config["installation_id"],
            "operation_id": self.journal["operation_id"], "epoch": self.journal["epoch"], "old_fingerprint": self.fingerprint, "new_fingerprint": expected_fp,
            "runtime_secret": self.journal["runtime_secret"], "identity_configmap": self.journal["next_identity"],
            "actual_scoped_api_worker_processes": len(proofs), "operator_secret_in_application": False,
            "pg_rights": {k: self.journal["pg_rights"][k] for k in ("allowed", "denied", "tls_verify_full", "wrong_password_denied", "connection_limit")},
            "s3_rights": self.journal["s3_rights"], "held_corpus_unchanged": True, "audits_verified": True, "retired_identity_rejected": True,
            "signing_key_preserved": True, "shared_session": shared, "preserved_existing_documents": len(self.journal["baseline_documents"]),
            "new_document": {**document, "status": "done", "problem": None, "active_publications": 1, "search_download_sha256_verified": True},
            "runtime": ready, "production_schedule": "disabled", "verified_at": datetime.now(timezone.utc).isoformat(),
            "limitations": ["dedicated PG instance and dedicated S3 bucket only", "not customer/external/mixed/RWX certification", "not node or host failover"]}
        Path(report).write_text(json.dumps(public, indent=2) + "\n")
        self.save(phase="verified", report=str(Path(report).resolve()))
        emit("scoped-runtime-functional-pass", preserved_existing_documents=len(self.journal["baseline_documents"]), new_document=document["id"],
            actual_scoped_processes=len(proofs), pg_denied=len(self.journal["pg_rights"]["denied"]), s3_denied=len(self.journal["s3_rights"]["denied"]), replicas=ready["replicas"])

    def prepare(self):
        from sqlalchemy.engine import make_url
        if self.journal is not None:
            raise ValueError("Existing credential operation must be resumed, not recreated")
        probe = FaultProbe(self.kube, self.value)
        require_quiescent(probe)
        if probe.identity["fingerprint"] != self.fingerprint:
            raise ValueError("Installed runtime differs")
        objects = self.lab.inventory()
        deployments = {o["metadata"]["name"]: o for o in objects if o["kind"] == "Deployment"}
        api = deployments["api"]
        container = api["spec"]["template"]["spec"]["containers"][0]
        secret_name = next(x["secretRef"]["name"] for x in container["envFrom"] if "secretRef" in x)
        identity_name = next(v["configMap"]["name"] for v in api["spec"]["template"]["spec"]["volumes"] if v["name"] == "runtime-identity")
        old_identity = json.loads(json.loads(self.kube.run("get", "configmap", identity_name, "-o", "json"))["data"]["identity.json"])
        old_secret = json.loads(self.kube.run("get", "secret", secret_name, "-o", "json"))
        data = {k: base64.b64decode(v, validate=True).decode() for k, v in old_secret["data"].items()}
        old_url = make_url(data["DATABASE_URL"])
        if (old_identity["runtime_fingerprint"] != self.fingerprint or old_identity["installation_id"] != self.config["installation_id"]
                or old_url.host != f"{self.config['storage_release']}-postgres.{self.value['namespace']}.svc"
                or old_url.database != self.config["database"] or old_url.username != "replica"
                or old_url.password != self.config["credentials"]["POSTGRES_PASSWORD"]
                or data["ARTIFACT_S3_ACCESS_KEY"] != self.config["credentials"]["S3_ROOT_ACCESS_KEY"]
                or data["ARTIFACT_S3_SECRET_KEY"] != self.config["credentials"]["S3_ROOT_SECRET_KEY"]):
            raise ValueError("Expected initial operator-backed source does not match")
        operation = uuid4().hex
        self.journal = {"version": 1, "phase": "prepared", "namespace_uid": self.value["namespace_uid"], "fingerprint": self.fingerprint,
            "operation_id": operation, "owner_id": "owned-lab-credentials", "old_deployments": deployments,
            "old_identity": old_identity, "old_identity_name": identity_name, "old_secret": old_secret,
            "pg_role": "okf_app_" + operation[:16], "pg_password": secrets.token_urlsafe(36),
            "s3_access": "OKF" + uuid4().hex.upper(), "s3_secret": secrets.token_urlsafe(36), "s3_policy_name": "okf-runtime-" + operation[:16],
            "marker": "okf-runtime-v1:" + self.config["installation_id"] + ":" + operation + ":" + self.value["namespace_uid"],
            "baseline_documents": self.kube.python(role_pods(self.kube.inventory(), "api")[0]["metadata"]["name"], BASELINE_SOURCE)}
        self.journal["new_db_runtime_url"] = old_url.set(username=self.journal["pg_role"], password=self.journal["pg_password"]).render_as_string(hide_password=False)
        self.save()
        emit("credential-operation-prepared", operation_id=operation, installation_id=self.config["installation_id"], running_roles_changed=False)

    def provision(self):
        if self.journal["phase"] not in {"prepared", "provisioned"}:
            raise ValueError("Prepared credential operation required")
        self.lab.assert_namespace()
        engine, s3, q = connections(self.config)
        admin = RustAdmin(self.config)
        try:
            with engine.begin() as connection:
                pg = provision_pg(connection, self.config, self.journal)
            self.save(pg_provision=pg)
            policy = s3_policy(self.config["bucket"], self.config["prefix"])
            response = admin.call("GET", "list-canned-policies")
            if response.status_code != 200 or not isinstance(response.json(), dict):
                raise ValueError("Selected native IAM API is unavailable")
            previous = response.json().get(self.journal["s3_policy_name"])
            if previous is None:
                response = admin.call("PUT", "add-canned-policy", query={"name": self.journal["s3_policy_name"]}, body=policy)
                if response.status_code != 200:
                    raise ValueError("Native IAM policy creation refused")
            elif policy_digest(previous) != policy_digest(policy):
                raise ValueError("Existing IAM policy differs or native API is unavailable")
            response = admin.call("GET", "user-info", query={"accessKey": self.journal["s3_access"]})
            if response.status_code == 404:
                response = admin.call("PUT", "add-user", query={"accessKey": self.journal["s3_access"]},
                    body={"secretKey": self.journal["s3_secret"], "status": "enabled", "policy": self.journal["s3_policy_name"]})
                if response.status_code != 200:
                    raise ValueError("Native IAM user creation refused")
            elif (response.status_code != 200 or response.json().get("policyName", "") != self.journal["s3_policy_name"]
                    or response.json().get("status") != "enabled" or response.json().get("memberOf")):
                raise ValueError("Existing IAM principal is not the expected planned account")
            response = admin.call("PUT", "set-user-or-group-policy", query={"userOrGroup": self.journal["s3_access"],
                "policyName": self.journal["s3_policy_name"], "isGroup": "false"})
            if response.status_code != 200:
                raise ValueError("Native IAM binding refused")
            self.save(phase="provisioned", policy=policy)
            emit("scoped-principals-provisioned", pg_tables=pg["tables"], policy_sha256=sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest(),
                runtime_switched=False)
        finally:
            admin.close(); q.close(); s3.close(); engine.dispose()

    def rights(self):
        from sqlalchemy.engine import make_url
        if self.journal["phase"] not in {"provisioned", "rights-tested"}:
            raise ValueError("Provisioned scoped accounts required")
        probe = FaultProbe(self.kube, self.value)
        if probe.identity["fingerprint"] != self.fingerprint:
            raise ValueError("Source runtime changed before principal tests")
        pod = role_pods(self.kube.inventory(), "api")[0]["metadata"]["name"]
        url = make_url(self.journal["new_db_runtime_url"]).set(drivername="postgresql")
        wrong = url.set(password=secrets.token_urlsafe(24))
        source = '''import json,psycopg
private=json.loads(PRIVATE_INPUT)
url=private['url'];wrong=private['wrong'];expected=private['role'];domain=private['domain']
check='connect'
with psycopg.connect(url,autocommit=True) as conn:
 assert conn.execute('SELECT current_user').fetchone()[0]==expected
 assert conn.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()[0] is True
 attrs=conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit,rolconnlimit FROM pg_roles WHERE rolname=current_user').fetchone()
 assert tuple(attrs)==(False,False,False,False,False,False,64)
 allowed=[];denied=[]
 check='business_crud'
 with conn.transaction(force_rollback=True):
  assert conn.execute('SELECT count(*) FROM config_revisions WHERE domain=%s',(domain,)).fetchone()[0]==0
  conn.execute('INSERT INTO config_revisions(domain,revision) VALUES(%s,1)',(domain,))
  conn.execute('UPDATE config_revisions SET revision=2 WHERE domain=%s',(domain,))
  assert conn.execute('SELECT revision FROM config_revisions WHERE domain=%s',(domain,)).fetchone()[0]==2
  assert conn.execute('DELETE FROM config_revisions WHERE domain=%s',(domain,)).rowcount==1
 assert conn.execute('SELECT count(*) FROM config_revisions WHERE domain=%s',(domain,)).fetchone()[0]==0
 allowed.append(check)
 for table in ['replica_maintenance_state','replica_runtime_identity','artifact_storage_control']:
  check='row_lock_'+table
  with conn.transaction(force_rollback=True):
   assert conn.execute('SELECT id FROM '+table+' WHERE id=1 FOR UPDATE').fetchone()==(1,)
  allowed.append(check)
 checks={'maintenance_epoch':'UPDATE replica_maintenance_state SET epoch=epoch WHERE false',
 'runtime_fingerprint':'UPDATE replica_runtime_identity SET fingerprint=fingerprint WHERE false',
 'storage_control':'UPDATE artifact_storage_control SET active_store_id=active_store_id WHERE false',
 'audit_update':'UPDATE audit_log SET username=username WHERE false',
 'audit_delete':'DELETE FROM audit_log WHERE false',
 'migration_head':'UPDATE alembic_version SET version_num=version_num WHERE false',
 'operator_password_catalog':'SELECT rolpassword FROM pg_authid LIMIT 1',
 'public_ddl':'CREATE TABLE public.acl_probe_forbidden(id integer)',
 'temporary_ddl':'CREATE TEMP TABLE acl_probe_forbidden(id integer)'}
 for check,statement in checks.items():
  try:
   with conn.transaction(force_rollback=True):conn.execute(statement)
  except psycopg.errors.InsufficientPrivilege:denied.append(check)
  else:raise AssertionError('A forbidden SQL operation was accepted')
 check='truncate_privilege'
 assert conn.execute("SELECT has_table_privilege(current_user,'config_revisions','TRUNCATE')").fetchone()[0] is False
 denied.append(check)
check='wrong_password'
try:
 with psycopg.connect(wrong):pass
except psycopg.OperationalError as exc:
 assert getattr(exc,'sqlstate',None)=='28P01' or 'password authentication failed' in str(exc)
else:raise AssertionError('Wrong password accepted')
print(json.dumps({'ok':True,'allowed':allowed,'denied':denied,'tls_verify_full':True,'wrong_password_denied':True,'probe_data_rolled_back':True,'connection_limit':64}))
'''.replace("PRIVATE_INPUT", repr(json.dumps({"url": url.render_as_string(hide_password=False),
            "wrong": wrong.render_as_string(hide_password=False), "role": self.journal["pg_role"], "domain": "acl-probe-" + self.journal["operation_id"]})), 1)
        wrapped = "import json,traceback\ntry:\n" + textwrap.indent(source, " ") + '''
except Exception as exc:
 print(json.dumps({'ok':False,'error_type':type(exc).__name__,'sqlstate':getattr(exc,'sqlstate',None),
 'check':globals().get('check','initial'),'line':traceback.extract_tb(exc.__traceback__)[-1].lineno}))
'''
        pg = self.kube.python(pod, wrapped)
        if pg.get("ok") is not True:
            emit("native-pg-rights-refused", **{k: pg.get(k) for k in ("error_type", "sqlstate", "check", "line")})
            raise ValueError("Native PostgreSQL scoped-right checks failed")
        s3 = scoped_s3_probe(self.config, self.journal)
        self.save(phase="rights-tested", pg_rights=pg, s3_rights=s3)
        emit("scoped-rights-passed", pg_denied=len(pg["denied"]), s3_denied=len(s3["denied"]), runtime_switched=False)


def scoped_s3_probe(config, record):
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
    def client(access, secret):
        return boto3.client("s3", endpoint_url=f"https://127.0.0.1:{config['ports']['artifacts']}", region_name="us-east-1",
            aws_access_key_id=access, aws_secret_access_key=secret, verify=config["ca_file"],
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 0}, connect_timeout=8, read_timeout=30, proxies={}))
    app = client(record["s3_access"], record["s3_secret"])
    root = client(config["credentials"]["S3_ROOT_ACCESS_KEY"], config["credentials"]["S3_ROOT_SECRET_KEY"])
    bad = client(record["s3_access"], secrets.token_urlsafe(36))
    admin = RustAdmin(config, record["s3_access"], record["s3_secret"])
    bucket, operation = config["bucket"], record["operation_id"]
    key = config["prefix"] + "credential-probe/" + operation + "/blob"
    foreign = "replica_test/" + config["run_id"] + "/operator-probes/" + operation + "/blob"
    body, keys, uploads, denied = ("credential-proof-" + operation).encode(), [], [], []
    canary_bucket, canary_key = bucket + "-deny-" + operation[:8], "proof/" + operation
    canary_created = canary_written = False
    discovery = None
    try:
        app.head_bucket(Bucket=bucket)
        app.get_bucket_location(Bucket=bucket)
        app.put_object(Bucket=bucket, Key=key, Body=body, IfNoneMatch="*", Metadata={"okf-operation": operation})
        keys.append(key)
        assert app.get_object(Bucket=bucket, Key=key)["Body"].read() == body
        assert app.get_object(Bucket=bucket, Key=key, Range="bytes=3-10")["Body"].read() == body[3:11]
        assert app.head_object(Bucket=bucket, Key=key)["ContentLength"] == len(body)
        assert any(x["Key"] == key for x in app.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=2).get("Contents", []))
        multi = app.create_multipart_upload(Bucket=bucket, Key=key + "-multipart", Metadata={"okf-operation": operation})["UploadId"]
        uploads.append((key + "-multipart", multi))
        part = app.upload_part(Bucket=bucket, Key=key + "-multipart", UploadId=multi, PartNumber=1, Body=body)
        app.complete_multipart_upload(Bucket=bucket, Key=key + "-multipart", UploadId=multi,
            MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": part["ETag"]}]}, IfNoneMatch="*")
        uploads.remove((key + "-multipart", multi)); keys.append(key + "-multipart")
        assert app.get_object(Bucket=bucket, Key=key + "-multipart")["Body"].read() == body
        aborted = app.create_multipart_upload(Bucket=bucket, Key=key + "-abort")["UploadId"]
        uploads.append((key + "-abort", aborted))
        app.abort_multipart_upload(Bucket=bucket, Key=key + "-abort", UploadId=aborted)
        uploads.remove((key + "-abort", aborted))
        root.put_object(Bucket=bucket, Key=foreign, Body=body, IfNoneMatch="*", Metadata={"okf-operation": operation})
        keys.append(foreign)
        for check, kwargs, method in (("foreign_prefix_read", {"Bucket": bucket, "Key": foreign}, app.get_object),
                ("foreign_prefix_write", {"Bucket": bucket, "Key": foreign, "Body": body, "Metadata": {"okf-operation": operation}}, app.put_object),
                ("foreign_prefix_delete", {"Bucket": bucket, "Key": foreign}, app.delete_object)):
            try:
                method(**kwargs)
            except ClientError as exc:
                if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 403 or exc.response["Error"]["Code"] != "AccessDenied":
                    raise ValueError("A specific S3 authorization refusal was not obtained") from None
                denied.append(check)
            else:
                raise ValueError("Scoped S3 account accepted a forbidden operation")
        assert root.get_object(Bucket=bucket, Key=foreign)["Body"].read() == body
        try:
            root.head_bucket(Bucket=canary_bucket)
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                raise ValueError("Canary bucket absence is unproven") from None
        else:
            raise ValueError("An existing canary bucket must not be adopted")
        root.create_bucket(Bucket=canary_bucket)
        canary_created = True
        root.put_object(Bucket=canary_bucket, Key=canary_key, Body=body, IfNoneMatch="*", Metadata={"okf-operation": operation})
        canary_written = True
        assert root.get_object(Bucket=canary_bucket, Key=canary_key)["Body"].read() == body
        for check, kwargs, method in (("foreign_bucket_read", {"Bucket": canary_bucket, "Key": canary_key}, app.get_object),
                ("foreign_bucket_write", {"Bucket": canary_bucket, "Key": canary_key, "Body": body, "Metadata": {"okf-operation": operation}}, app.put_object),
                ("foreign_bucket_delete", {"Bucket": canary_bucket, "Key": canary_key}, app.delete_object),
                ("foreign_bucket_list", {"Bucket": canary_bucket, "MaxKeys": 1}, app.list_objects_v2)):
            try:
                method(**kwargs)
            except ClientError as exc:
                if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 403 or exc.response["Error"]["Code"] != "AccessDenied":
                    raise ValueError("Foreign-bucket authorization refusal was not obtained") from None
                denied.append(check)
            else:
                raise ValueError("Runtime principal accessed an operator-only bucket")
        try:
            listing = app.list_buckets()
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "AccessDenied":
                raise ValueError("Bucket discovery outcome is unknown") from None
            discovery = {"mode": "denied", "visible_bucket_count": 0}
        else:
            discovery = bucket_discovery([b["Name"] for b in listing.get("Buckets", [])], bucket)
        response = admin.call("GET", "list-users")
        if response.status_code != 403:
            raise ValueError("Runtime S3 account was not denied IAM administration")
        denied.append("iam_admin_list")
        try:
            bad.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "SignatureDoesNotMatch":
                raise ValueError("Specific bad-signature S3 refusal required") from None
        else:
            raise ValueError("Wrong S3 secret accepted")
        denied.append("wrong_secret")
        app.delete_object(Bucket=bucket, Key=key)
        keys.remove(key)
        return {"allowed": ["dedicated_bucket_head_list", "prefix_conditional_put", "prefix_cold_range_read", "prefix_delete",
            "multipart_complete_conditional", "multipart_abort"], "denied": denied,
            "tls_verified": True, "bucket_listing_scope": "dedicated-installation-bucket", "bucket_discovery": discovery}
    finally:
        for object_key, upload_id in uploads:
            root.abort_multipart_upload(Bucket=bucket, Key=object_key, UploadId=upload_id)
        for object_key in keys:
            try:
                metadata = root.head_object(Bucket=bucket, Key=object_key).get("Metadata", {})
            except ClientError as exc:
                if exc.response["ResponseMetadata"]["HTTPStatusCode"] == 404:
                    continue
                raise
            if metadata.get("okf-operation") != operation:
                raise ValueError("Probe cleanup ownership differs")
            root.delete_object(Bucket=bucket, Key=object_key)
        if canary_written:
            try:
                metadata = root.head_object(Bucket=canary_bucket, Key=canary_key).get("Metadata", {})
            except ClientError as exc:
                if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                    raise
            else:
                if metadata.get("okf-operation") != operation:
                    raise ValueError("Foreign-bucket canary ownership changed")
                root.delete_object(Bucket=canary_bucket, Key=canary_key)
        if canary_created:
            root.delete_bucket(Bucket=canary_bucket)  # Only our newly created, now-empty bucket; never recursive purge.
        admin.close(); app.close(); root.close(); bad.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "provision", "rights", "secrets", "fence", "compute", "stage", "release", "start", "verify"])
    parser.add_argument("--report")
    parser.add_argument("--config", required=True)
    parser.add_argument("--namespace-uid", required=True)
    parser.add_argument("--expected-fingerprint", required=True)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--apply", action="store_true", required=True)
    args = parser.parse_args()
    try:
        config, journal = Path(args.config).resolve(strict=True), Path(args.journal).resolve()
        if config.parent != journal.parent or config == journal:
            raise ValueError("Separate protected credential journal required")
        operation = ReplicaCredentials(json.loads(config.read_bytes()), args.namespace_uid, journal, args.expected_fingerprint)
        if args.phase == "verify":
            if args.report is None:
                raise ValueError("Explicit public report required")
            operation.verify(args.report)
        else:
            getattr(operation, args.phase)()
    except Exception as exc:  # noqa: BLE001 -- redact arbitrary provider/SQL failures; never print credentials.
        print(json.dumps({"credentials_refused": type(exc).__name__,
            "locations": [Path(f.filename).name + ":" + str(f.lineno) for f in traceback.extract_tb(exc.__traceback__)[-3:]]}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
