"""Actual PostgreSQL cutover, retry and schema probes in disposable schemas."""
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db.session import get_engine, session_scope
from tests.test_artifact_storage_migration import corpus as corpus
from tests.test_artifact_storage_migration import pipeline_env as pipeline_env
from tests.test_artifact_storage_migration import portable_cache as portable_cache


def test_postgres_resume_copy(corpus, monkeypatch):
    from tests.test_artifact_storage_migration import test_resume_copy_without_duplicates
    test_resume_copy_without_duplicates(corpus, monkeypatch)


@pytest.mark.parametrize("point", ["copy", "cutover"])
def test_postgres_commit_failure_resume(corpus, point):
    from tests.test_artifact_storage_migration import test_interrupted_sql_commit_resumes_without_partial_selection
    test_interrupted_sql_commit_resumes_without_partial_selection(corpus, point)


def test_postgres_cold_publication(pipeline_env, tmp_path, monkeypatch):
    from tests.test_artifact_storage_migration import test_paths_rebased_for_attachments_and_cold_publication
    test_paths_rebased_for_attachments_and_cold_publication(pipeline_env, tmp_path, monkeypatch, "rwx")


def test_postgres_cold_staging(corpus, monkeypatch):
    from tests.test_artifact_storage_migration import test_legacy_checkpoint_and_candidate_sidecars_survive_migration
    test_legacy_checkpoint_and_candidate_sidecars_survive_migration(corpus, monkeypatch)


def test_postgres_nested_source_cache(portable_cache, tmp_path, monkeypatch):
    from tests.test_artifact_storage_migration import test_existing_portable_cache_rewrites_nested_blob_refs
    test_existing_portable_cache_rewrites_nested_blob_refs(portable_cache, tmp_path, monkeypatch)


def test_postgres_portable_candidate_ignores_partial_cache(corpus, monkeypatch):
    from tests.test_artifact_storage_migration import test_portable_candidate_snapshot_is_not_replaced_from_partial_cache
    test_portable_candidate_snapshot_is_not_replaced_from_partial_cache(corpus, monkeypatch)


def test_postgres_schema_preserves_nonempty_corpus_and_refuses_selected_downgrade(corpus, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from app.db.models import Document
    from tests.test_artifact_storage_migration import migration, backup
    with session_scope() as session:
        schema = session.scalar(text("select current_schema()"))
    assert schema.startswith("artifact_") and len(schema) == 41
    monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=10000")
    corpus[0].database_url = get_engine().url.set(query={}).render_as_string(hide_password=False)
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl01h_storage_migration")
    command.downgrade(config, "rpl01g_artifact_gc")
    command.upgrade(config, "rpl01h_storage_migration")
    with session_scope() as session:
        assert session.query(Document).count() == 2
        assert all(row.legacy_artifacts_version == 1 for row in session.query(Document))
    task = migration(corpus)
    proof = backup(task, corpus[1])
    task.copy(apply=True, writers_stopped=True, backup=proof)
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    with pytest.raises(RuntimeError, match="receipts/selection"):
        command.downgrade(config, "rpl01g_artifact_gc")
