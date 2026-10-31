"""Actual SQL locks/JSON/head migrations in an explicitly disposable database."""
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

from app.db.session import session_scope, get_engine
from tests.test_portable_source_cache import portable_cache as portable_cache
from tests.test_processing_scope_change_end_to_end import source_environment as source_environment
from tests.test_portable_job_files import portable_exports as portable_exports
from tests.test_portable_review_files import portable_review as portable_review
from tests.test_artifact_gc import orphan as orphan
from tests.test_generation_pipeline import pipeline_env as pipeline_env


def test_postgres_source_cache_cold_roundtrip(portable_cache, tmp_path):
    from tests.test_portable_source_cache import test_source_cache_survives_worker_disk_loss
    test_source_cache_survives_worker_disk_loss(portable_cache, tmp_path)


def test_postgres_scope_restore_after_worker_loss(source_environment, monkeypatch, tmp_path):
    from tests.test_portable_source_cache import test_scope_exclusion_and_restore_reuse_cold_source_cache
    test_scope_exclusion_and_restore_reuse_cold_source_cache(source_environment, monkeypatch, tmp_path)


def test_postgres_export_from_cold_bound_original(portable_exports, tmp_path):
    from tests.test_portable_job_files import test_queued_export_builds_from_bound_original_on_cold_worker
    test_queued_export_builds_from_bound_original_on_cold_worker(portable_exports, tmp_path)


def test_postgres_review_original_after_worker_loss(portable_review, tmp_path):
    from tests.test_portable_review_files import test_pending_review_original_is_retained_on_another_worker
    test_pending_review_original_is_retained_on_another_worker(portable_review, tmp_path)


def test_postgres_export_source_pin_survives_document_purge(portable_exports):
    from tests.test_portable_job_files import test_queued_export_pins_original_until_job_finishes_or_fails
    test_queued_export_pins_original_until_job_finishes_or_fails(portable_exports)


def test_postgres_export_blocks_schema_downgrade(portable_exports, monkeypatch):
    from tests.test_portable_job_files import test_consumer_schema_downgrade_refuses_ready_portable_export
    test_consumer_schema_downgrade_refuses_ready_portable_export(portable_exports, monkeypatch)


def test_postgres_cold_table_sidecar_import(pipeline_env, tmp_path):
    from tests.test_portable_file_consumers import test_table_import_restores_approved_jsonl_on_cold_worker
    test_table_import_restores_approved_jsonl_on_cold_worker(pipeline_env, tmp_path)


def test_postgres_consumer_migrations_preserve_legacy_rows(orphan, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from app.db.models import Document
    with session_scope() as session:
        schema = session.scalar(text("select current_schema()"))
        session.execute(text("DROP TABLE artifact_orphans"))
        session.execute(text("DROP TABLE artifact_gc_state"))
        session.execute(text("DROP INDEX ix_artifact_gc_candidates"))
        session.execute(text("DROP INDEX ix_artifact_source_caches_object_id"))
        session.execute(text("DROP TABLE artifact_source_caches"))
        session.execute(text("ALTER TABLE documents DROP COLUMN source_cache_version"))
        session.execute(text("ALTER TABLE diagnostic_bundles DROP COLUMN artifact_version"))
        session.execute(text("ALTER TABLE document_processing_reviews DROP COLUMN artifact_version"))
        session.execute(text("ALTER TABLE document_processing_reviews DROP COLUMN artifact_revision"))
    assert schema.startswith("artifact_") and len(schema) == 41
    monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=10000")
    url = get_engine().url.set(query={}).render_as_string(hide_password=False)
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(db_url=url))
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl01d_portable_staging")
    command.upgrade(config, "head")
    with session_scope() as session:
        document = session.query(Document).one()
        assert document.source_cache_version == 1
        assert document.filename == "a.pdf"
    command.downgrade(config, "rpl01d_portable_staging")
    command.upgrade(config, "head")
    with session_scope() as session:
        assert session.query(Document).one().source_cache_version == 1
