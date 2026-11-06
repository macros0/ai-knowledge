"""Import an authenticated format3 archive into new held lab storage; never activate jobs.

Identity rebinding, authentication invalidation and explicit reviewed activation
must follow verified imports. A failed target stays stopped and is never reused
by this fresh-target command. The source must be unavailable throughout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryFile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replica_backup import ROOT, Lab, connections, digest, validate_config


def assert_new_target(config, plan):
    validate_config(config)
    if (config["namespace"] == plan.source.namespace or config["installation_id"] == plan.source.installation_id
            or config["database"] == "replica_test_" + plan.source.namespace.removeprefix("replica-test-")
            or config["bucket"] == plan.source.namespace):
        raise ValueError("New isolated recovery target required")


def assert_jobs_held(items):
    for item in items:
        if item["kind"] == "Deployment" and item.get("spec", {}).get("replicas", 1) != 0:
            raise ValueError("Recovery application must stay stopped")
        if (item["kind"] == "Pod" and item.get("metadata", {}).get("labels", {}).get("okf-role") in {"api", "worker", "frontend"}
                and item.get("status", {}).get("phase") not in {"Succeeded", "Failed"}):
            raise ValueError("Recovery application Pod remains")
        if item["kind"] == "Job" and not item.get("status", {}).get("succeeded"):
            raise ValueError("Unknown recovery job")


def source_offline(source_lab):
    for item in source_lab.inventory():
        if item["kind"] in {"Deployment", "StatefulSet"} and item["spec"].get("replicas", 1) != 0:
            raise ValueError("Source workload is not stopped")
        if item["kind"] == "Pod" and item.get("status", {}).get("phase") not in {"Succeeded", "Failed"}:
            raise ValueError("Source Pod remains available")


def volumes(lab):
    values = json.loads(lab.kubectl("get", "pvc", "-o", "json"))["items"]
    if (len(values) != 3 or any(v["status"].get("phase") != "Bound"
            or v["metadata"].get("labels", {}).get("okf-run-id") != lab.config["run_id"] for v in values)):
        raise ValueError("Three owned bound recovery volumes required")
    return sorted((v["metadata"]["name"], v["metadata"]["uid"], v["spec"]["volumeName"]) for v in values)


def restore_postgres(reader, entry_id, lab, private_log):
    c = lab.config
    remote = ["kubectl", "--context", c["context"], "-n", c["namespace"], "exec", "-i",
        c["storage_release"] + "-postgres-0", "--", "pg_restore", "--host=/var/run/postgresql",
        "--username=replica", "--dbname=" + c["database"], "--single-transaction", "--exit-on-error", "--no-owner", "--no-acl"]
    command = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o",
        f"UserKnownHostsFile={ROOT / 'tests/tmp/replica-lab-known_hosts'}", "ubuntu@10.10.1.52", shlex.join(remote)]
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("PG")}
    with Path(private_log).open("xb") as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=errors, stderr=errors, env=environment)
        try:
            reader.copy(entry_id, process.stdin)
            process.stdin.close()
            if process.wait(timeout=180):
                raise ValueError("PostgreSQL recovery import failed")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=15)


def import_backup(config, *, namespace_uid, source_config, source_namespace_uid, archive, key_file, report):
    from app.deployment.replica_backup import BackupReader
    from sqlalchemy import inspect, text

    reader = BackupReader(archive, Path(key_file).read_bytes())  # Full archive verification precedes any provider mutation.
    plan = reader.manifest.plan
    assert_new_target(config, plan)
    if source_config["namespace"] != plan.source.namespace or source_config["installation_id"] != plan.source.installation_id:
        raise ValueError("Explicit source backup identity required")
    lab, source = Lab(config, namespace_uid), Lab(source_config, source_namespace_uid)
    source_offline(source)
    lab.drained()
    assert_jobs_held(lab.inventory())
    target_volumes = volumes(lab)
    if {v[2] for v in target_volumes} & {v[2] for v in volumes(source)}:
        raise ValueError("Recovery volumes overlap source")
    engine, s3, q = connections(config)
    try:
        with engine.connect() as connection:
            if inspect(connection).get_table_names(schema="public") or connection.scalar(text("SELECT current_database()")) != config["database"]:
                raise ValueError("Empty new PostgreSQL database required")
        collections = q.get("/collections")
        collections.raise_for_status()
        if collections.json()["result"]["collections"]:
            raise ValueError("Empty new Qdrant instance required")
        if s3.list_buckets()["Buckets"]:
            raise ValueError("Empty new artifact instance required")

        def guard():
            source_offline(source)
            lab.drained()
            assert_jobs_held(lab.inventory())
            if volumes(lab) != target_volumes:
                raise ValueError("Recovery volume binding changed")

        private = Path(config["ca_file"]).parent
        guard()
        pg_entry = next(e for e in plan.inventory if e.component == "postgres")
        restore_postgres(reader, pg_entry.id, lab, private / (plan.backup_id + "-pg-restore.log"))
        print(json.dumps({"phase": "postgres-imported-held", "application_replicas": 0}), flush=True)
        with engine.connect() as connection:
            state = connection.execute(text("SELECT enabled,operation_id,epoch FROM replica_maintenance_state WHERE id=1")).mappings().one()
            if not state["enabled"] or state["operation_id"] != plan.backup_id or state["epoch"] != plan.source.epoch:
                raise ValueError("Imported maintenance fence differs")
            actual_counts = {k: connection.scalar(text("SELECT count(*) FROM " + t)) for k, t in {
                "documents": "documents", "concepts": "okf_concepts", "chunks": "document_chunks",
                "work_items": "work_items", "audit_events": "audit_log"}.items()}
            artifacts = connection.execute(text("SELECT * FROM artifact_objects WHERE state='ready' ORDER BY object_id")).mappings().all()
            actual_counts["blobs"] = len(artifacts)
            if actual_counts != plan.counts.model_dump():
                raise ValueError("Imported SQL totals differ")
            work_sql = text("SELECT id,status,owner_id,fence,attempts,maintenance_epoch FROM work_items ORDER BY id")
            held_work_hash = digest([dict(r) for r in connection.execute(work_sql).mappings()])
            guard()
            q_entry = next(e for e in plan.inventory if e.component == "qdrant")
            with TemporaryFile(dir=private) as stream:
                reader.copy(q_entry.id, stream)
                stream.seek(0)
                response = q.post(f"/collections/{config['collection']}/snapshots/upload?wait=true&priority=snapshot",
                    files={"snapshot": ("recovery.snapshot", stream, "application/octet-stream")})
                response.raise_for_status()
            guard()
            s3.create_bucket(Bucket=config["bucket"])
            entries = {e.id: e for e in plan.inventory if e.component == "artifacts"}
            if set(entries) != {a["object_id"] for a in artifacts}:
                raise ValueError("Imported artifact inventory differs")
            for artifact in artifacts:
                entry = entries[artifact["object_id"]]
                if (artifact["store_id"] != config["store_id"] or artifact["key"] != "objects/" + entry.id
                        or (entry.expected_sha256, entry.expected_size) != (artifact["sha256"], artifact["size"])):
                    raise ValueError("Imported artifact reference differs")
                key = config["prefix"] + artifact["key"]
                with TemporaryFile(dir=private) as stream:
                    reader.copy(entry.id, stream)
                    stream.seek(0)
                    s3.put_object(Bucket=config["bucket"], Key=key, Body=stream, ContentLength=artifact["size"],
                        ContentType=artifact["media_type"], Metadata={"sha256": artifact["sha256"]}, IfNoneMatch="*")
                cold = s3.get_object(Bucket=config["bucket"], Key=key)
                result_hash, size = hashlib.sha256(), 0
                try:
                    for block in iter(lambda body=cold["Body"]: body.read(4 * 1024 * 1024), b""):
                        size += len(block)
                        result_hash.update(block)
                finally:
                    cold["Body"].close()
                if size != artifact["size"] or result_hash.hexdigest() != artifact["sha256"]:
                    raise ValueError("Restored artifact checksum differs")
            guard()
            reader.verify()
            if digest([dict(r) for r in connection.execute(work_sql).mappings()]) != held_work_hash:
                raise ValueError("Recovery work changed before activation")
            points = q.post(f"/collections/{config['collection']}/points/count", json={"exact": True})
            points.raise_for_status()
            result = {"status": "providers-restored-held", "backup_id": plan.backup_id, "target_namespace": config["namespace"],
                "namespace_uid": namespace_uid, "target_volumes": target_volumes, "counts": actual_counts,
                "qdrant_points": points.json()["result"]["count"], "artifact_hashes_verified": len(artifacts),
                "restored_executed_jobs": 0, "held_work_state_sha256": held_work_hash, "source_unavailable": True,
                "import_finished_at": datetime.now(timezone.utc).isoformat(), "identity_rebound": False,
                "activated": False, "production_schedule": "disabled"}
            Path(report).write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result), flush=True)
    finally:
        q.close()
        s3.close()
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "source-config", "archive", "key-file", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--namespace-uid", required=True)
    parser.add_argument("--source-namespace-uid", required=True)
    args = parser.parse_args()
    try:
        import_backup(json.loads(args.config.read_bytes()), namespace_uid=args.namespace_uid,
            source_config=json.loads(args.source_config.read_bytes()), source_namespace_uid=args.source_namespace_uid,
            archive=args.archive, key_file=args.key_file, report=args.report)
        return 0
    except Exception as exc:  # noqa: BLE001 -- raw provider failures must stay in the protected directory.
        import traceback
        with (args.config.parent / "restore-error.log").open("w", encoding="utf-8") as errors:
            traceback.print_exc(file=errors)
        frames = traceback.extract_tb(exc.__traceback__)
        print(json.dumps({"status": "restore-refused-target-held", "type": type(exc).__name__,
            "locations": [{"function": f.name, "line": f.lineno} for f in frames
                if Path(f.filename).resolve() == Path(__file__).resolve()],
            "sqlstate": getattr(getattr(exc, "orig", None), "sqlstate", None)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
