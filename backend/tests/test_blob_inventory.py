"""Maintenance inventory is scoped, bounded and never trusts provider digests."""
import io
import hashlib
from datetime import datetime, timezone

import pytest

from app.services.artifacts.contract import BlobConflict, BlobIntegrityError
from tests.test_shared_blob_contract import adapter as adapter


def test_inventory_pages_only_canonical_objects_and_hashes_bytes(adapter):
    refs = [adapter.put(io.BytesIO(str(i).encode()), object_id=f"{i:032x}", media_type="text/plain") for i in range(3)]
    page = adapter.inventory_page(limit=2)
    assert [item.object_id for item in page.items] == [ref.object_id for ref in refs[:2]]
    assert page.cursor is not None
    last = adapter.inventory_page(cursor=page.cursor, limit=2)
    assert [item.object_id for item in last.items] == [refs[2].object_id]
    assert last.cursor is None
    discovered = adapter.inspect_candidate(page.items[0], max_bytes=100)
    assert (discovered.sha256, discovered.size) == (refs[0].sha256, 1)


def test_gc_scope_refuses_existing_unmarked_store_and_foreign_database(adapter):
    adapter.put(io.BytesIO(b"orphan"), object_id="b" * 32, media_type="text/plain")
    with pytest.raises(BlobConflict):
        adapter.gc_scope("a" * 32, initialize=True)
    adapter.gc_scope("a" * 32, initialize=True, adopt_existing=True)
    adapter.gc_scope("a" * 32)
    with pytest.raises(BlobConflict):
        adapter.gc_scope("b" * 32)


def test_inventory_inspection_rejects_changed_bytes_and_size_budget(adapter):
    ref = adapter.put(io.BytesIO(b"orphan"), object_id="b" * 32, media_type="text/plain")
    candidate = adapter.inventory_page(limit=2).items[0]
    with pytest.raises(ValueError):
        adapter.inspect_candidate(candidate, max_bytes=1)
    if hasattr(adapter, "root"):
        (adapter.root / ref.key).write_bytes(b"changed")
    else:
        adapter.client.objects[adapter.prefix + ref.key] = b"changed"
    with pytest.raises(BlobIntegrityError):
        adapter.inspect_candidate(candidate, max_bytes=100)


def test_real_sdk_inventory_prefix_pagination_and_streamed_digest(tmp_path):
    import boto3
    from botocore.stub import Stubber
    from botocore.response import StreamingBody
    from tests.test_s3_blob_store import store
    client = boto3.client("s3", endpoint_url="https://s3.invalid", region_name="us-east-1",
                          aws_access_key_id="test", aws_secret_access_key="test")
    adapter = store(tmp_path, client)
    value = b"actual bytes, not provider metadata"
    key = "install/objects/" + "b" * 32
    version = {"ETag": '"opaque-etag"', "LastModified": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    with Stubber(client) as stub:
        stub.add_response("list_objects_v2", {"Contents": [{"Key": key, "Size": len(value), **version}],
            "IsTruncated": True, "NextContinuationToken": "opaque-token"},
            {"Bucket": "test-bucket", "Prefix": "install/objects/", "MaxKeys": 1})
        stub.add_response("list_objects_v2", {"Contents": [], "IsTruncated": False},
            {"Bucket": "test-bucket", "Prefix": "install/objects/", "MaxKeys": 1, "ContinuationToken": "opaque-token"})
        stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(value), len(value)),
            "ContentLength": len(value), "Metadata": {"sha256": "0" * 64}, **version},
            {"Bucket": "test-bucket", "Key": key})
        page = adapter.inventory_page(limit=1)
        assert page.cursor == "opaque-token"
        assert adapter.inventory_page(limit=1, cursor=page.cursor).cursor is None
        assert adapter.inspect_candidate(page.items[0], max_bytes=100).sha256 == hashlib.sha256(value).hexdigest()
        stub.assert_no_pending_responses()
