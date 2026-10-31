"""Real PostgreSQL contention and migration in disposable per-test schemas."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import io
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.db.session import get_engine, session_scope
from app.db.models import ArtifactObject, Document
from app.services.artifacts.contract import BlobConflict
from app.services.artifacts.gc import initialize_gc, run_gc_cycle
from app.services.artifacts.lifecycle import _now
from app.services.artifacts.local import LocalBlobStore
from app.services.artifacts.repository import attach_blob
from tests.test_artifact_gc_worker import config, age_observations


def test_postgres_provider_orphan_roundtrip(tmp_path):
    from tests.test_artifact_gc_worker import test_provider_only_orphan_is_quarantined_then_deleted
    test_provider_only_orphan_is_quarantined_then_deleted(LocalBlobStore(tmp_path / "blobs", store_id="test"), tmp_path)


@pytest.mark.parametrize("winner", ["attach", "gc"])
def test_postgres_discovery_serializes_with_late_binding(tmp_path, monkeypatch, winner):
    settings = config(tmp_path)
    store = initialize_gc(settings, store=LocalBlobStore(tmp_path / "blobs", store_id="test"))
    ref = store.put(io.BytesIO(b"unregistered"), object_id="b" * 32, media_type="application/octet-stream")
    with session_scope() as session:
        session.add(Document(id="a" * 16, filename="test.pdf"))
    run_gc_cycle(settings, store=store)
    age_observations()
    reached, proceed = threading.Event(), threading.Event()
    hook = "inspect_candidate" if winner == "attach" else "delete"
    original = getattr(store, hook)
    def pause(*args, **kwargs):
        value = original(*args, **kwargs) if winner == "attach" else None
        reached.set()
        assert proceed.wait(10)
        return value if winner == "attach" else original(*args, **kwargs)
    monkeypatch.setattr(store, hook, pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        collection = pool.submit(run_gc_cycle, settings, store=store)
        try:
            assert reached.wait(10)
            with session_scope() as session:
                if winner == "gc":
                    with pytest.raises(BlobConflict):
                        attach_blob(session, ref=ref, owner_type="original", owner_id="a" * 16, generation_id=None, logical_name="original")
                else:
                    attach_blob(session, ref=ref, owner_type="original", owner_id="a" * 16, generation_id=None, logical_name="original")
        finally:
            proceed.set()
        assert collection.result(timeout=10).deleted == (1 if winner == "gc" else 0)
    with session_scope() as session:
        assert session.get(ArtifactObject, ref.object_id).state == ("deleted" if winner == "gc" else "ready")


def test_postgres_gc_lease_reclaim_and_persistent_cursor(tmp_path):
    from tests.test_artifact_gc_worker import test_gc_restarts_pagination_and_excludes_concurrent_owner
    test_gc_restarts_pagination_and_excludes_concurrent_owner(LocalBlobStore(tmp_path / "blobs", store_id="test"), tmp_path)


def test_postgres_reappeared_tombstone_one_pass_budget(tmp_path):
    from tests.test_artifact_gc_worker import test_reappeared_tombstone_can_be_collected_with_one_object_byte_budget
    test_reappeared_tombstone_can_be_collected_with_one_object_byte_budget(LocalBlobStore(tmp_path / "blobs", store_id="test"), tmp_path)


def test_postgres_gc_migration_preserves_bound_objects(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from app.db.models import ArtifactGcState
    settings = config(tmp_path)
    store = LocalBlobStore(tmp_path / "blobs", store_id="test")
    ref = store.put(io.BytesIO(b"accepted"), object_id="b" * 32, media_type="text/plain")
    with session_scope() as session:
        session.add(Document(id="a" * 16, filename="test.pdf"))
        attach_blob(session, ref=ref, owner_type="original", owner_id="a" * 16, generation_id=None, logical_name="original")
        session.get(ArtifactObject, ref.object_id).created_at = _now(session) - timedelta(days=2)
        schema = session.scalar(text("select current_schema()"))
        session.execute(text("DROP TABLE artifact_orphans"))
        session.execute(text("DROP TABLE artifact_gc_state"))
        session.execute(text("DROP INDEX ix_artifact_gc_candidates"))
        session.execute(text("DROP INDEX ix_artifact_source_caches_object_id"))
    assert schema.startswith("artifact_") and len(schema) == 41
    monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=10000")
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(db_url=get_engine().url.set(query={}).render_as_string(hide_password=False)))
    root = Path(__file__).resolve().parents[2]
    alembic = Config(str(root / "alembic.ini"))
    alembic.set_main_option("script_location", str(root / "alembic"))
    command.stamp(alembic, "rpl01f_file_consumers")
    command.upgrade(alembic, "head")
    command.downgrade(alembic, "rpl01f_file_consumers")
    command.upgrade(alembic, "head")
    initialize_gc(settings, store=store, adopt_existing=True)
    assert run_gc_cycle(settings, store=store).deleted == 0
    with session_scope() as session:
        assert session.get(ArtifactGcState, store.store_id).scope_token
    with pytest.raises(RuntimeError, match="ownership"):
        command.downgrade(alembic, "rpl01f_file_consumers")
