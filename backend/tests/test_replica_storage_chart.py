"""Distributed storage ownership is independent for every component/release."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from tests.test_distributed_chart import distributed

ROOT = Path(__file__).resolve().parents[2]


def storage(*extra, check=True):
    helm = os.environ.get("HELM_BIN", "helm")
    assert shutil.which(helm), "Helm required for storage acceptance"
    command = [helm, "template", "storage-test", str(ROOT / "deploy/helm/ai-knowledge-storage"),
        "--namespace", "test", "--set", "installationId=" + "1" * 32]
    for value in extra:
        command.extend(["--set", value])
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if not check:
        return result
    assert result.returncode == 0, result.stderr
    return [item for item in yaml.safe_load_all(result.stdout) if item]


def managed(component):
    return [f"{component}.provider=managed", f"{component}.claim.name=test-{component}",
        f"{component}.existingSecret=test-storage", f"{component}.provision=true"]


def test_external_creates_no_storage():
    assert storage() == []
    documents = distributed("replicaStorage.postgres.provider=external", "replicaStorage.qdrant.provider=external",
        "replicaStorage.qdrant.url=https://vectors.example.invalid:6333")
    assert not any(item["kind"] in {"StatefulSet", "PersistentVolumeClaim", "Secret"} for item in documents)
    assert not any(item["kind"] == "Service" and item["metadata"]["name"] in {"postgres", "qdrant"} for item in documents)


def test_mixed_ownership_is_per_component():
    documents = storage(*managed("qdrant"))
    assert {item["metadata"]["name"] for item in documents if item["kind"] == "StatefulSet"} == {"storage-test-qdrant"}
    assert not any(item["kind"] == "Secret" for item in documents)
    assert not any(item["kind"] in {"ClusterRole", "CustomResourceDefinition", "Namespace"} for item in documents)


def test_uninstall_preserves_data_contract():
    documents = storage(*managed("postgres"), *managed("qdrant"), *managed("artifacts"))
    claims = [item for item in documents if item["kind"] == "PersistentVolumeClaim"]
    assert len(claims) == 3
    assert all(item["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep" for item in claims)
    assert all(item["spec"]["accessModes"] == ["ReadWriteOnce"] for item in claims)
    for workload in (item for item in documents if item["kind"] == "StatefulSet"):
        pod = workload["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert "@sha256:" in pod["containers"][0]["image"]
        assert not any("value" in entry and "PASSWORD" in entry["name"]
            for entry in pod["containers"][0].get("env", []))


def test_storage_provision_is_explicit_and_external_never_claims_pvc():
    assert not any(item["kind"] == "PersistentVolumeClaim" for item in storage(
        "qdrant.provider=managed", "qdrant.claim.name=existing-qdrant", "qdrant.existingSecret=test-storage"))
    assert storage("postgres.provision=true", check=False).returncode != 0


@pytest.mark.parametrize("override", ["profile=resilient", "artifacts.backend=rwx,artifacts.provider=managed"])
def test_resilience_rejects_unverified_disks(override):
    result = storage(override, check=False)
    assert result.returncode != 0


def test_runtime_resolves_external_tls():
    from types import SimpleNamespace
    from app.deployment.replica_profile import validate_replica_endpoints
    settings = SimpleNamespace(db_url="postgresql+psycopg://app:CANARY@customer-db:5432/knowledge?sslmode=verify-full",
        qdrant_url="https://customer-vector:6333", postgres_provider="external", qdrant_provider="external",
        environment="production")
    validate_replica_endpoints(settings)
    settings.qdrant_url = "http://customer-vector:6333"
    with pytest.raises(ValueError, match="TLS") as error:
        validate_replica_endpoints(settings)
    assert "CANARY" not in str(error.value)
    settings.qdrant_url = "https://customer-vector:6333"
    settings.db_url = "postgresql+psycopg://app:CANARY@customer-db:5432/knowledge?sslmode=require"
    with pytest.raises(ValueError, match="TLS"):
        validate_replica_endpoints(settings)

@pytest.mark.parametrize("tls", [False, True])
def test_managed_postgres_bounds_dead_client_detection_without_timing_out_transactions(tls):
    extra = ["postgres.tlsSecret=test-tls"] if tls else []
    documents = storage(*managed("postgres"), *extra)
    pg = next(v for v in documents if v["kind"] == "StatefulSet")
    args = pg["spec"]["template"]["spec"]["containers"][0].get("args", [])
    assert len(args) % 2 == 0 and all(args[i] == "-c" for i in range(0, len(args), 2))
    settings = dict(v.split("=", 1) for v in args[1::2])
    assert {key: settings.get(key) for key in (
        "tcp_keepalives_idle", "tcp_keepalives_interval", "tcp_keepalives_count", "tcp_user_timeout"
    )} == dict(tcp_keepalives_idle="30", tcp_keepalives_interval="10",
               tcp_keepalives_count="3", tcp_user_timeout="60000")
    assert not {"statement_timeout", "idle_in_transaction_session_timeout"} & settings.keys()
    if tls:
        assert settings["ssl"] == "on" and settings["ssl_key_file"] == "/run/tls/tls.key"
