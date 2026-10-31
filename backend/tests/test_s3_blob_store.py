import io
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from botocore.config import Config
from botocore.response import StreamingBody
from botocore.stub import Stubber, ANY
import boto3
import hashlib
from pathlib import Path

from app.services.artifacts.contract import BlobUnavailable, BlobIntegrityError
from app.services.artifacts.s3 import S3BlobStore
from app.services.artifacts.rwx import RwxBlobStore
from tests.support.blob_s3_fake import MemoryS3


def store(tmp_path, client=None, **kwargs):
    return S3BlobStore(client=client or MemoryS3(), endpoint="https://s3.invalid", bucket="test-bucket",
                       prefix="install/", store_id="test", scratch_root=tmp_path, **kwargs)


def test_multipart_completion_is_conditional_and_cleanup_runs(tmp_path):
    client = MemoryS3()
    adapter = store(tmp_path, client)
    value = b"x" * (8 * 1024 * 1024 + 1)
    ref = adapter.put(io.BytesIO(value), object_id="b" * 32, media_type="text/plain")
    assert "complete" in client.calls
    assert client.uploads == {}
    with adapter.open(ref) as source:
        assert source.read() == value


def test_failed_part_aborts_upload_and_hides_provider_error(tmp_path):
    client = MemoryS3()
    def failed(**kwargs):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "private-credential"}}, "UploadPart")
    client.upload_part = failed
    with pytest.raises(BlobUnavailable) as error:
        store(tmp_path, client).put(io.BytesIO(b"x" * (8 * 1024 * 1024 + 1)), object_id="b" * 32, media_type="text/plain")
    assert "private-credential" not in str(error.value)
    assert client.aborted and client.uploads == {} and client.objects == {}


def test_get_corruption_never_yields(tmp_path):
    client = MemoryS3()
    adapter = store(tmp_path, client)
    ref = adapter.put(io.BytesIO(b"safe"), object_id="b" * 32, media_type="text/plain")
    client.objects["install/" + ref.key] = b"evil"
    with pytest.raises(BlobIntegrityError):
        with adapter.open(ref):
            pytest.fail("Corrupt remote object yielded")


def test_request_endpoint_cannot_redirect_credentials(tmp_path):
    adapter = store(tmp_path)
    with pytest.raises(BlobUnavailable):
        adapter.check_endpoint(SimpleNamespace(url="https://other.invalid/install/object"))
    assert adapter.check_endpoint(SimpleNamespace(url="https://s3.invalid/install/object")) is None


def test_rwx_requires_existing_root(tmp_path):
    with pytest.raises(ValueError):
        RwxBlobStore(tmp_path / "not-mounted", store_id="test")
    assert not (tmp_path / "not-mounted").exists()


def test_actual_sdk_model_sends_conditional_put_and_verifies_body(tmp_path):
    client = boto3.client("s3", endpoint_url="https://s3.invalid", region_name="us-east-1",
                          aws_access_key_id="test", aws_secret_access_key="test",
                          config=Config(s3={"addressing_style": "path"}))
    value = b"source"
    digest = hashlib.sha256(value).hexdigest()
    key = "install/objects/" + "b" * 32
    with Stubber(client) as stub:
        stub.add_response("put_object", {}, {"Bucket": "test-bucket", "Key": key,
            "Body": ANY, "IfNoneMatch": "*", "ContentLength": len(value),
            "ContentType": "text/plain", "Metadata": {"sha256": digest}})
        stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(value), len(value)),
                          "ContentLength": len(value)}, {"Bucket": "test-bucket", "Key": key})
        ref = store(tmp_path, client).put(io.BytesIO(value), object_id="b" * 32, media_type="text/plain")
        assert ref.sha256 == digest
        stub.assert_no_pending_responses()


def test_actual_sdk_multipart_model_accepts_if_none_match(tmp_path):
    client = boto3.client("s3", endpoint_url="https://s3.invalid", region_name="us-east-1",
                          aws_access_key_id="test", aws_secret_access_key="test")
    value = b"x" * (8 * 1024 * 1024 + 1)
    digest = hashlib.sha256(value).hexdigest()
    key = "install/objects/" + "b" * 32
    with Stubber(client) as stub:
        stub.add_response("create_multipart_upload", {"UploadId": "upload-id"},
                          {"Bucket": "test-bucket", "Key": key, "ContentType": "text/plain", "Metadata": {"sha256": digest}})
        for number in [1, 2]:
            stub.add_response("upload_part", {"ETag": str(number)}, {"Bucket": "test-bucket",
                "Key": key, "UploadId": "upload-id", "PartNumber": number, "Body": ANY})
        stub.add_response("complete_multipart_upload", {}, {"Bucket": "test-bucket", "Key": key,
            "UploadId": "upload-id", "IfNoneMatch": "*", "MultipartUpload": {"Parts": [
                {"PartNumber": 1, "ETag": "1"}, {"PartNumber": 2, "ETag": "2"}]}})
        stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(value), len(value)),
                          "ContentLength": len(value)}, {"Bucket": "test-bucket", "Key": key})
        assert store(tmp_path, client).put(io.BytesIO(value), object_id="b" * 32, media_type="text/plain").size == len(value)
        stub.assert_no_pending_responses()


def test_before_send_hook_blocks_changed_authority_in_real_sdk(tmp_path):
    client = boto3.client("s3", endpoint_url="https://s3.invalid", region_name="us-east-1",
                          aws_access_key_id="test", aws_secret_access_key="test")
    store(tmp_path, client)
    with pytest.raises(BlobUnavailable):
        client.meta.events.emit("before-send.s3.GetObject", request=SimpleNamespace(url="https://evil.invalid/key"))


@pytest.mark.parametrize("field,value", [
    ("endpoint", "https://credential:secret@s3.invalid"),
    ("endpoint", "http://s3.invalid"), ("endpoint", "https://s3.invalid/path"),
    ("prefix", "../escape/"), ("bucket", "../escape"),
])
def test_invalid_endpoint_or_namespace_rejected_before_io(tmp_path, field, value):
    kwargs = dict(client=MemoryS3(), endpoint="https://s3.invalid", bucket="test-bucket",
                  prefix="install/", store_id="test", scratch_root=tmp_path)
    kwargs[field] = value
    with pytest.raises(ValueError):
        S3BlobStore(**kwargs)


def test_missing_bucket_is_not_successful_idempotent_delete(tmp_path):
    client = MemoryS3()
    adapter = store(tmp_path, client)
    ref = adapter.put(io.BytesIO(b"safe"), object_id="b" * 32, media_type="text/plain")
    def missing_bucket(**kwargs):
        raise ClientError({"Error": {"Code": "NoSuchBucket"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "GetObject")
    client.get_object = missing_bucket
    with pytest.raises(BlobUnavailable):
        adapter.delete(ref)
    assert client.objects


def test_s3_owned_seek_and_close_failures_are_typed(tmp_path, monkeypatch):
    adapter = store(tmp_path)
    ref = adapter.put(io.BytesIO(b"safe"), object_id="b" * 32, media_type="text/plain")
    class FailedSpool:
        def seek(self, offset):
            raise PermissionError("private-path")
        def close(self):
            raise PermissionError("another-private-path")
    monkeypatch.setattr(adapter, "_verified_spool", lambda ref: FailedSpool())
    with pytest.raises(BlobUnavailable) as error:
        with adapter.open(ref):
            pytest.fail("Failed scratch yielded")
    assert "private-path" not in str(error.value)


def test_s3_close_failure_preserves_consumer_exception(tmp_path, monkeypatch):
    adapter = store(tmp_path)
    ref = adapter.put(io.BytesIO(b"safe"), object_id="b" * 32, media_type="text/plain")
    class FailedClose(io.BytesIO):
        def close(self):
            raise PermissionError("private-path")
    monkeypatch.setattr(adapter, "_verified_spool", lambda ref: FailedClose(b"safe"))
    original = RuntimeError("Consumer failed")
    with pytest.raises(RuntimeError) as error:
        with adapter.open(ref):
            raise original
    assert error.value is original


def test_rwx_inspection_io_failure_is_typed(tmp_path, monkeypatch):
    original = Path.is_dir
    def failed(path):
        if path == tmp_path:
            raise PermissionError("private-path")
        return original(path)
    monkeypatch.setattr(Path, "is_dir", failed)
    with pytest.raises(BlobUnavailable):
        RwxBlobStore(tmp_path, store_id="test")
