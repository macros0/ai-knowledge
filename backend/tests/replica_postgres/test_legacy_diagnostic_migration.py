"""Actual PostgreSQL rollback, locking and cold frozen diagnostic execution."""
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import DiagnosticBundle, DiagnosticSegment
from app.db.session import get_engine, session_scope
from app.services.artifacts.control import migration_access
from tests.support.replica_postgres import PrivatePostgresUrl
from tests.replica_postgres.test_compact_adoption import official_schema as official_schema
from tests.test_artifact_legacy_diagnostics import (
    corpus as corpus, legacy as source_legacy,
    test_diagnostic_cutover_failure_rolls_back_sql_and_resumes_original_plan as rollback_contract,
    test_second_s4_transfer_rebinds_frozen_input_and_queued_work_together as transfer_contract,
)
from tests.test_legacy_diagnostic_handoff import (
    test_frozen_worker_uses_original_cutoff_after_source_disk_is_unavailable as cold_contract,
    test_explicit_frontend_spool_survives_source_off_without_recollection as frontend_contract,
    test_late_frozen_worker_cannot_publish_after_replacement_claim as late_contract,
)


@pytest.fixture
def legacy(corpus,monkeypatch):
    value=source_legacy.__wrapped__(corpus,monkeypatch)
    value['database_url']=PrivatePostgresUrl(get_engine().url.render_as_string(hide_password=False))
    return value


def test_native_journal_cutover_rollback_and_same_plan_retry(legacy,monkeypatch):
    rollback_contract(legacy,monkeypatch)


def test_native_cold_frozen_worker(legacy,monkeypatch):
    cold_contract(legacy,monkeypatch)


def test_native_frontend_source_off(legacy,monkeypatch):
    frontend_contract(legacy,monkeypatch)


def test_native_late_frozen_worker(legacy,monkeypatch):
    late_contract(legacy,monkeypatch)


def test_native_second_frozen_transfer(legacy):
    transfer_contract(legacy)


def test_native_diagnostic_writer_blocks_cutover_then_same_plan_retries(legacy):
    from tests.test_artifact_storage_migration import migration, backup
    task=migration((legacy['source'],legacy['root']))
    proof=backup(task,legacy['root'])
    task.copy(apply=True,writers_stopped=True,backup=proof)
    with get_engine().connect() as writer:
        transaction=writer.begin()
        try:
            writer.execute(text('UPDATE diagnostic_sessions SET stop_reason=stop_reason'))
            with pytest.raises(DBAPIError) as failure:
                task.cutover(apply=True,writers_stopped=True,backup=proof)
            assert failure.value.orig.sqlstate=='55P03'
            with migration_access(),session_scope() as session:
                assert session.scalar(select(DiagnosticSegment.id)) is None
                assert session.get(DiagnosticBundle,legacy['bundle_id']).artifact_version==1
        finally:transaction.rollback()
    assert task.cutover(apply=True,writers_stopped=True,backup=proof)['cutover'] is True
