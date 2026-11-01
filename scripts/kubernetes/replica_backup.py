"""Format3 export for the explicitly owned kind training lab, never a production scheduler.

PostgreSQL keeps one exported snapshot alive throughout the copy. On uncertainty
the SQL fence stays closed; this command never times out an LLM capacity owner.
Credentials are read from a protected file and are never printed or put in argv.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
PUBLIC_REFUSALS = {
    "Owned isolated lab scope required", "Explicit lab release required", "Explicit isolated loopback forwards required",
    "Independent approved destination required", "Recorded namespace UID required", "Separate backup key required",
    "New independent destination with free capacity required", "Selected PostgreSQL 17 exporter required",
    "Namespace ownership changed", "Unknown writer deployment", "Expected source topology differs",
    "Source role image is ambiguous", "This recipe supports only the synthetic isolated lab",
    "SQL installation or storage scope differs", "Foreign workload refused", "Owned lab Kubernetes operation refused",
    "Writer drain unverified; maintenance remains active", "Provider requests are active or uncertain; source stays held",
    "Artifact deletion is uncertain", "Foreign artifact scope", "Unsafe Qdrant snapshot name", "Frozen source ownership differs",
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def validate_config(value):
    run = value.get("run_id", "")
    expected = {"namespace": f"replica-test-{run}", "database": f"replica_test_{run}",
        "collection": f"replica_test_{run}", "bucket": f"replica-test-{run}",
        "prefix": f"replica_test/{run}/application/", "context": "kind-okf-policy"}
    mismatches = [field for field, expected_value in expected.items() if value.get(field) != expected_value]
    if value.get("ssh_host") not in {"10.10.1.52", "ubuntu@10.10.1.52"}:
        mismatches.append("ssh_host")
    if mismatches:
        # Field names are public schema constants; no caller value is included.
        print(json.dumps({"invalid_scope_fields": mismatches}), file=sys.stderr)
    if (not re.fullmatch(r"[a-z0-9]{8,32}", run) or mismatches
            or not re.fullmatch(r"[0-9a-f]{32}", value.get("installation_id", ""))):
        raise ValueError("Owned isolated lab scope required")
    for field in ("application_release", "storage_release", "store_id"):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value.get(field, "")):
            raise ValueError("Explicit lab release required")
    ports = value.get("ports", {})
    if (set(ports) != {"postgres", "qdrant", "grpc", "artifacts", "frontend"}
            or len(set(ports.values())) != 5
            or any(type(p) is not int or not 25400 <= p <= 25999 for p in ports.values())):
        raise ValueError("Explicit isolated loopback forwards required")


def destination_binding(evidence_file, archive):
    from app.deployment.replica_backup import Destination
    from app.services.diagnostics.paths import assert_no_links
    evidence = json.loads(Path(evidence_file).read_bytes())
    archive = Path(archive).absolute()
    parent = Path(evidence["backup_parent"]).absolute()
    assert_no_links(archive, parent)
    if (archive.parent != parent or evidence.get("authority") != "user-confirmed"
            or evidence.get("production_schedule") != "disabled" or evidence.get("scope") != "owned-lab"
            or evidence["source_failure_domain"] == evidence["destination_failure_domain"]):
        raise ValueError("Independent approved destination required")
    return Destination(store_id="separate-pc-training-backup", failure_domain=evidence["destination_failure_domain"],
        platform_evidence_sha256=digest(evidence))


def validate_drained(items, config, nodes):
    storage = {f"{config['storage_release']}-{role}-0" for role in ("postgres", "qdrant", "artifacts")}
    for item in items:
        kind = item["kind"]
        if kind == "CronJob" and item.get("spec", {}).get("suspend") is not True:
            raise ValueError("Scheduled writer is not suspended")
        if kind == "Job" and not item.get("status", {}).get("succeeded"):
            raise ValueError("Maintenance job has not completed")
        if kind != "Pod" or item.get("status", {}).get("phase") in {"Succeeded", "Failed"}:
            continue
        metadata = item.get("metadata", {})
        if (metadata.get("name") not in storage or metadata.get("deletionTimestamp")
                or metadata.get("labels", {}).get("app.kubernetes.io/instance") != config["storage_release"]
                or nodes.get(item.get("spec", {}).get("nodeName")) is not True):
            raise ValueError("Writer or unreachable node remains")


def runtime_config_name(deployments):
    names = set()
    protected = {"MODEL_MODE", "LLM_BASE_URL", "EMBEDDING_PROVIDER", "AUTH_PROVIDER", "AUDIT_DB_ENABLED", "AUDIT_STDOUT_MODE"}
    for role in ("api", "worker"):
        if role not in deployments:
            raise ValueError("Backup runtime role is missing")
        containers = deployments[role]["spec"]["template"]["spec"]["containers"]
        if len(containers) != 1:
            raise ValueError("Explicit single-container backup runtime required")
        container = containers[0]
        refs = [entry["configMapRef"]["name"] for entry in container.get("envFrom", []) if "configMapRef" in entry]
        overrides = [entry for entry in container.get("env", []) if entry["name"].upper() in protected]
        if len(refs) != 1 or any(entry != {"name": entry["name"], "value": "http://model-stub:8000/v1"}
                or entry["name"].upper() != "LLM_BASE_URL" for entry in overrides):
            raise ValueError("Ambiguous or overridden backup runtime configuration")
        names.add(refs[0])
    if len(names) != 1:
        raise ValueError("Backup runtime configurations differ")
    return names.pop()


class Lab:
    def __init__(self, config, namespace_uid):
        validate_config(config)
        if not re.fullmatch(r"[0-9a-f-]{36}", namespace_uid):
            raise ValueError("Recorded namespace UID required")
        self.config, self.namespace_uid = config, namespace_uid

    def kubectl(self, *args, namespaced=True):
        c = self.config
        command = ["kubectl", "--context", c["context"]]
        if namespaced:
            command += ["-n", c["namespace"]]
        command += list(args)
        result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={ROOT / 'tests/tmp/replica-lab-known_hosts'}",
            "ubuntu@10.10.1.52", shlex.join(command)], capture_output=True, timeout=75, check=False)
        if result.returncode:
            raise ValueError("Owned lab Kubernetes operation refused")
        return result.stdout

    def assert_namespace(self):
        namespace = json.loads(self.kubectl("get", "namespace", self.config["namespace"], "-o", "json", namespaced=False))
        labels = namespace["metadata"].get("labels", {})
        if (namespace["metadata"]["uid"] != self.namespace_uid
                or namespace["metadata"].get("deletionTimestamp")
                or labels.get("okf-run-id") != self.config["run_id"]
                or labels.get("app.kubernetes.io/managed-by") != "okf-replica-acceptance"):
            raise ValueError("Namespace ownership changed")

    def inventory(self):
        self.assert_namespace()
        return json.loads(self.kubectl("get", "deploy,sts,pods,jobs,cronjobs", "-o", "json"))["items"]

    def drained(self):
        nodes = json.loads(self.kubectl("get", "nodes", "-o", "json", namespaced=False))["items"]
        ready = {n["metadata"]["name"]: any(c["type"] == "Ready" and c["status"] == "True"
            for c in n.get("status", {}).get("conditions", [])) for n in nodes}
        validate_drained(self.inventory(), self.config, ready)

    def scale(self, role, before, after):
        self.assert_namespace()
        obj = json.loads(self.kubectl("get", "deploy", role, "-o", "json"))
        if obj["metadata"].get("labels", {}).get("app.kubernetes.io/instance") != self.config["application_release"]:
            raise ValueError("Foreign workload refused")
        self.kubectl("scale", "deployment/" + role, "--current-replicas=" + str(before), "--replicas=" + str(after))


def export_postgres(writer, entry_id, command, environment, private_stderr):
    with Path(private_stderr).open("xb") as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, env=environment)
        try:
            writer.add(entry_id, process.stdout)
            if process.wait(timeout=180):
                raise ValueError("PostgreSQL export failed")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=15)


def postgres_dump_command(config, snapshot):
    validate_config(config)
    if not re.fullmatch(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{8}-[0-9]+", snapshot):
        raise ValueError("Invalid PostgreSQL snapshot identity")
    # A single reset on kubectl's TCP port-forward can terminate all streams.
    # The selected server's client instead uses its local Unix socket. SSH and
    # Kubernetes carry the export to the controller without a password in argv.
    remote = ["kubectl", "--context", config["context"], "-n", config["namespace"], "exec",
        config["storage_release"] + "-postgres-0", "--", "pg_dump", "--host=/var/run/postgresql",
        "--username=replica", "--dbname=" + config["database"], "--format=custom", "--no-owner", "--no-acl",
        "--snapshot=" + snapshot]
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=yes", "-o",
        f"UserKnownHostsFile={ROOT / 'tests/tmp/replica-lab-known_hosts'}", "ubuntu@10.10.1.52", shlex.join(remote)]


class ResponseStream:
    def __init__(self, response):
        from app.deployment.replica_backup import CHUNK
        self.blocks = response.iter_bytes(CHUNK)

    def read(self, _size):
        return next(self.blocks, b"")


def connections(config):
    import boto3
    import httpx
    from botocore.config import Config
    from sqlalchemy import URL, create_engine
    c, ports = config["credentials"], config["ports"]
    trust = str(Path(config["ca_file"]).resolve(strict=True))
    engine = create_engine(URL.create("postgresql+psycopg", username="replica", password=c["POSTGRES_PASSWORD"],
        host="127.0.0.1", port=ports["postgres"], database=config["database"],
        query={"sslmode": "verify-full", "sslrootcert": trust, "connect_timeout": "8"}), pool_pre_ping=False)
    s3 = boto3.client("s3", endpoint_url=f"https://127.0.0.1:{ports['artifacts']}",
        aws_access_key_id=c["S3_ROOT_ACCESS_KEY"], aws_secret_access_key=c["S3_ROOT_SECRET_KEY"],
        region_name="us-east-1", verify=trust,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 0},
            connect_timeout=8, read_timeout=60, proxies={}))
    qdrant = httpx.Client(base_url=f"https://127.0.0.1:{ports['qdrant']}", trust_env=False,
        verify=ssl.create_default_context(cafile=trust), headers={"api-key": c["QDRANT_API_KEY"]}, timeout=90)
    return engine, s3, qdrant


def sql_source(connection, config):
    from sqlalchemy import text
    identity = connection.execute(text("SELECT * FROM replica_runtime_identity WHERE id=1")).mappings().one()
    control = connection.execute(text("SELECT * FROM artifact_storage_control WHERE id=1")).mappings().one()
    descriptor = control["active_descriptor"]
    endpoint = f"https://{config['storage_release']}-artifacts.{config['namespace']}.svc:9000"
    if (connection.scalar(text("SELECT current_database()")) != config["database"]
            or identity["schema_revision"] != "rpl05_maintenance_epoch"
            or connection.scalar(text("SELECT version_num FROM alembic_version")) != identity["schema_revision"]
            or identity["installation_id"] != config["installation_id"]
            or control["installation_id"] != config["installation_id"]
            or control["active_store_id"] != config["store_id"] or control["migration_id"] is not None
            or any(descriptor.get(k) != v for k, v in {"backend": "s3", "store_id": config["store_id"],
                "bucket": config["bucket"], "prefix": config["prefix"], "endpoint": endpoint}.items())):
        raise ValueError("SQL installation or storage scope differs")
    return dict(identity), dict(control)


def run_backup(config, *, namespace_uid, evidence_file, archive, key_file, pg_dump, report):
    from app.deployment.replica_backup import BackupPlan, BackupWriter, verify_backup
    from app.services.work.maintenance_control import (
        REQUIRED_WRITERS,
        MaintenanceOperator,
        enter_maintenance,
        leave_maintenance,
    )
    from sqlalchemy import text
    from test_scripts.replica_runtime_acceptance import Kubernetes
    from test_scripts.replica_runtime_faults import FaultProbe, require_quiescent
    lab = Lab(config, namespace_uid)
    destination = destination_binding(evidence_file, archive)
    key = Path(key_file).read_bytes()
    if len(key) != 32 or Path(key_file).resolve().is_relative_to(Path(archive).resolve()):
        raise ValueError("Separate backup key required")
    if Path(archive).exists() or shutil.disk_usage(Path(archive).parent).free < 1024**3:
        raise ValueError("New independent destination with free capacity required")
    version = subprocess.run([str(pg_dump), "--version"], capture_output=True, timeout=10, check=True).stdout
    if b"(PostgreSQL) 17." not in version:
        raise ValueError("Selected PostgreSQL 17 exporter required")
    items = lab.inventory()
    deployments = {i["metadata"]["name"]: i for i in items if i["kind"] == "Deployment"}
    if set(deployments) != {"frontend", "api", "worker", "model-stub"}:
        raise ValueError("Unknown writer deployment")
    intended = {r: deployments[r]["spec"]["replicas"] for r in ("frontend", "api", "worker")}
    if intended != {"frontend": 2, "api": 2, "worker": 2} or deployments["model-stub"]["spec"]["replicas"] != 1:
        raise ValueError("Expected source topology differs")
    images = {}
    for role in intended:
        image_ids = {s["imageID"][-71:] for i in items if i["kind"] == "Pod"
            and i["metadata"].get("labels", {}).get("okf-role") == role for s in i["status"]["containerStatuses"]}
        if len(image_ids) != 1:
            raise ValueError("Source role image is ambiguous")
        images[role] = image_ids.pop()
    runtime_name = runtime_config_name(deployments)
    selected = json.loads(lab.kubectl("get", "configmap", runtime_name, "-o", "json"))
    if selected.get("immutable") is not True:
        raise ValueError("Immutable selected backup runtime required")
    runtime = selected["data"]
    if runtime.get("EMBEDDING_PROVIDER") != "fake" or runtime.get("AUTH_PROVIDER") != "simulation":
        raise ValueError("This recipe supports only the synthetic isolated lab")
    value = {**config, "namespace_uid": namespace_uid, "frontend_url": f"http://127.0.0.1:{config['ports']['frontend']}", "simulation_username": "replica.admin"}
    kube = Kubernetes(value, ssh_host="ubuntu@10.10.1.52", known_hosts=ROOT / "tests/tmp/replica-lab-known_hosts")
    probe = FaultProbe(kube, value)
    require_quiescent(probe)
    actual = kube.python(probe.api_pods()[0]["metadata"]["name"], '''import json
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
s=get_settings();identity=enforce_runtime_identity(s)
print(json.dumps({'fingerprint':identity.fingerprint,'audit_db_enabled':s.audit_db_enabled,'audit_stdout_mode':s.audit_stdout_mode}))
''')
    if (actual.get("fingerprint") != probe.identity["fingerprint"] or type(actual.get("audit_db_enabled")) is not bool
            or actual.get("audit_stdout_mode") not in {"disabled", "durable"}):
        raise ValueError("Actual backup runtime configuration differs")
    audit_settings = SimpleNamespace(okf_deployment_schema="replicas-v1", app_role="api",
        audit_db_enabled=actual["audit_db_enabled"], audit_stdout_mode=actual["audit_stdout_mode"])
    engine, s3, q = connections(config)
    operation = uuid4().hex
    started = datetime.now(timezone.utc)
    with engine.connect() as preflight:
        installed, _ = sql_source(preflight, config)
        if installed["fingerprint"] != actual["fingerprint"]:
            raise ValueError("Installed backup runtime identity differs")
    with MaintenanceOperator(engine, operation_id=operation, owner_id="owned-lab-pc", settings=audit_settings) as op:
        with op.transaction() as session:
            installed, _ = sql_source(session.connection(), config)
            if installed["fingerprint"] != actual["fingerprint"]:
                raise ValueError("Installed backup runtime changed before maintenance")
            epoch = enter_maintenance(session, operation, kind="backup", intended_counts=intended)
        print(json.dumps({"phase": "fenced", "epoch": epoch, "operation_id": operation}), flush=True)
        for role in ("frontend", "api", "worker", "model-stub"):
            lab.scale(role, 1 if role == "model-stub" else intended[role], 0)
        deadline = time.monotonic() + 180
        while True:
            try:
                lab.drained()
                break
            except ValueError:
                if time.monotonic() >= deadline:
                    raise ValueError("Writer drain unverified; maintenance remains active") from None
                time.sleep(2)
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as frozen:
            frozen.execute(text("SET TRANSACTION READ ONLY"))
            snapshot = frozen.scalar(text("SELECT pg_export_snapshot()"))
            consistent = frozen.scalar(text("SELECT clock_timestamp()"))
            identity, _control = sql_source(frozen, config)
            if identity["fingerprint"] != actual["fingerprint"]:
                raise ValueError("Frozen backup runtime identity differs")
            if frozen.scalar(text("SELECT count(*) FROM replica_capacity_slots WHERE resource='llm' AND owner_id IS NOT NULL")):
                raise ValueError("Provider requests are active or uncertain; source stays held")
            artifacts = frozen.execute(text("SELECT * FROM artifact_objects WHERE state='ready' ORDER BY object_id")).mappings().all()
            if frozen.scalar(text("SELECT count(*) FROM artifact_objects WHERE state='deleting'")):
                raise ValueError("Artifact deletion is uncertain")
            if any(a["store_id"] != config["store_id"] or a["key"] != "objects/" + a["object_id"] for a in artifacts):
                raise ValueError("Foreign artifact scope")
            counts = {k: frozen.scalar(text("SELECT count(*) FROM " + table)) for k, table in {
                "documents": "documents", "concepts": "okf_concepts", "chunks": "document_chunks",
                "work_items": "work_items", "audit_events": "audit_log"}.items()}
            counts["blobs"] = len(artifacts)
            response = q.post(f"/collections/{config['collection']}/snapshots?wait=true")
            response.raise_for_status()
            qsnap = response.json()["result"]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", qsnap["name"]):
                raise ValueError("Unsafe Qdrant snapshot name")
            pg_id, q_id = uuid4().hex, uuid4().hex
            source = {"installation_id": config["installation_id"], "namespace": config["namespace"], "operation_id": operation,
                "epoch": epoch, "consistent_at": consistent, "failure_domain": json.loads(Path(evidence_file).read_bytes())["source_failure_domain"],
                "unknown_writers": 0, "provider_requests": 0, "writers": {r: "stopped" for r in REQUIRED_WRITERS}, "snapshot_id": snapshot}
            plan = BackupPlan.model_validate({"format": 3, "backup_id": operation, "schema_revision": identity["schema_revision"],
                "started_at": started, "source": source, "destination": destination, "images": images, "counts": counts,
                "storage": [{"component": r, "provider": "managed", "recipe": "selected-lab-basic-v1",
                    "scope_sha256": digest([config["namespace"], config["storage_release"], config.get(r, config["bucket"])])}
                    for r in ("postgres", "qdrant", "artifacts")], "model_vector_sha256": digest(identity["fingerprint"]),
                "component_receipts": {"postgres": digest([snapshot, consistent]), "qdrant": digest(qsnap),
                    "artifacts": digest([dict(a) for a in artifacts])}, "key_reference": "local-protected:training-backup-key",
                "inventory": [{"id": pg_id, "component": "postgres"}, {"id": q_id, "component": "qdrant",
                    "expected_size": qsnap.get("size"), "expected_sha256": qsnap.get("checksum")}]
                    + [{"id": a["object_id"], "component": "artifacts", "expected_size": a["size"], "expected_sha256": a["sha256"]}
                        for a in artifacts]})

            def guard():
                op.assert_owned()
                lab.drained()
                frozen.execute(text("SELECT 1"))  # The original snapshot connection must still exist.
                with op.transaction() as session:
                    state = session.execute(text("SELECT * FROM replica_maintenance_state WHERE id=1")).mappings().one()
                    token = session.scalar(text("SELECT maintenance_token FROM artifact_storage_control WHERE id=1"))
                    slots = session.scalar(text("SELECT count(*) FROM replica_capacity_slots WHERE resource='llm' AND owner_id IS NOT NULL"))
                    if not state["enabled"] or state["operation_id"] != operation or state["epoch"] != epoch or token != operation or slots:
                        raise ValueError("Frozen source ownership differs")
                return plan.source

            environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("PG")}
            with BackupWriter(archive, plan, key, source_guard=guard,
                    destination_guard=lambda: destination_binding(evidence_file, archive)) as writer:
                export_postgres(writer, pg_id, postgres_dump_command(config, snapshot),
                    environment, Path(config["ca_file"]).parent / (operation + "-pg-dump.log"))
                with q.stream("GET", f"/collections/{config['collection']}/snapshots/{qsnap['name']}") as response:
                    response.raise_for_status()
                    writer.add(q_id, ResponseStream(response))
                for artifact in artifacts:
                    result = s3.get_object(Bucket=config["bucket"], Key=config["prefix"] + artifact["key"])
                    try:
                        writer.add(artifact["object_id"], result["Body"])
                    finally:
                        result["Body"].close()
                writer.finish(now=frozen.scalar(text("SELECT clock_timestamp()")))
            cold = verify_backup(archive, key)
        with op.transaction() as session:
            leave_maintenance(session, operation, writer_receipts={r: "stopped" for r in REQUIRED_WRITERS})
    for role in ("model-stub", "api", "worker", "frontend"):
        lab.scale(role, 0, 1 if role == "model-stub" else intended[role])
    public = {"status": "independent-backup-verified", "format": 3, "backup_id": operation,
        "archive": str(Path(archive).absolute()), "namespace": config["namespace"], "source_epoch": epoch,
        "runtime_configmap": runtime_name, "runtime_fingerprint": actual["fingerprint"],
        "consistent_at": consistent.isoformat(), "counts": counts, "encrypted_objects": len(cold.objects),
        "plaintext_bytes": sum(o.size for o in cold.objects.values()), "production_schedule": "disabled", "restore_verified": False}
    Path(report).write_text(json.dumps(public, indent=2) + "\n")
    print(json.dumps(public), flush=True)
    q.close()
    s3.close()
    engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("config", "evidence-file", "archive", "key-file", "pg-dump", "report"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--namespace-uid", required=True)
    args = parser.parse_args()
    try:
        run_backup(json.loads(args.config.read_bytes()), namespace_uid=args.namespace_uid,
            evidence_file=args.evidence_file, archive=args.archive, key_file=args.key_file, pg_dump=args.pg_dump, report=args.report)
        return 0
    except Exception as exc:  # noqa: BLE001 -- provider errors can contain credentials; public message is fixed.
        import traceback
        private_log = args.config.parent / "backup-error.log"
        with private_log.open("w", encoding="utf-8") as errors:
            traceback.print_exc(file=errors)
        print(f"Backup refused ({type(exc).__name__}); inspect protected inputs; any entered fence remains held", file=sys.stderr)
        if type(exc) is ValueError and str(exc) in PUBLIC_REFUSALS:
            print(str(exc), file=sys.stderr)
        frames = traceback.extract_tb(exc.__traceback__)
        print(json.dumps({"failure_locations": [{"function": f.name, "line": f.lineno}
            for f in frames if Path(f.filename).resolve() == Path(__file__).resolve()],
            "sqlstate": getattr(getattr(exc, "orig", None), "sqlstate", None)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
