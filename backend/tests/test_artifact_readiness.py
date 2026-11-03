"""Runtime probes respect the selected storage's actual mutation scope."""
import io
import os
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


@pytest.fixture(params=["local", "rwx"])
def filesystem_store(request, tmp_path, monkeypatch):
    from app.services.artifacts.local import LocalBlobStore
    from app.services.artifacts.rwx import RwxBlobStore
    root = tmp_path / "mounted-root"
    root.mkdir()
    cls = RwxBlobStore if request.param == "rwx" else LocalBlobStore
    store = cls(root, store_id="readiness-test")
    monkeypatch.setattr("app.services.artifacts.get_blob_store", lambda _settings: store)
    return store, SimpleNamespace(artifact_backend=request.param)


def test_filesystem_readiness_accepts_working_store_without_root_write_permission(
    monkeypatch, filesystem_store,
):
    from app.services import health
    from app.services.work.runtime import probe_artifacts
    store, settings = filesystem_store
    real_access = os.access

    def access(path, mode, **kwargs):
        # Observed Synology NFS ACL: the mounted root reports W_OK false,
        # while the application's owned object/temp directories are writable.
        if path == store.root and mode & os.W_OK:
            return False
        return real_access(path, mode, **kwargs)

    monkeypatch.setattr(os, "access", access)
    payload = b"an actual immutable blob remains writable and readable"
    ref = store.put(io.BytesIO(payload), object_id="1" * 32, media_type="application/octet-stream")
    with store.open(ref) as stream:
        assert stream.read() == payload
    before = sorted(path.relative_to(store.root) for path in store.root.rglob("*"))
    assert probe_artifacts(settings) is True
    monkeypatch.setattr(health, "get_settings", lambda: settings)
    assert health._check_artifacts() == {"status": "ok"}
    assert sorted(path.relative_to(store.root) for path in store.root.rglob("*")) == before
    store.delete(ref)


@pytest.mark.parametrize("directory", ["objects", "tmp"])
def test_filesystem_readiness_rejects_unwritable_mutation_directory(
    monkeypatch, filesystem_store, directory,
):
    from app.services.work.runtime import probe_artifacts
    store, settings = filesystem_store
    real_access = os.access

    def access(path, mode, **kwargs):
        if path == store.root / directory and mode & os.W_OK:
            return False
        return real_access(path, mode, **kwargs)

    monkeypatch.setattr(os, "access", access)
    assert probe_artifacts(settings) is False
