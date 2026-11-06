"""Replica configuration is bound to an operator-provided keyed identity."""
import json
from pathlib import Path

import pytest

from app.config import Settings


def configured(tmp_path):
    from app.deployment.runtime_identity import identity_document
    settings = Settings(_env_file=None, data_dir=tmp_path / "bootstrap", app_secret_key="a" * 64,
        database_url="postgresql+psycopg://user:CANARY_PG_PASSWORD@database:5432/replica_test_example")
    settings.okf_deployment_schema, settings.deployment_mode, settings.app_role = "replicas-v1", "distributed", "api"
    settings.deployment_namespace = "test-namespace"
    settings.artifact_backend, settings.artifact_store_id = "s3", "test-artifacts"
    settings.artifact_uploads_enabled = True
    settings.frontend_replicas, settings.api_replicas, settings.worker_replicas = 2, 2, 2
    settings.runtime_identity_file = tmp_path / "identity.json"
    document = identity_document(settings, installation_id="1" * 32, storage_receipt_digest="sha256:" + "2" * 64)
    settings.runtime_identity_file.write_text(json.dumps(document), encoding="utf-8")
    values = settings.model_dump()
    values["_env_file"] = None
    return values, document


def test_api_and_worker_share_identity_with_different_local_scratch(tmp_path):
    from app.deployment.runtime_identity import validate_identity
    values, document = configured(tmp_path)
    api = Settings(**values)
    worker = Settings(**{**values, "app_role": "worker", "data_dir": tmp_path / "worker-data",
        "diagnostics_dir": tmp_path / "worker-diagnostics", "artifact_scratch_root": tmp_path / "worker-scratch"})
    assert validate_identity(api).installation_id == validate_identity(worker).installation_id == document["installation_id"]
    assert "CANARY_PG_PASSWORD" not in json.dumps(document)
    assert "a" * 64 not in json.dumps(document)


@pytest.mark.parametrize("field,value", [
    ("app_secret_key", "b" * 64),
    ("database_url", "postgresql+psycopg://user:CANARY_PG_PASSWORD@different-db:5432/replica_test_example"),
    ("llm_global_max_concurrency", 2),
    ("deployment_namespace", "restored-namespace"),
    ("runtime_image_id", "different-backend@sha256:" + "5" * 64),
])
def test_identity_rejects_different_common_settings(tmp_path, field, value):
    values, _ = configured(tmp_path)
    with pytest.raises(ValueError, match="identity") as error:
        Settings(**{**values, field: value})
    assert "CANARY_PG_PASSWORD" not in str(error.value)


def test_identity_rejects_changed_receipt_and_unknown_fields(tmp_path):
    values, document = configured(tmp_path)
    document["unexpected"] = "CANARY_PG_PASSWORD"
    Path(values["runtime_identity_file"]).write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        Settings(**values)


def test_distributed_without_identity_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="identity|verified"):
        Settings(_env_file=None, data_dir=tmp_path, okf_deployment_schema="replicas-v1",
            deployment_mode="distributed", app_role="api", artifact_backend="s3",
            deployment_namespace="test-namespace",
            artifact_store_id="test-artifacts", artifact_uploads_enabled=True,
            worker_replicas=1, database_url="postgresql+psycopg://user:CANARY@database/test")


def test_identity_binds_custom_ca_bytes_at_the_same_path(tmp_path):
    from app.deployment.bootstrap import OperatorSettings
    from app.deployment.runtime_identity import identity_document, validate_identity
    values, _ = configured(tmp_path)
    certificates = tmp_path / "ca-bundle.crt"
    certificates.write_bytes(b"public-test-root-one")
    values["runtime_trust_file"] = certificates
    prepared = OperatorSettings(**values)
    document = identity_document(prepared, installation_id="1" * 32, storage_receipt_digest="sha256:" + "2" * 64)
    Path(values["runtime_identity_file"]).write_text(json.dumps(document), encoding="utf-8")
    running = Settings(**values)
    certificates.write_bytes(b"public-test-root-two")
    with pytest.raises(ValueError, match="identity"):
        validate_identity(running)
