import io

import pytest

from app.services.artifacts.local import LocalBlobStore
from app.services.artifacts.materialize import materialize
from app.services.artifacts.contract import BlobIntegrityError


def test_materialize_cleans_after_error(tmp_path):
    store = LocalBlobStore(tmp_path / "store", store_id="test")
    ref = store.put(io.BytesIO(b"source document"), object_id="a" * 32, media_type="text/plain")
    with pytest.raises(RuntimeError):
        with materialize(ref, store=store, scratch_root=tmp_path / "scratch") as path:
            assert path.read_bytes() == b"source document"
            saved_path = path
            raise RuntimeError("Parser failed")
    assert not saved_path.exists()
    assert list((tmp_path / "scratch").iterdir()) == []


def test_materialize_rechecks_provider_bytes_and_cleans_partial_copy(tmp_path):
    store = LocalBlobStore(tmp_path / "store", store_id="test")
    ref = store.put(io.BytesIO(b"safe"), object_id="a" * 32, media_type="text/plain")
    class CorruptProvider:
        def open(self, ref):
            return io.BytesIO(b"evil")
    with pytest.raises(BlobIntegrityError):
        with materialize(ref, store=CorruptProvider(), scratch_root=tmp_path / "scratch"):
            pytest.fail("Corrupt materialization yielded")
    assert list((tmp_path / "scratch").iterdir()) == []
