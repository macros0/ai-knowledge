"""Real PostgreSQL compact adoption: official schema, exclusion and rollback.

Filesystem and backup inputs here are synthetic S4 fixtures, not CSI or
independent recovery qualification. The operator must never release on adopt.
"""
from pathlib import Path
import json
import re
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.models import ArtifactStorageControl, AuditLog, MaintenanceState, ReplicaRuntimeIdentity
from app.db.session import get_engine
from app.services.work.maintenance_control import MaintenanceOperator, MaintenanceRefused, assert_writes_allowed, leave_maintenance
from tests.support.replica_postgres import PrivatePostgresUrl
from tests.test_replica_compact_adoption import (
    COUNTS, WRITERS, adopt, corpus as corpus, prepared as prepared, source_snapshot,
    test_real_s4_cutover_legacy_nulls_queue_config_and_auth_remain_held as preserved_contract,
)


@pytest.fixture(autouse=True)
def official_schema(_db):
    engine = get_engine()
    with engine.begin() as connection:
        schema = connection.scalar(text('SELECT current_schema()'))
        assert re.fullmatch(r'artifact_[0-9a-f]{32}', schema)
        # Replace only this fixture's fresh disposable schema; run actual
        # migrations rather than accepting create_all as schema evidence.
        connection.execute(DropSchema(schema, cascade=True))
        connection.execute(CreateSchema(schema))
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / 'alembic.ini'))
        config.set_main_option('script_location', str(root / 'alembic'))
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == 'rpl05_maintenance_epoch'


@pytest.fixture
def native(prepared):
    settings, receipt, task = prepared
    settings.database_url = PrivatePostgresUrl(get_engine().url.render_as_string(hide_password=False))
    return settings, receipt, task


def operator(settings, operation=None):
    return MaintenanceOperator(get_engine(), operation_id=operation or uuid4().hex,
        owner_id='native-compact', settings=settings)


def assert_uninstalled():
    with get_engine().connect() as connection:
        assert connection.scalar(select(ReplicaRuntimeIdentity.id)) is None
        assert connection.scalar(select(MaintenanceState.id)) is None
        assert connection.scalar(select(ArtifactStorageControl.maintenance_token)) is None


def test_pg_compact_adoption_preserves_nonempty_source_and_held_retry(native):
    preserved_contract(native)


def test_pg_compact_held_epoch_retires_legacy_writers_after_explicit_test_release(native):
    settings, receipt, _ = native
    snapshot = source_snapshot(settings)
    with operator(settings) as op:
        adopt(op, settings, snapshot, receipt)
        with Session(get_engine()) as session, pytest.raises(MaintenanceRefused, match='active'):
            assert_writes_allowed(session, 1, require_installed=True)
        # Only an explicit test coordinator releases; adoption never does.
        with op.transaction() as session:
            assert leave_maintenance(session, op.operation_id, writer_receipts=WRITERS) == 1
    with Session(get_engine()) as session:
        assert assert_writes_allowed(session, 1, require_installed=True) == 1
    for retired in (None, 0):
        with Session(get_engine()) as session, pytest.raises(MaintenanceRefused, match='retired'):
            assert_writes_allowed(session, retired, require_installed=True)


def test_pg_compact_advisory_operator_excludes_second_owner(native):
    settings, receipt, _ = native
    snapshot = source_snapshot(settings)
    with operator(settings) as op:
        with pytest.raises(MaintenanceRefused, match='already active'), operator(settings):
            pytest.fail('A second operator acquired the same source')
        adopt(op, settings, snapshot, receipt)


def test_pg_compact_waits_for_existing_sql_writer_then_can_retry(native):
    settings, receipt, _ = native
    snapshot = source_snapshot(settings)
    with get_engine().connect() as writer, operator(settings) as op:
        transaction = writer.begin()
        try:
            # A real UPDATE takes RowExclusive; SELECT FOR UPDATE alone would
            # not conflict with the adoption's SHARE ROW EXCLUSIVE table lock.
            writer.execute(text('UPDATE documents SET filename=filename'))
            with pytest.raises(DBAPIError) as failure:
                adopt(op, settings, snapshot, receipt)
            assert failure.value.orig.sqlstate == '55P03'
            assert_uninstalled()
        finally:
            transaction.rollback()
        adopt(op, settings, snapshot, receipt)
        with op.transaction() as session:
            assert session.get(MaintenanceState, 1).enabled is True


def test_pg_compact_audit_failure_rolls_back_identity_and_hold(native, monkeypatch):
    settings, receipt, _ = native
    snapshot = source_snapshot(settings)
    def unavailable(*_args, **_kwargs):
        raise RuntimeError('synthetic audit failure')
    with monkeypatch.context() as patch:
        patch.setattr('app.services.audit.record_in_session', unavailable)
        with operator(settings) as op, pytest.raises(RuntimeError, match='synthetic audit failure'):
            adopt(op, settings, snapshot, receipt)
    assert_uninstalled()
    assert source_snapshot(settings) == snapshot
    with operator(settings) as op:
        adopt(op, settings, snapshot, receipt)


def test_pg_compact_rejects_committed_source_write_after_snapshot(native):
    settings, receipt, _ = native
    snapshot = source_snapshot(settings)
    with get_engine().begin() as connection:
        connection.execute(text("UPDATE documents SET filename='changed.docx'"))
    with operator(settings) as op, pytest.raises(MaintenanceRefused, match='differs'):
        adopt(op, settings, snapshot, receipt)
    assert_uninstalled()


@pytest.mark.parametrize('publication_failure', [False, True])
def test_pg_compact_cli_uses_literal_target_and_resumes_held_publication(native, tmp_path, monkeypatch, capsys, publication_failure):
    from tests.test_replica_compact_adoption_cli import load, result
    settings, receipt, _ = native
    tool = load()
    runtime, source, receipts, counts, writers = [tmp_path / name for name in (
        'native-runtime.env', 'native-source.json', 'native-receipt.json', 'native-counts.json', 'native-writers.json')]
    values = {'ENVIRONMENT':'development', 'APP_SECRET_KEY':settings.app_secret_key,
        'DATABASE_URL':settings.db_url, 'DATA_DIR':str(settings.data_dir), 'DIAGNOSTICS_DIR':str(settings.diagnostics_dir),
        'OKF_DEPLOYMENT_SCHEMA':'replicas-v1', 'DEPLOYMENT_MODE':'distributed', 'APP_ROLE':'api',
        'DEPLOYMENT_NAMESPACE':settings.deployment_namespace, 'ARTIFACT_BACKEND':'rwx',
        'ARTIFACT_ROOT':str(settings.artifact_root), 'ARTIFACT_STORE_ID':settings.artifact_store_id,
        'ARTIFACT_UPLOADS_ENABLED':'true', 'RUNTIME_IDENTITY_FILE':str(settings.runtime_identity_file),
        'RUNTIME_IMAGE_ID':settings.runtime_image_id, 'FRONTEND_REPLICAS':'2', 'API_REPLICAS':'2', 'WORKER_REPLICAS':'2'}
    runtime.write_text('\n'.join(key+'='+json.dumps(value) for key,value in values.items()))
    operation = uuid4().hex
    args = ['--runtime-env-file',str(runtime),'--source-snapshot',str(source),
        '--operation-id',operation,'--owner-id','native-cli']
    # Actual loader/operator/database selection, including search_path. The
    # isolated server has no TLS: this is not provider transport qualification.
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://foreign:private-canary@production/production')
    assert tool.main(['--phase','inspect',*args]) == 0
    assert result(capsys)['snapshot_written'] is True
    assert json.loads(source.read_bytes()) == source_snapshot(settings)
    for path, value in ((receipts,receipt),(counts,COUNTS),(writers,WRITERS)):
        path.write_text(json.dumps(value))
    args += ['--storage-receipt',str(receipts),'--counts',str(counts),'--writer-receipts',str(writers)]
    original = tool.write_identity
    if publication_failure:
        def unavailable(*_args):
            raise OSError('synthetic private-canary publication failure')
        monkeypatch.setattr(tool, 'write_identity', unavailable)
        assert tool.main(['--phase','adopt','--apply',*args]) == 2
        failure = result(capsys)
        assert failure['jobs_held'] is True and 'private-canary' not in json.dumps(failure)
        assert not settings.runtime_identity_file.exists()
        monkeypatch.setattr(tool, 'write_identity', original)
    for _ in range(2):
        assert tool.main(['--phase','adopt','--apply',*args]) == 0
        report = result(capsys)
        assert report['jobs_held'] is True and report['workloads_started'] is False
        assert json.loads(settings.runtime_identity_file.read_bytes()) == report['identity']
    with get_engine().connect() as connection:
        assert connection.scalar(select(MaintenanceState.enabled)) is True
        assert connection.scalar(select(text('count(*)')).select_from(AuditLog)) == 2
