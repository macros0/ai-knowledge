from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import io

import pytest

from app.services.artifacts.contract import BlobConflict, BlobIntegrityError, BlobUnavailable
from app.services.artifacts.local import LocalBlobStore
from app.services.storage import StorageFullError

OBJECT_ID = "a" * 32


def test_roundtrip_digest_and_range(tmp_path):
    store = LocalBlobStore(tmp_path / "objects", store_id="test")
    value = b"0123456789"
    ref = store.put(io.BytesIO(value), object_id=OBJECT_ID, media_type="text/plain")
    assert ref.sha256 == hashlib.sha256(value).hexdigest()
    assert ref.size == len(value)
    assert store.stat(ref) == ref
    with store.open(ref, start=2, end=5) as stream:
        assert stream.read(100) == b"234"
        assert stream.read(1) == b""
    store.delete(ref)
    store.delete(ref)


def test_same_id_is_immutable_under_concurrent_writers(tmp_path):
    store = LocalBlobStore(tmp_path, store_id="test")
    def write(value):
        try:
            return store.put(io.BytesIO(value), object_id=OBJECT_ID, media_type="text/plain")
        except BlobConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        refs = list(pool.map(write, [b"first", b"second"]))
    assert sum(ref is not None for ref in refs) == 1
    ref = next(ref for ref in refs if ref)
    with store.open(ref) as stream:
        original = stream.read()
    assert store.put(io.BytesIO(original), object_id=OBJECT_ID, media_type="text/plain") == ref
    assert not list((tmp_path / "tmp").iterdir())


def test_interrupted_put_not_visible(tmp_path):
    store = LocalBlobStore(tmp_path, store_id="test")
    class Interrupted(io.BytesIO):
        def read(self, size=-1):
            if self.tell():
                raise OSError("Read interrupted")
            return super().read(1)
    with pytest.raises(OSError):
        store.put(Interrupted(b"value"), object_id=OBJECT_ID, media_type="text/plain")
    assert not (tmp_path / "objects" / OBJECT_ID).exists()
    assert not list((tmp_path / "tmp").iterdir())


@pytest.mark.parametrize("changes", [
    {"key": "../outside"}, {"object_id": "../outside"}, {"store_id": "other"},
])
def test_no_path_escape_or_foreign_store(tmp_path, changes):
    store = LocalBlobStore(tmp_path, store_id="test")
    ref = store.put(io.BytesIO(b"safe"), object_id=OBJECT_ID, media_type="text/plain")
    with pytest.raises(ValueError):
        store.stat(replace(ref, **changes))


def test_corruption_rejected_before_read(tmp_path):
    store = LocalBlobStore(tmp_path, store_id="test")
    ref = store.put(io.BytesIO(b"safe"), object_id=OBJECT_ID, media_type="text/plain")
    (tmp_path / ref.key).write_bytes(b"evil")
    with pytest.raises(BlobIntegrityError):
        with store.open(ref):
            pytest.fail("corrupt read yielded")


def test_link_cannot_redirect_artifact_read(tmp_path):
    store = LocalBlobStore(tmp_path / "store", store_id="test")
    ref = store.put(io.BytesIO(b"safe"), object_id=OBJECT_ID, media_type="text/plain")
    external = tmp_path / "outside"
    external.write_bytes(b"safe")
    target = store.root / ref.key
    target.unlink()
    try:
        target.symlink_to(external)
    except OSError:
        pytest.skip("Platform does not permit symlink creation")
    with pytest.raises(ValueError):
        store.stat(ref)
    assert external.read_bytes() == b"safe"


@pytest.mark.parametrize("start,end", [(-1, 1), (2, 1), (0, 9), (True, 2)])
def test_invalid_range_rejected(tmp_path, start, end):
    store = LocalBlobStore(tmp_path, store_id="test")
    ref = store.put(io.BytesIO(b"safe"), object_id=OBJECT_ID, media_type="text/plain")
    with pytest.raises(ValueError):
        with store.open(ref, start=start, end=end):
            pytest.fail("invalid range yielded")


def test_read_io_failure_is_typed_without_path(tmp_path, monkeypatch):
    store = LocalBlobStore(tmp_path, store_id="test")
    ref = store.put(io.BytesIO(b"safe"), object_id=OBJECT_ID, media_type="text/plain")
    def failed_digest(handle):
        raise OSError("private-path")
    monkeypatch.setattr(store, "_digest", failed_digest)
    with pytest.raises(BlobUnavailable) as error:
        store.stat(ref)
    assert "private-path" not in str(error.value)


def test_delete_io_failure_is_typed(tmp_path, monkeypatch):
    store = LocalBlobStore(tmp_path, store_id="test")
    ref = store.put(io.BytesIO(b"safe"), object_id=OBJECT_ID, media_type="text/plain")
    from pathlib import Path
    original = Path.unlink
    def forbidden(path, *args, **kwargs):
        if path.name == OBJECT_ID:
            raise PermissionError("private-path")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", forbidden)
    with pytest.raises(BlobUnavailable):
        store.delete(ref)


def test_cleanup_failure_preserves_primary_storage_full(tmp_path, monkeypatch):
    import errno
    from pathlib import Path
    store = LocalBlobStore(tmp_path, store_id="test")
    original = Path.unlink
    def forbidden(path, *args, **kwargs):
        if path.parent.name == "tmp":
            raise PermissionError("private-path")
        return original(path, *args, **kwargs)
    class Full(io.BytesIO):
        def read(self, size=-1):
            raise OSError(errno.ENOSPC, "disk full")
    monkeypatch.setattr(Path, "unlink", forbidden)
    with pytest.raises(StorageFullError):
        store.put(Full(b"data"), object_id=OBJECT_ID, media_type="text/plain")


def test_consumer_error_is_not_reinterpreted_as_storage_failure(tmp_path):
    store = LocalBlobStore(tmp_path, store_id="test")
    ref = store.put(io.BytesIO(b"safe"), object_id=OBJECT_ID, media_type="text/plain")
    original = OSError("Parser-specific error")
    with pytest.raises(OSError) as error:
        with store.open(ref):
            raise original
    assert error.value is original
