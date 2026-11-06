from datetime import timedelta
import io
import time

import pytest
from sqlalchemy import select

from app.db.session import session_scope
from tests.test_artifact_gc import orphan as orphan


def test_read_wrapper_protects_open_and_releases_after_consumer_failure(orphan):
    from app.db.models import ArtifactReadLease
    from app.services.artifacts.lifecycle import collect_blob
    from app.services.artifacts.leased import LeasedBlobStore
    store, ref = orphan
    with pytest.raises(RuntimeError, match="consumer"):
        with LeasedBlobStore(store).open(ref) as stream:
            assert not collect_blob(store, ref.object_id, grace_seconds=0)
            assert stream.read(9) == b"protected"
            raise RuntimeError("consumer")
    with session_scope() as session:
        assert not session.scalars(select(ArtifactReadLease)).all()
    assert collect_blob(store, ref.object_id, grace_seconds=0)


def test_lost_lease_stops_reader_instead_of_serving_pinned_bytes(orphan):
    from app.db.models import ArtifactReadLease
    from app.services.artifacts.contract import BlobLeaseLost
    from app.services.artifacts.leased import LeasedBlobStore
    from app.services.artifacts.lifecycle import _now
    store, ref = orphan
    with LeasedBlobStore(store, ttl_seconds=1, renew_interval=.05).open(ref) as stream:
        with session_scope() as session:
            row = session.scalar(select(ArtifactReadLease))
            row.expires_at = _now(session) - timedelta(seconds=5)
        for _ in range(30):
            try:
                stream.read(0)
            except BlobLeaseLost:
                break
            time.sleep(.03)
        else:
            pytest.fail("Reader continued after losing its lease")


@pytest.mark.parametrize("provider", ["local", "s3"])
def test_guard_interrupts_provider_verification_before_yield(tmp_path, provider):
    from app.services.artifacts.contract import BlobLeaseLost
    from app.services.artifacts.local import LocalBlobStore
    from app.services.artifacts.s3 import S3BlobStore
    from tests.support.blob_s3_fake import MemoryS3
    store = LocalBlobStore(tmp_path / "blobs", store_id="local") if provider == "local" else S3BlobStore(
        client=MemoryS3(), endpoint="https://s3.invalid", bucket="test-bucket", prefix="", store_id="local", scratch_root=tmp_path)
    ref = store.put(io.BytesIO(b"x" * 200_000), object_id="c" * 32, media_type="text/plain")
    count = 0
    def guard():
        nonlocal count
        count += 1
        if count >= 3:
            raise BlobLeaseLost("lost")
    with pytest.raises(BlobLeaseLost):
        with store.open(ref, guard=guard):
            pytest.fail("Lost lease yielded verified data")
