"""Runtime probes respect the application's selected S3 prefix permissions."""
from types import SimpleNamespace

import boto3
from botocore.stub import Stubber
import pytest


@pytest.fixture
def scoped_store(monkeypatch):
    client = boto3.client("s3", endpoint_url="https://s3.example.test",
        aws_access_key_id="test-access", aws_secret_access_key="test-secret", region_name="us-east-1")
    store = SimpleNamespace(client=client, bucket="shared-bucket", prefix="installation/owned/")
    monkeypatch.setattr("app.services.artifacts.get_blob_store", lambda _settings: store)
    with Stubber(client) as stubber:
        yield store, stubber
        stubber.assert_no_pending_responses()


@pytest.mark.parametrize("contents", [[], [{"Key": "installation/owned/objects/retained", "Size": 1}]])
def test_readiness_accepts_prefix_scoped_access_without_bucket_wide_permission(scoped_store, contents):
    from app.services.work.runtime import probe_artifacts
    store, stubber = scoped_store
    # HEAD bucket requires unconditioned ListBucket; a prefix-only principal
    # cannot perform it. The SDK must send a bounded request within its scope.
    stubber.add_response("list_objects_v2", {"Contents": contents},
        {"Bucket": store.bucket, "Prefix": store.prefix, "MaxKeys": 1})
    assert probe_artifacts(SimpleNamespace(artifact_backend="s3")) is True


def test_api_readiness_reports_available_prefix_scoped_storage(monkeypatch, scoped_store):
    from app.services import health
    store, stubber = scoped_store
    stubber.add_response("list_objects_v2", {},
        {"Bucket": store.bucket, "Prefix": store.prefix, "MaxKeys": 1})
    monkeypatch.setattr(health, "get_settings", lambda: SimpleNamespace(artifact_backend="s3"))
    assert health._check_artifacts() == {"status": "ok"}


def test_api_readiness_keeps_denied_prefix_unavailable(monkeypatch, scoped_store):
    from app.services import health
    store, stubber = scoped_store
    stubber.add_client_error("list_objects_v2", service_error_code="AccessDenied", http_status_code=403,
        expected_params={"Bucket": store.bucket, "Prefix": store.prefix, "MaxKeys": 1})
    monkeypatch.setattr(health, "get_settings", lambda: SimpleNamespace(artifact_backend="s3"))
    assert health._check_artifacts() == {"status": "down", "error": "artifact storage unavailable"}
