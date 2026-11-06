from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io

import pytest

from app.services.artifacts.contract import BlobConflict, BlobIntegrityError
from app.services.artifacts.local import LocalBlobStore
from app.services.artifacts.rwx import RwxBlobStore
from app.services.artifacts.s3 import S3BlobStore
from app.services.artifacts.materialize import materialize
from tests.support.blob_s3_fake import MemoryS3

ID = "b" * 32


@pytest.fixture(params=["local", "rwx", "s3"])
def adapter(request, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    if request.param == "local":
        return LocalBlobStore(root, store_id="test")
    if request.param == "rwx":
        return RwxBlobStore(root, store_id="test")
    return S3BlobStore(client=MemoryS3(), endpoint="https://s3.invalid", bucket="test-bucket",
                       prefix="install/", store_id="test", scratch_root=tmp_path / "scratch")


def test_roundtrip_digest_and_range(adapter, tmp_path):
    ref = adapter.put(io.BytesIO(b"original document"), object_id=ID, media_type="text/plain")
    assert adapter.stat(ref) == ref
    with adapter.open(ref, start=1, end=4) as source:
        assert source.read(999) == b"rig"
        assert source.read(1) == b""
    with materialize(ref, store=adapter, scratch_root=tmp_path / "parser") as path:
        assert path.read_bytes() == b"original document"
    assert not path.exists()


def test_same_id_different_bytes_conflicts(adapter):
    ref = adapter.put(io.BytesIO(b"original"), object_id=ID, media_type="text/plain")
    assert adapter.put(io.BytesIO(b"original"), object_id=ID, media_type="text/plain") == ref
    with pytest.raises(BlobConflict):
        adapter.put(io.BytesIO(b"replacement"), object_id=ID, media_type="text/plain")
    with adapter.open(ref) as source:
        assert source.read() == b"original"


def test_concurrent_immutable_publication(adapter):
    def write(value):
        try:
            return adapter.put(io.BytesIO(value), object_id=ID, media_type="text/plain")
        except BlobConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        refs = list(pool.map(write, [b"one", b"two"]))
    assert sum(ref is not None for ref in refs) == 1


def test_interrupted_source_never_publishes(adapter):
    class Interrupted(io.BytesIO):
        def read(self, size=-1):
            raise OSError("Source interrupted")
    with pytest.raises(OSError):
        adapter.put(Interrupted(b"source"), object_id=ID, media_type="text/plain")
    ref = adapter.put(io.BytesIO(b"retry"), object_id=ID, media_type="text/plain")
    adapter.delete(ref)
    adapter.delete(ref)


def test_foreign_store_and_bad_ranges_rejected(adapter):
    ref = adapter.put(io.BytesIO(b"value"), object_id=ID, media_type="text/plain")
    with pytest.raises(ValueError):
        adapter.stat(replace(ref, store_id="foreign"))
    with pytest.raises(ValueError):
        with adapter.open(ref, start=1, end=999):
            pytest.fail("Invalid range yielded")
    with pytest.raises(BlobIntegrityError):
        adapter.stat(replace(ref, sha256="0" * 64))
