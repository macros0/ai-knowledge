"""Discover committed provider-only bytes, quarantine, then safely collect."""
from datetime import timedelta
import io
import threading

import pytest

from app.config import Settings
from app.db.models import ArtifactBinding, ArtifactObject, Document
from app.db.session import session_scope
from app.services.artifacts.contract import BlobConflict, BlobUnavailable
from app.services.artifacts.lifecycle import _now, acquire_read_lease
from app.services.artifacts.repository import attach_blob
from tests.test_shared_blob_contract import adapter as adapter


def config(tmp_path, **kwargs):
    return Settings(_env_file=None, data_dir=tmp_path, artifact_gc_enabled=True, **kwargs)


def age_observations():
    from app.db.models import ArtifactOrphan
    with session_scope() as session:
        for row in session.query(ArtifactOrphan):
            row.first_seen_at = _now(session) - timedelta(days=2)


def test_provider_only_orphan_is_quarantined_then_deleted(adapter, tmp_path):
    from app.services.artifacts.gc import initialize_gc, run_gc_cycle
    settings = config(tmp_path)
    initialize_gc(settings, store=adapter)
    ref = adapter.put(io.BytesIO(b"failed before SQL commit"), object_id="b" * 32, media_type="text/plain")
    first = run_gc_cycle(settings, store=adapter)
    assert first.observed == 1 and first.deleted == 0
    assert adapter.stat(ref) == ref
    age_observations()
    result = run_gc_cycle(settings, store=adapter)
    assert result.registered == 1 and result.deleted == 1
    with pytest.raises(BlobUnavailable):
        adapter.stat(ref)
    with session_scope() as session:
        assert session.get(ArtifactObject, ref.object_id).state == "deleted"


def test_late_binding_and_read_lease_win_against_automatic_gc(adapter, tmp_path):
    from app.services.artifacts.gc import initialize_gc, run_gc_cycle
    initialize_gc(config(tmp_path), store=adapter)
    ref = adapter.put(io.BytesIO(b"accepted after discovery"), object_id="b" * 32, media_type="text/plain")
    run_gc_cycle(config(tmp_path), store=adapter)
    age_observations()
    with session_scope() as session:
        session.add(Document(id="a" * 16, filename="test.pdf"))
        attach_blob(session, ref=ref, owner_type="original", owner_id="a" * 16, generation_id=None, logical_name="original")
        session.get(ArtifactObject, ref.object_id).created_at = _now(session) - timedelta(days=2)
    assert run_gc_cycle(config(tmp_path), store=adapter).deleted == 0
    with session_scope() as session:
        session.query(ArtifactBinding).delete()
        acquire_read_lease(session, ref)
    assert run_gc_cycle(config(tmp_path), store=adapter).deleted == 0
    assert adapter.stat(ref) == ref


def test_wrong_scope_never_deletes_and_failed_provider_retries(adapter, tmp_path, monkeypatch):
    from app.services.artifacts.gc import initialize_gc, run_gc_cycle
    settings = config(tmp_path)
    initialize_gc(settings, store=adapter)
    ref = adapter.put(io.BytesIO(b"orphan"), object_id="b" * 32, media_type="text/plain")
    run_gc_cycle(settings, store=adapter)
    age_observations()
    original = adapter.delete
    monkeypatch.setattr(adapter, "delete", lambda *_a, **_kw: (_ for _ in ()).throw(BlobUnavailable("offline")))
    assert run_gc_cycle(settings, store=adapter).failures == 1
    with session_scope() as session:
        row = session.get(ArtifactObject, ref.object_id)
        assert row.state == "deleting"
        row.delete_expires_at = _now(session) - timedelta(seconds=1)
    monkeypatch.setattr(adapter, "delete", original)
    assert run_gc_cycle(settings, store=adapter).deleted == 1
    # A different database's ownership token must not replace the provider marker.
    from app.db.models import ArtifactGcState
    with session_scope() as session:
        session.get(ArtifactGcState, adapter.store_id).scope_token = "f" * 32
    with pytest.raises(BlobConflict):
        run_gc_cycle(settings, store=adapter)


def test_gc_default_off_and_pre_stopped_worker_never_builds_provider(tmp_path, monkeypatch):
    from app.services.artifacts.gc import run_gc_cycle, run_gc_loop
    monkeypatch.setattr("app.services.artifacts.get_blob_store", lambda *_a: pytest.fail("unexpected provider access"))
    assert run_gc_cycle(Settings(_env_file=None, data_dir=tmp_path)).deleted == 0
    stop = threading.Event()
    stop.set()
    run_gc_loop(stop, config(tmp_path))


def test_gc_config_refuses_short_grace_and_production(tmp_path):
    with pytest.raises(ValueError):
        config(tmp_path, artifact_gc_grace_seconds=1)
    with pytest.raises(ValueError):
        config(tmp_path, environment="production")


def test_gc_restarts_pagination_and_excludes_concurrent_owner(adapter, tmp_path):
    from app.db.models import ArtifactGcState, ArtifactOrphan
    from app.services.artifacts.gc import initialize_gc, run_gc_cycle
    settings = config(tmp_path, artifact_gc_batch_size=1)
    initialize_gc(settings, store=adapter)
    refs = [adapter.put(io.BytesIO(b"x"), object_id=f"{i:032x}", media_type="text/plain") for i in range(3)]
    for _ in range(3):
        run_gc_cycle(settings, store=adapter)
    with session_scope() as session:
        assert session.query(ArtifactOrphan).count() == 3
        state = session.get(ArtifactGcState, adapter.store_id)
        state.owner_token, state.lease_expires_at = "f" * 32, _now(session) + timedelta(seconds=120)
    assert run_gc_cycle(settings, store=adapter).busy
    age_observations()
    with session_scope() as session:
        session.get(ArtifactGcState, adapter.store_id).lease_expires_at = _now(session) - timedelta(seconds=1)
    for _ in range(6):
        run_gc_cycle(settings, store=adapter)
    with session_scope() as session:
        assert session.query(ArtifactObject).filter_by(state="deleted").count() == len(refs)


def test_changed_provider_object_restarts_quarantine(adapter, tmp_path):
    from app.services.artifacts.gc import initialize_gc, run_gc_cycle
    settings = config(tmp_path)
    initialize_gc(settings, store=adapter)
    ref = adapter.put(io.BytesIO(b"old"), object_id="b" * 32, media_type="text/plain")
    run_gc_cycle(settings, store=adapter)
    age_observations()
    if hasattr(adapter, "root"):
        (adapter.root / ref.key).write_bytes(b"new bytes")
    else:
        adapter.client.objects[adapter.prefix + ref.key] = b"new bytes"
    result = run_gc_cycle(settings, store=adapter)
    assert result.deleted == 0 and result.registered == 0
    assert adapter.inventory_page(limit=1).items[0].size == 9


def test_gc_lost_owner_guard_stops_before_delete(adapter, tmp_path, monkeypatch):
    from app.db.models import ArtifactGcState
    from app.services.artifacts.contract import BlobLeaseLost
    from app.services.artifacts.gc import initialize_gc, run_gc_cycle
    settings = config(tmp_path)
    initialize_gc(settings, store=adapter)
    ref = adapter.put(io.BytesIO(b"orphan"), object_id="b" * 32, media_type="text/plain")
    run_gc_cycle(settings, store=adapter)
    age_observations()
    inspect = adapter.inspect_candidate
    def lose_owner(*args, **kwargs):
        value = inspect(*args, **kwargs)
        with session_scope() as session:
            session.get(ArtifactGcState, adapter.store_id).owner_token = "e" * 32
        return value
    monkeypatch.setattr(adapter, "inspect_candidate", lose_owner)
    with pytest.raises(BlobLeaseLost):
        run_gc_cycle(settings, store=adapter)
    assert adapter.stat(ref) == ref


def test_deleted_id_reappearing_after_late_put_is_collected_without_reviving_it(adapter, tmp_path):
    from app.services.artifacts.gc import initialize_gc, run_gc_cycle
    settings = config(tmp_path)
    initialize_gc(settings, store=adapter)
    ref = adapter.put(io.BytesIO(b"orphan"), object_id="b" * 32, media_type="text/plain")
    run_gc_cycle(settings, store=adapter)
    age_observations()
    assert run_gc_cycle(settings, store=adapter).deleted == 1
    adapter.put(io.BytesIO(b"orphan"), object_id=ref.object_id, media_type=ref.media_type)
    assert run_gc_cycle(settings, store=adapter).deleted == 0
    age_observations()
    assert run_gc_cycle(settings, store=adapter).deleted == 1
    with session_scope() as session:
        assert session.get(ArtifactObject, ref.object_id).state == "deleted"


def test_inspection_budget_defers_large_orphans(adapter, tmp_path):
    from app.services.artifacts.gc import initialize_gc, run_gc_cycle
    settings = config(tmp_path, artifact_gc_verify_bytes=1)
    initialize_gc(settings, store=adapter)
    ref = adapter.put(io.BytesIO(b"large"), object_id="b" * 32, media_type="text/plain")
    run_gc_cycle(settings, store=adapter)
    age_observations()
    result = run_gc_cycle(settings, store=adapter)
    assert result.budget_skipped == 1 and result.deleted == 0
    assert adapter.stat(ref) == ref


def test_reappeared_tombstone_can_be_collected_with_one_object_byte_budget(adapter, tmp_path):
    from app.services.artifacts.gc import initialize_gc, run_gc_cycle
    settings = config(tmp_path, artifact_gc_verify_bytes=6)
    initialize_gc(settings, store=adapter)
    ref = adapter.put(io.BytesIO(b"orphan"), object_id="b" * 32, media_type="text/plain")
    run_gc_cycle(settings, store=adapter)
    age_observations()
    run_gc_cycle(settings, store=adapter)  # Inspection/registration consumes this cycle's budget.
    assert run_gc_cycle(settings, store=adapter).deleted == 1
    adapter.put(io.BytesIO(b"orphan"), object_id=ref.object_id, media_type=ref.media_type)
    run_gc_cycle(settings, store=adapter)
    age_observations()
    assert run_gc_cycle(settings, store=adapter).deleted == 1


def test_real_background_worker_collects_and_stops(tmp_path, monkeypatch):
    from app.services.artifacts.gc import initialize_gc, start_gc_if_enabled
    from app.services.artifacts.local import LocalBlobStore
    from app.services.artifacts.repository import register_blob
    settings = config(tmp_path)
    store = initialize_gc(settings, store=LocalBlobStore(tmp_path / "blobs", store_id="test"))
    ref = store.put(io.BytesIO(b"unbound metadata"), object_id="b" * 32, media_type="text/plain")
    with session_scope() as session:
        register_blob(session, ref).created_at = _now(session) - timedelta(days=2)
    deleted = threading.Event()
    original = store.delete
    def collect(*args, **kwargs):
        original(*args, **kwargs)
        deleted.set()
    monkeypatch.setattr(store, "delete", collect)
    monkeypatch.setattr("app.services.artifacts.get_blob_store", lambda *_a: store)
    worker = start_gc_if_enabled(settings)
    try:
        assert deleted.wait(10)
    finally:
        worker.stop()
    assert not worker.thread.is_alive()


def test_reconfigured_store_location_is_rejected(tmp_path):
    from app.services.artifacts.gc import initialize_gc
    from app.services.artifacts.local import LocalBlobStore
    settings = config(tmp_path)
    initialize_gc(settings, store=LocalBlobStore(tmp_path / "first", store_id="same"))
    with pytest.raises(BlobConflict, match="location"):
        initialize_gc(settings, store=LocalBlobStore(tmp_path / "second", store_id="same"))


def test_source_cache_head_protects_bytes_even_if_member_binding_is_lost(tmp_path):
    from app.db.models import SourceCacheHead
    from app.services.artifacts.gc import initialize_gc, run_gc_cycle
    from app.services.artifacts.local import LocalBlobStore
    from app.services.artifacts.repository import register_blob
    settings = config(tmp_path)
    store = initialize_gc(settings, store=LocalBlobStore(tmp_path / "blobs", store_id="test"))
    ref = store.put(io.BytesIO(b"cache manifest"), object_id="b" * 32, media_type="text/plain")
    with session_scope() as session:
        session.add(Document(id="a" * 16, filename="test.pdf"))
        register_blob(session, ref).created_at = _now(session) - timedelta(days=2)
        session.add(SourceCacheHead(doc_id="a" * 16, generation_id="", cache_id="c" * 64,
            source_key="d" * 64, content_fingerprint="e" * 64, object_id=ref.object_id))
    from app.services.artifacts.lifecycle import collect_blob
    assert not collect_blob(store, ref.object_id, grace_seconds=0)
    assert run_gc_cycle(settings, store=store).deleted == 0
    assert store.stat(ref) == ref
