"""A predecessor import must acquire a new held restore identity after rpl05."""
import hashlib
import importlib.util
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.deployment.replica_backup import BackupPlan, canonical
from app.db.models import MaintenanceState, ArtifactStorageControl
from app.db.session import get_engine
from app.services.work.maintenance_control import MaintenanceOperator
from tests.test_replica_predecessor_backup import previous_plan
from tests.test_replica_restore_identity import prepare


def load():
    assert importlib.util.find_spec('app.deployment.predecessor_restore') is not None, 'Predecessor restore rebinding missing'
    from app.deployment.predecessor_restore import rebind_predecessor_target
    return rebind_predecessor_target


def recovery_plan(connection, fingerprint, *, installation_id=None, namespace=None, backup_id=None):
    value, _ = previous_plan()
    if installation_id is not None:
        value['source']['installation_id'] = installation_id
    if namespace is not None:
        value['source']['namespace'] = namespace
    if backup_id is not None:
        value['backup_id'] = value['source']['operation_id'] = backup_id
        value['source']['stop_proof']['artifact_fence_operation_id'] = backup_id
    value['model_vector_sha256'] = hashlib.sha256(canonical(fingerprint)).hexdigest()
    value['counts'] = {key: connection.scalar(text('SELECT count(*) FROM ' + table)) for key, table in {
        'documents': 'documents', 'concepts': 'okf_concepts', 'chunks': 'document_chunks',
        'work_items': 'work_items', 'audit_events': 'audit_log'}.items()}
    value['counts']['blobs'] = connection.scalar(text("SELECT count(*) FROM artifact_objects WHERE state='ready'"))
    value['inventory'] = [entry for entry in value['inventory'] if entry['component'] != 'artifacts']
    proof = value['source']['stop_proof']
    proof['runtime_fingerprint_sha256'] = value['model_vector_sha256']
    scope = {key: value['source'][key] for key in ('installation_id', 'namespace', 'operation_id')}
    scope.update(namespace_uid=proof['namespace_uid'], schema_revision=value['schema_revision'],
        runtime_fingerprint_sha256=value['model_vector_sha256'])
    proof['source_scope_sha256'] = hashlib.sha256(canonical(scope)).hexdigest()
    return value


@pytest.fixture
def restored(tmp_path):
    settings, _ = prepare(tmp_path)
    with get_engine().begin() as conn:
        conn.execute(MaintenanceState.__table__.update().values(epoch=1, operation_id='0' * 32,
            operation_kind='upgrade', owner_id='schema-migration', intended_counts_json={}, writer_receipts_json={}))
        conn.execute(ArtifactStorageControl.__table__.update().values(maintenance_token='0' * 32))
        conn.execute(text("INSERT INTO work_items(id,kind,payload_version,payload_json,dedupe_key) VALUES(:id,'probe',1,'{}','retained')"), {'id': '4' * 32})
        value = recovery_plan(conn, '1' * 64)
        namespace = value['source']['namespace']
        conn.execute(ArtifactStorageControl.__table__.update().values(active_descriptor={
            'backend': 's3', 'store_id': settings.artifact_store_id, 'bucket': namespace,
            'prefix': f"replica_test/{namespace.removeprefix('replica-test-')}/application/",
            'endpoint': f'https://source-artifacts.{namespace}.svc:9000'}))
    return settings, BackupPlan.model_validate(value)


def apply(settings, plan):
    operation = uuid4().hex
    with MaintenanceOperator(get_engine(), operation_id=operation, owner_id='predecessor-restore', settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            result = load()(session, settings, plan, installation_id='5' * 32,
                operation_id=operation, selection_digest='sha256:' + '7' * 64)
        with op.transaction() as session:
            assert load()(session, settings, plan, installation_id='5' * 32,
                operation_id=operation, selection_digest='sha256:' + '7' * 64)[0] == result[0]
    return operation, result


def test_predecessor_restore_revokes_auth_and_keeps_restored_work_unexecuted(restored):
    settings, plan = restored
    operation, (document, counts) = apply(settings, plan)
    assert counts['invalidated_sessions'] == 1
    assert document['installation_id'] == '5' * 32
    with get_engine().connect() as conn:
        assert conn.execute(text('SELECT epoch,enabled,operation_id,operation_kind FROM replica_maintenance_state')).one() == (2, 1, operation, 'restore')
        assert conn.scalar(text('SELECT count(*) FROM auth_sessions')) == 0
        assert conn.scalar(text('SELECT count(*) FROM audit_log')) == 1
        assert conn.execute(text('SELECT status,maintenance_epoch,attempts FROM work_items')).one() == ('queued', 0, 0)


@pytest.mark.parametrize('failure', ['not_migrated', 'foreign_operation', 'source_fingerprint', 'running_work',
    'unknown_count', 'changed_installation', 'wrong_storage', 'changed_fence',
    'source_backend', 'source_bucket', 'source_prefix'])
def test_predecessor_restore_refuses_uncertain_import_before_auth_cleanup(restored, failure):
    settings, plan = restored
    with get_engine().begin() as conn:
        if failure == 'not_migrated':
            conn.execute(text("UPDATE alembic_version SET version_num='rpl04_runtime_identity'"))
        elif failure in {'foreign_operation', 'changed_fence'}:
            conn.execute(MaintenanceState.__table__.update().values(**(
                {'operation_id': '9' * 32} if failure == 'foreign_operation' else {'epoch': 7})))
        elif failure == 'source_fingerprint':
            conn.execute(text("UPDATE replica_runtime_identity SET fingerprint=:fp"), {'fp': '9' * 64})
        elif failure == 'running_work':
            conn.execute(text("UPDATE work_items SET status='running',owner_id='unknown'"))
        elif failure == 'unknown_count':
            conn.execute(text("DELETE FROM work_items"))
        elif failure == 'changed_installation':
            conn.execute(text("UPDATE artifact_storage_control SET installation_id=:id"), {'id': '9' * 32})
        elif failure in {'source_backend', 'source_bucket', 'source_prefix'}:
            import json
            source = conn.scalar(text('SELECT active_descriptor FROM artifact_storage_control'))
            source = json.loads(source) if isinstance(source, str) else source
            source[{'source_backend': 'backend', 'source_bucket': 'bucket', 'source_prefix': 'prefix'}[failure]] = 'foreign'
            conn.execute(ArtifactStorageControl.__table__.update().values(active_descriptor=source))
        else:
            settings.artifact_store_id = 'foreign-store'
    with pytest.raises(ValueError):
        apply(settings, plan)
    with get_engine().connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM auth_sessions')) == 1
        assert conn.scalar(text('SELECT count(*) FROM audit_log')) == 0


def test_predecessor_restore_audit_failure_rolls_back_identity_and_auth(restored, monkeypatch):
    settings, plan = restored
    def fail(*_args, **_kwargs):
        raise RuntimeError('audit unavailable')
    monkeypatch.setattr('app.services.audit.record_in_session', fail)
    with pytest.raises(RuntimeError, match='audit unavailable'):
        apply(settings, plan)
    with get_engine().connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM auth_sessions')) == 1
        assert conn.scalar(text('SELECT fingerprint FROM replica_runtime_identity')) == '1' * 64
        assert conn.scalar(text('SELECT epoch FROM replica_maintenance_state')) == 1
