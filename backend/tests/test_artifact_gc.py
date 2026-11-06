"""Reference/lease protection and retryable deletion share one SQL boundary."""
from datetime import timedelta
import io

import pytest

from app.db.models import ArtifactBinding, ArtifactObject, Document
from app.db.session import session_scope
from app.services.artifacts.contract import BlobConflict, BlobUnavailable
from app.services.artifacts.local import LocalBlobStore
from app.services.artifacts.repository import attach_blob

DOC = "a" * 16


@pytest.fixture
def orphan(tmp_path):
    store = LocalBlobStore(tmp_path / "blobs", store_id="local")
    ref = store.put(io.BytesIO(b"protected original"), object_id="b" * 32, media_type="text/plain")
    with session_scope() as session:
        session.add(Document(id=DOC, filename="a.pdf"))
        attach_blob(session, ref=ref, owner_type="original", owner_id=DOC, generation_id=None, logical_name="original")
        session.query(ArtifactBinding).delete()
    return store, ref


def test_gc_skips_bound_or_leased_blob(orphan):
    from app.services.artifacts.lifecycle import acquire_read_lease, release_read_lease, collect_blob
    store, ref = orphan
    with session_scope() as session:
        attach_blob(session, ref=ref, owner_type="original", owner_id=DOC, generation_id=None, logical_name="original")
    assert not collect_blob(store, ref.object_id, grace_seconds=0)
    with session_scope() as session:
        session.query(ArtifactBinding).delete()
        token = acquire_read_lease(session, ref, ttl_seconds=60)
    assert not collect_blob(store, ref.object_id, grace_seconds=0)
    with session_scope() as session:
        release_read_lease(session, ref.object_id, token)
    assert collect_blob(store, ref.object_id, grace_seconds=0)
    assert not (store.root / ref.key).exists()


def test_attach_races_gc(orphan):
    from app.services.artifacts.lifecycle import claim_delete, acquire_read_lease
    store, ref = orphan
    with session_scope() as session:
        claim = claim_delete(session, ref.object_id, grace_seconds=0)
        assert claim.ref == ref
    with session_scope() as session:
        with pytest.raises(BlobConflict):
            attach_blob(session, ref=ref, owner_type="original", owner_id=DOC, generation_id=None, logical_name="original")
    with session_scope() as session:
        with pytest.raises(BlobUnavailable):
            acquire_read_lease(session, ref)
    assert (store.root / ref.key).exists()


def test_gc_failure_retains_tombstone_and_retries(orphan, monkeypatch):
    from app.services.artifacts.lifecycle import collect_blob
    from app.services.artifacts.lifecycle import _now
    store, ref = orphan
    original = store.delete
    monkeypatch.setattr(store, "delete", lambda ref: (_ for _ in ()).throw(BlobUnavailable("offline")))
    with pytest.raises(BlobUnavailable):
        collect_blob(store, ref.object_id, grace_seconds=0)
    with session_scope() as session:
        row = session.get(ArtifactObject, ref.object_id)
        assert row.state == "deleting"
        row.delete_expires_at = _now(session) - timedelta(seconds=5)
    monkeypatch.setattr(store, "delete", original)
    assert collect_blob(store, ref.object_id, grace_seconds=0)
    with session_scope() as session:
        assert session.get(ArtifactObject, ref.object_id).state == "deleted"
        with pytest.raises(BlobConflict):
            attach_blob(session, ref=ref, owner_type="original", owner_id=DOC, generation_id=None, logical_name="original")


def test_stale_gc_receipt_cannot_finish_new_claim(orphan):
    from app.services.artifacts.lifecycle import claim_delete, finish_delete, _now
    _, ref = orphan
    with session_scope() as session:
        old = claim_delete(session, ref.object_id, grace_seconds=0)
        session.get(ArtifactObject, ref.object_id).delete_expires_at = _now(session) - timedelta(seconds=5)
    with session_scope() as session:
        new = claim_delete(session, ref.object_id, grace_seconds=0)
        assert new.token != old.token
        assert not finish_delete(session, old)
        assert finish_delete(session, new)


def test_gc_has_a_default_orphan_grace_period(orphan):
    from app.services.artifacts.lifecycle import collect_blob
    store, ref = orphan
    assert not collect_blob(store, ref.object_id)
    assert (store.root / ref.key).exists()


def test_purge_detaches_references_but_cannot_delete_active_reader(orphan):
    from app.services.artifacts.leased import LeasedBlobStore
    from app.services.artifacts.lifecycle import collect_blob
    from app.services.registry import get_registry
    store, ref = orphan
    with session_scope() as session:
        attach_blob(session, ref=ref, owner_type="original", owner_id=DOC, generation_id=None, logical_name="original")
    with LeasedBlobStore(store).open(ref) as stream:
        assert get_registry().delete(DOC)
        assert not collect_blob(store, ref.object_id, grace_seconds=0)
        assert stream.read() == b"protected original"
    assert collect_blob(store, ref.object_id, grace_seconds=0)
