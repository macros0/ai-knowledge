"""Typed artifact failures drive bounded retries; integrity/fences remain terminal."""
import pytest

from app.services.artifacts.contract import (
    BlobConflict, BlobIntegrityError, BlobLeaseLost, BlobNotFound, BlobUnavailable,
)
from app.services.errors import processing_error_code, public_error_code
from app.services.storage import StorageFullError


def test_unavailable_artifact_is_recoverable_without_parsing_provider_text():
    failure = BlobUnavailable("opaque provider diagnostic")
    assert public_error_code(failure) == "dependency_unavailable"
    assert processing_error_code(failure) == "processing_unavailable"


def test_wrapped_unavailable_artifact_retains_recoverable_classification():
    try:
        raise BlobUnavailable("opaque diagnostic")
    except BlobUnavailable as failure:
        try:
            raise RuntimeError("publication interrupted") from failure
        except RuntimeError as outer:
            assert processing_error_code(outer) == "processing_unavailable"


def test_real_s3_sdk_500_boundary_is_retryable_without_claiming_quota():
    from botocore.exceptions import ClientError
    from app.services.artifacts.s3 import S3BlobStore
    class Client:
        def put_object(self, **_kwargs):
            raise ClientError({"Error": {"Code": "InternalError", "Message": "opaque"},
                               "ResponseMetadata": {"HTTPStatusCode": 500}}, "PutObject")
    adapter = object.__new__(S3BlobStore)
    adapter.client, adapter.bucket = Client(), "synthetic-only"
    with pytest.raises(BlobUnavailable) as caught:
        adapter._call("put_object", Key="objects/" + "a" * 32, Body=b"test")
    assert public_error_code(caught.value) == "dependency_unavailable"
    assert processing_error_code(caught.value) == "processing_unavailable"


@pytest.mark.parametrize("kind", [BlobNotFound, BlobLeaseLost, BlobIntegrityError, BlobConflict, OSError, ValueError])
def test_missing_integrity_fence_and_untyped_failures_are_not_relabelled(kind):
    failure = kind("storage unavailable no space timeout")
    assert public_error_code(failure) == "internal_error"
    assert processing_error_code(failure) == "internal_error"


def test_known_storage_full_keeps_its_specific_code():
    failure = StorageFullError()
    assert public_error_code(failure) == processing_error_code(failure) == "storage_full"

@pytest.mark.parametrize("kind", [BlobNotFound, BlobLeaseLost, BlobIntegrityError, BlobConflict])
def test_artifact_integrity_and_fences_override_a_transient_context(kind):
    try:
        raise BlobUnavailable("transport context")
    except BlobUnavailable as transient:
        try:
            raise kind("terminal artifact failure") from transient
        except kind as failure:
            assert processing_error_code(failure) == "internal_error"
