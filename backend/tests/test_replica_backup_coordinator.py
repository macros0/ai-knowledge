"""The owned-lab exporter refuses foreign scope and unverifiable physical drains."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest


def coordinator():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_backup.py"
    assert path.is_file(), "Live backup coordinator is missing"
    spec = importlib.util.spec_from_file_location("backup_coordinator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config():
    run = "r5o2r20261101"
    return {"run_id": run, "namespace": f"replica-test-{run}", "context": "kind-okf-policy",
        "ssh_host": "10.10.1.52", "installation_id": "4" * 32,
        "storage_release": "replica-storage-recovery", "application_release": "replica-application-recovery",
        "database": f"replica_test_{run}", "collection": f"replica_test_{run}",
        "bucket": f"replica-test-{run}", "prefix": f"replica_test/{run}/application/",
        "store_id": "replica-recovery-app",
        "ports": {"postgres": 25441, "qdrant": 25443, "grpc": 25444, "artifacts": 25445, "frontend": 25449}}


@pytest.mark.parametrize("field,value", [("namespace", "default"), ("database", "application"),
    ("prefix", "customer/"), ("ssh_host", "-oProxyCommand=bad"), ("context", "production"),
    ("collection", "knowledge"), ("bucket", "existing-customer"), ("installation_id", "invalid")])
def test_foreign_scope_refused_before_any_provider_access(field, value):
    module = coordinator()
    inputs = config()
    inputs[field] = value
    with pytest.raises(ValueError):
        module.validate_config(inputs)


def objects():
    value = config()
    labels = {"okf-run-id": value["run_id"], "app.kubernetes.io/instance": value["storage_release"]}
    return [{"kind": "Pod", "metadata": {"name": f"{value['storage_release']}-{role}-0", "labels": labels},
        "status": {"phase": "Running"}, "spec": {"nodeName": "node1"}}
        for role in ("postgres", "qdrant", "artifacts")]


@pytest.mark.parametrize("fault", ["unknown_pod", "terminating_worker", "scheduled_job", "unreachable_node"])
def test_drain_needs_absent_writers_and_healthy_nodes(fault):
    module = coordinator()
    items = objects()
    nodes = {"node1": True}
    if fault in {"unknown_pod", "terminating_worker"}:
        items.append({"kind": "Pod", "metadata": {"name": fault, "deletionTimestamp": "now"},
            "status": {"phase": "Running"}, "spec": {"nodeName": "node1"}})
    elif fault == "scheduled_job":
        items.append({"kind": "CronJob", "spec": {"suspend": False}})
    else:
        nodes["node1"] = False
    with pytest.raises(ValueError):
        module.validate_drained(items, config(), nodes)
    assert module.validate_drained(objects(), config(), {"node1": True}) is None


def test_failed_pg_dump_cannot_be_accepted_as_success(tmp_path):
    module = coordinator()
    received = bytearray()

    class Writer:
        def add(self, _entry, stream):
            while block := stream.read(4096):
                received.extend(block)

    with pytest.raises(ValueError, match="export"):
        module.export_postgres(Writer(), "1" * 32,
            [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'partial');sys.exit(2)"],
            {}, tmp_path / "private-stderr.log")
    assert received == b"partial"


def test_pg_dump_uses_owned_pod_local_socket_and_ssh_without_credentials():
    module = coordinator()
    inputs = config()
    inputs["credentials"] = {"POSTGRES_PASSWORD": "private-canary"}
    command = module.postgres_dump_command(inputs, "00000004-000002B2-1")
    joined = " ".join(command)
    assert "private-canary" not in joined
    assert "ubuntu@10.10.1.52" in command
    assert "--host=/var/run/postgresql" in joined
    assert "replica-storage-recovery-postgres-0" in joined
    assert "--snapshot=00000004-000002B2-1" in joined
    with pytest.raises(ValueError):
        module.postgres_dump_command(inputs, "snapshot;foreign")


def test_destination_requires_user_evidence_and_approved_parent(tmp_path):
    module = coordinator()
    evidence = {"authority": "user-confirmed", "source_failure_domain": "pve-host",
        "destination_failure_domain": "separate-pc", "production_schedule": "disabled",
        "scope": "owned-lab", "backup_parent": str(tmp_path)}
    receipt = tmp_path / "evidence.json"
    receipt.write_text(json.dumps(evidence))
    assert module.destination_binding(receipt, tmp_path / "new-backup").failure_domain == "separate-pc"
    with pytest.raises(ValueError):
        module.destination_binding(receipt, tmp_path.parent / "foreign")
    evidence["destination_failure_domain"] = evidence["source_failure_domain"]
    receipt.write_text(json.dumps(evidence))
    with pytest.raises(ValueError):
        module.destination_binding(receipt, tmp_path / "new-backup")
