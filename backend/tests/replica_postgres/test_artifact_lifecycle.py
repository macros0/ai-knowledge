"""Reuse contract controls and exercise actual row-lock ordering across connections."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from pathlib import Path

import pytest

from app.db.session import session_scope
from app.config import get_settings
from app.services.artifacts.contract import BlobConflict
from app.services.artifacts.lifecycle import claim_delete, collect_blob
from app.services.artifacts.repository import attach_blob
from tests.test_artifact_gc import DOC, orphan as orphan
from tests.test_generation_pipeline import pipeline_env as pipeline_env
from tests.test_portable_staging import portable as portable
from tests.test_artifact_gc import (
    test_gc_skips_bound_or_leased_blob as test_gc_skips_bound_or_leased_blob,
    test_attach_races_gc as test_attach_races_gc,
    test_gc_failure_retains_tombstone_and_retries as test_gc_failure_retains_tombstone_and_retries,
    test_stale_gc_receipt_cannot_finish_new_claim as test_stale_gc_receipt_cannot_finish_new_claim,
    test_gc_has_a_default_orphan_grace_period as test_gc_has_a_default_orphan_grace_period,
    test_purge_detaches_references_but_cannot_delete_active_reader as test_purge_detaches_references_but_cannot_delete_active_reader,
)
from tests.test_artifact_read_lease import (
    test_read_wrapper_protects_open_and_releases_after_consumer_failure as test_read_wrapper_protects_open_and_releases_after_consumer_failure,
    test_lost_lease_stops_reader_instead_of_serving_pinned_bytes as test_lost_lease_stops_reader_instead_of_serving_pinned_bytes,
)
from tests.test_artifact_generation import (
    test_checkpoint_survives_worker_disk_loss as test_checkpoint_survives_worker_disk_loss,
    test_checkpoint_cas_and_abandoned_generation_reject_late_writer as test_checkpoint_cas_and_abandoned_generation_reject_late_writer,
    test_checkpoint_reference_blocks_orphan_gc as test_checkpoint_reference_blocks_orphan_gc,
)


@pytest.mark.parametrize("winner", ["attach", "gc"])
def test_real_postgres_lock_orders_attach_against_gc(orphan, winner):
    store, ref = orphan
    locked = threading.Event()
    competing = threading.Event()
    commit = threading.Event()
    scope = dict(ref=ref, owner_type="original", owner_id=DOC, generation_id=None, logical_name="original")
    def first():
        with session_scope() as session:
            if winner == "attach":
                attach_blob(session, **scope)
            else:
                assert claim_delete(session, ref.object_id, grace_seconds=0)
            locked.set()
            assert commit.wait(5)
    def second():
        competing.set()
        if winner == "attach":
            return collect_blob(store, ref.object_id, grace_seconds=0)
        with session_scope() as session:
            with pytest.raises(BlobConflict):
                attach_blob(session, **scope)
        return "rejected"
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(first)
        try:
            assert locked.wait(5)
            collector = pool.submit(second)
            assert competing.wait(5)
            time.sleep(.1)
            assert not collector.done(), "Competing transaction bypassed the object lock"
        finally:
            commit.set()
        writer.result(timeout=5)
        assert collector.result(timeout=5) == (False if winner == "attach" else "rejected")
    assert (store.root / ref.key).exists()


def test_actual_postgres_lifecycle_migration_preserves_bound_object(orphan, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from app.db.session import get_engine
    from app.db.models import ArtifactObject
    _, ref = orphan
    with session_scope() as session:
        attach_blob(session, ref=ref, owner_type="original", owner_id=DOC, generation_id=None, logical_name="original")
        schema = session.scalar(text("select current_schema()"))
    assert schema.startswith("artifact_") and len(schema) == 41
    monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=10000")
    # Keep Alembic/configparser free of percent-encoded libpq options.
    url = get_engine().url.set(query={}).render_as_string(hide_password=False)
    alembic_settings = get_settings().model_copy(update={"database_url": url})
    monkeypatch.setattr("app.config.get_settings", lambda: alembic_settings)
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl01b_artifact_lifecycle")
    command.downgrade(config, "rpl01_artifact_objects")
    command.upgrade(config, "rpl01b_artifact_lifecycle")
    with session_scope() as session:
        assert session.get(ArtifactObject, ref.object_id).state == "ready"
        assert not collect_blob(orphan[0], ref.object_id, grace_seconds=0)


def test_postgres_portable_publication_survives_cold_worker(pipeline_env, monkeypatch, tmp_path):
    from tests.test_portable_publication import test_candidate_publishes_after_worker_disk_loss
    test_candidate_publishes_after_worker_disk_loss(pipeline_env, monkeypatch, tmp_path, provider="local")


def test_postgres_staging_survives_cold_worker(portable, tmp_path):
    from tests.test_portable_staging import test_staging_checkpoint_survives_worker_disk_loss
    test_staging_checkpoint_survives_worker_disk_loss(portable, tmp_path)


def test_postgres_pipeline_resumes_without_checkpoint_disk(pipeline_env, monkeypatch, tmp_path):
    from tests.test_portable_staging import test_pipeline_resume_reuses_durable_checkpoint_after_scratch_loss
    test_pipeline_resume_reuses_durable_checkpoint_after_scratch_loss(pipeline_env, monkeypatch, tmp_path)


def test_postgres_checkpoint_commit_rejects_stale_pointer(portable):
    from tests.test_portable_staging import test_stale_checkpoint_commit_cannot_replace_new_result
    test_stale_checkpoint_commit_cannot_replace_new_result(portable)


def test_postgres_staging_marker_migration_preserves_legacy_and_refuses_portable_downgrade(orphan, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from app.db.models import DocumentStaging
    from app.db.session import get_engine
    with session_scope() as session:
        session.add(DocumentStaging(doc_id=DOC, chunks_data={"0": {"file": "chunk_00.json"}}, processed_chunks=[0]))
        schema = session.scalar(text("select current_schema()"))
    assert schema.startswith("artifact_") and len(schema) == 41
    monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=10000")
    url = get_engine().url.set(query={}).render_as_string(hide_password=False)
    alembic_settings = get_settings().model_copy(update={"database_url": url})
    monkeypatch.setattr("app.config.get_settings", lambda: alembic_settings)
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl01d_portable_staging")
    command.downgrade(config, "rpl01c_portable_publication")
    command.upgrade(config, "rpl01d_portable_staging")
    with session_scope() as session:
        row = session.get(DocumentStaging, DOC)
        assert row.checkpoint_version == 1 and row.chunks_data == {"0": {"file": "chunk_00.json"}}
        row.checkpoint_version = 2
    with pytest.raises(RuntimeError, match="portable staging"):
        command.downgrade(config, "rpl01c_portable_publication")
    with session_scope() as session:
        assert session.get(DocumentStaging, DOC).checkpoint_version == 2


def test_postgres_publication_marker_upgrade_preserves_legacy_and_sealed_rows(orphan, monkeypatch):
    import io
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from app.db.session import get_engine
    from app.db.models import DocumentGeneration
    from app.services.generation_store import begin_generation
    store, _ = orphan
    ref = store.put(io.BytesIO(b"{}"), object_id="e" * 32, media_type="application/json")
    with session_scope() as session:
        generation = begin_generation(session, DOC)
        sealed_id = generation.id
        attach_blob(session, ref=ref, owner_type="generation", owner_id=DOC,
                    generation_id=sealed_id, logical_name="publication.json")
        generation.publication_version = 2
        session.add(DocumentGeneration(id="f" * 32, doc_id=DOC, phase="retired"))
        schema = session.scalar(text("select current_schema()"))
    assert schema.startswith("artifact_") and len(schema) == 41
    monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=10000")
    url = get_engine().url.set(query={}).render_as_string(hide_password=False)
    alembic_settings = get_settings().model_copy(update={"database_url": url})
    monkeypatch.setattr("app.config.get_settings", lambda: alembic_settings)
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl01c_portable_publication")
    command.downgrade(config, "rpl01b_artifact_lifecycle")
    command.upgrade(config, "rpl01c_portable_publication")
    with session_scope() as session:
        assert session.get(DocumentGeneration, sealed_id).publication_version == 2
        assert session.get(DocumentGeneration, "f" * 32).publication_version == 1
