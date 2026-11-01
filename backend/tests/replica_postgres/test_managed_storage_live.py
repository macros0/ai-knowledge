"""Real selected S3/Qdrant services, only on explicit owned loopback test targets."""
import json
import os
from pathlib import Path

import pytest


class PrivateTarget(dict):
    def __repr__(self):
        return "PrivateTarget(<redacted>)"


@pytest.fixture
def target():
    path = os.environ.get("OKF_REPLICA_STORAGE_FILE")
    if not path:
        if os.environ.get("OKF_REPLICA_STORAGE_REQUIRED") == "1":
            pytest.fail("Required isolated managed storage target missing")
        pytest.skip("Explicit isolated managed storage file required")
    value = PrivateTarget(json.loads(Path(path).read_text(encoding="utf-8-sig")))
    if (value.get("namespace") != "replica-test-s3rpl20261031" or value.get("run_id") != "s3rpl20261031"
            or value.get("endpoint") != "http://127.0.0.1:25435" or value.get("qdrant_url") != "http://127.0.0.1:25433"
            or value.get("bucket") != "replica-test-s3rpl20261031"):
        pytest.fail("Only the explicitly owned loopback storage targets are permitted")
    return value


def settings(target, tmp_path):
    from app.config import Settings
    value = Settings(_env_file=None, data_dir=tmp_path)
    for name, configured in {"artifact_backend": "s3", "artifact_store_id": "managed-lab-s3",
            "artifact_s3_endpoint": target["endpoint"], "artifact_s3_bucket": target["bucket"],
            "artifact_s3_prefix": "replica_test/s3rpl20261031/", "artifact_s3_allow_http": True,
            "artifact_s3_access_key": target["access"], "artifact_s3_secret_key": target["secret"]}.items():
        setattr(value, name, configured)
    value.okf_deployment_schema, value.app_role = "replicas-v1", "api"
    value.deployment_namespace = target["namespace"]
    value.qdrant_url, value.qdrant_api_key = target["qdrant_url"], target["qdrant_key"]
    value.qdrant_prefer_grpc, value.qdrant_grpc_port = False, 25434
    value.qdrant_collection = "replica_test_s3rpl20261031"
    return value


def test_selected_rustfs_passes_actual_artifact_bootstrap_contract(target, tmp_path):
    from app.services.artifacts import get_blob_store
    from app.services.artifacts.control import migration_access
    from app.deployment.bootstrap import probe_artifacts
    value = settings(target, tmp_path)
    with migration_access():
        store = get_blob_store(value)
        try:
            store.client.create_bucket(Bucket=store.bucket)
        except store.client.exceptions.BucketAlreadyOwnedByYou:
            pass
    receipt = probe_artifacts(value, installation_id=target["installation_id"], selection_digest="sha256:" + "3" * 64)
    assert receipt["checks"] == ["conditional_put", "cold_read", "range_read", "delete"]
    assert receipt["qualification"] == "basic-bootstrap-only"


def test_selected_services_reject_wrong_credentials(target, tmp_path):
    from botocore.exceptions import ClientError
    from app.services.artifacts import get_blob_store
    from app.services.artifacts.control import migration_access
    import httpx
    value = settings(target, tmp_path)
    value.artifact_s3_secret_key = "WRONG_TEST_SECRET"
    with migration_access():
        store = get_blob_store(value)
        with pytest.raises(ClientError) as error:
            store.client.head_bucket(Bucket=store.bucket)
        assert error.value.response["ResponseMetadata"]["HTTPStatusCode"] in {401, 403}
    with httpx.Client(trust_env=False, timeout=10) as client:
        denied = client.get(target["qdrant_url"] + "/collections")
        allowed = client.get(target["qdrant_url"] + "/collections", headers={"api-key": target["qdrant_key"]})
    assert denied.status_code in {401, 403} and allowed.status_code == 200


def test_vector_bootstrap_verifies_schema_and_refuses_populated_collection(target, tmp_path):
    from uuid import uuid4
    from app.deployment.bootstrap import initialize_empty_vector_store
    from app.services.vector_store import VectorStore
    from qdrant_client import models as qm
    value = settings(target, tmp_path)
    initialize_empty_vector_store(value)
    store = VectorStore(settings=value)
    point_id = str(uuid4())
    try:
        store.client.upsert(store.collection, points=[qm.PointStruct(id=point_id,
            vector=[1.0] + [0.0] * (value.embedding_dimensions - 1), payload={"bootstrap_test": True})], wait=True)
        with pytest.raises(ValueError, match="nonempty"):
            initialize_empty_vector_store(value)
    finally:
        store.client.delete(store.collection, points_selector=qm.PointIdsList(points=[point_id]), wait=True)
