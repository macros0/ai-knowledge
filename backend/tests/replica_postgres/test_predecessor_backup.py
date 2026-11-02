"""Real predecessor metadata and restoration through the actual forward DDL."""
import hashlib

import pytest
from sqlalchemy import text
from uuid import uuid4

from app.db.models import ArtifactStorageControl
from app.deployment.replica_backup import BackupPlan, canonical
from app.services.work.maintenance_control import MaintenanceOperator
from tests.replica_postgres.test_schema_adoption import (predecessor as predecessor,
    bootstrap_settings as bootstrap_settings)
from tests.test_replica_predecessor_restore import recovery_plan
from tests.test_replica_predecessor_stop import load
from tests.test_replica_backup_coordinator import config


def source_config(engine):
    value = config()
    run = engine.url.database.removeprefix('replica_test_')
    value.update(run_id=run, database=engine.url.database, collection='replica_test_' + run,
        namespace='replica-test-' + run, bucket='replica-test-' + run,
        prefix='replica_test/' + run + '/application/', installation_id='1' * 32)
    return value


def source_descriptor(value):
    return {'backend': 's3', 'store_id': value['store_id'], 'bucket': value['bucket'],
        'prefix': value['prefix'],
        'endpoint': f"https://{value['storage_release']}-artifacts.{value['namespace']}.svc:9000"}


def test_pg_predecessor_source_guard_reads_actual_old_schema(predecessor):
    engine, _config, _settings, old = predecessor
    value = source_config(engine)
    with engine.begin() as conn:
        conn.execute(ArtifactStorageControl.__table__.update().values(active_store_id=value['store_id'],
            active_descriptor=source_descriptor(value)))
    with engine.connect() as conn:
        identity, control = load().predecessor_sql_source(conn, value, old['runtime_fingerprint'])
        assert identity['schema_revision'] == 'rpl04_runtime_identity'
        assert control['maintenance_token'] is None
        assert conn.scalar(text('SELECT count(*) FROM documents')) == 1


@pytest.mark.parametrize('failure', ['running_work', 'changed_schema'])
def test_pg_predecessor_guard_refuses_real_uncertain_source(predecessor, failure):
    engine, _config, _settings, old = predecessor
    value = source_config(engine)
    with engine.begin() as conn:
        conn.execute(ArtifactStorageControl.__table__.update().values(active_store_id=value['store_id'],
            active_descriptor=source_descriptor(value)))
        if failure == 'running_work':
            conn.execute(text("UPDATE work_items SET status='running',owner_id='unknown'"))
        else:
            conn.execute(text("UPDATE alembic_version SET version_num='rpl05_maintenance_epoch'"))
    with engine.connect() as conn, pytest.raises(ValueError):
        load().predecessor_sql_source(conn, value, old['runtime_fingerprint'])


def test_pg_predecessor_recovery_after_real_forward_migration(predecessor):
    from app.deployment.predecessor_restore import rebind_predecessor_target
    engine, config, settings, old = predecessor
    with engine.begin() as conn:
        value = recovery_plan(conn, old['runtime_fingerprint'])
        value['source']['installation_id'] = old['installation_id']
        proof = value['source']['stop_proof']
        scope = {key: value['source'][key] for key in ('installation_id', 'namespace', 'operation_id')}
        scope.update(namespace_uid=proof['namespace_uid'], schema_revision=value['schema_revision'],
            runtime_fingerprint_sha256=value['model_vector_sha256'])
        proof['source_scope_sha256'] = hashlib.sha256(canonical(scope)).hexdigest()
        source_ns = value['source']['namespace']
        conn.execute(ArtifactStorageControl.__table__.update().values(active_descriptor={
            'backend': 's3', 'store_id': settings.artifact_store_id, 'bucket': source_ns,
            'prefix': f"replica_test/{source_ns.removeprefix('replica-test-')}/application/",
            'endpoint': f'https://source-artifacts.{source_ns}.svc:9000'}))
    settings.database_url = engine.url.render_as_string(hide_password=False)
    settings.deployment_namespace = 'replica-test-' + engine.url.database.removeprefix('replica_test_')
    settings.artifact_backend = 's3'
    settings.artifact_root = None
    settings.artifact_s3_endpoint = f'https://artifacts.{settings.deployment_namespace}.svc:9000'
    settings.artifact_s3_bucket = settings.deployment_namespace
    settings.artifact_s3_prefix = f"replica_test/{engine.url.database.removeprefix('replica_test_')}/application/"
    operation = uuid4().hex
    with MaintenanceOperator(engine, operation_id=operation, owner_id='predecessor-restore-test', settings=settings) as op:
        with op.transaction() as session:
            # Seed the archived source fence/audit as imported SQL fixture data.
            # This is a protocol test, not a pg_dump or complete-copy restore.
            from app.services import audit
            from app.services.audit_event import AuditContext
            from app.deployment.predecessor_upgrade import migrate_fenced_predecessor
            session.execute(ArtifactStorageControl.__table__.update().values(maintenance_token=value['backup_id']))
            audit.record_in_session(session, action_type='replica_maintenance_enter', user_id='system', username='system',
                target_type='predecessor_artifact_fence', target_id=value['backup_id'], new_value={'epoch': 0},
                event_context=AuditContext(actor_type='system'), settings=settings)
            value['counts']['audit_events'] += 1
        plan = BackupPlan.model_validate(value)
        with op.transaction() as session:
            migrate_fenced_predecessor(session, settings, plan, operation_id=operation,
                target_database=engine.url.database, restore=True)
        with op.transaction() as session:
            document, result = rebind_predecessor_target(session, settings, plan, installation_id='5' * 32,
                operation_id=operation, selection_digest='sha256:' + '7' * 64)
        with op.transaction() as session:
            assert rebind_predecessor_target(session, settings, plan, installation_id='5' * 32,
                operation_id=operation, selection_digest='sha256:' + '7' * 64)[0] == document
            assert session.scalar(text('SELECT count(*) FROM auth_sessions')) == 0
            assert session.execute(text('SELECT status,maintenance_epoch,attempts FROM work_items')).one() == ('queued', 0, 0)
            assert session.execute(text('SELECT epoch,enabled,operation_kind FROM replica_maintenance_state')).one() == (2, True, 'restore')
            assert session.scalar(text('SELECT count(*) FROM documents')) == 1
    assert result['invalidated_sessions'] == 1 and document['installation_id'] == '5' * 32


@pytest.mark.parametrize('fail_after_ddl', [False, True])
def test_pg_artifact_fence_transfers_atomically_to_real_maintenance(predecessor, monkeypatch, fail_after_ddl):
    from alembic import command
    from sqlalchemy import inspect
    import importlib.util
    assert importlib.util.find_spec('app.deployment.predecessor_upgrade') is not None, 'Atomic predecessor fence transfer missing'
    from app.deployment.predecessor_upgrade import migrate_fenced_predecessor
    engine, _config, settings, old = predecessor
    value = source_config(engine)
    settings.deployment_namespace = value['namespace']
    settings.database_url = engine.url.render_as_string(hide_password=False)
    settings.artifact_store_id = value['store_id']
    with engine.begin() as conn:
        conn.execute(ArtifactStorageControl.__table__.update().values(active_store_id=value['store_id'],
            active_descriptor=source_descriptor(value)))
    tool, operation = load(), uuid4().hex
    assert hasattr(tool, 'hold_predecessor_source'), 'Predecessor artifact fence acquisition missing'
    with MaintenanceOperator(engine, operation_id=operation, owner_id='predecessor-update-test', settings=settings) as op:
        with op.transaction() as session:
            tool.hold_predecessor_source(session, value, old['runtime_fingerprint'], operation_id=operation)
        with op.transaction() as session:
            tool.hold_predecessor_source(session, value, old['runtime_fingerprint'], operation_id=operation)
            assert session.scalar(text('SELECT count(*) FROM audit_log')) == 1
            raw = recovery_plan(session.connection(), old['runtime_fingerprint'], installation_id=old['installation_id'],
                namespace=value['namespace'], backup_id=operation)
        plan = BackupPlan.model_validate(raw)
        if fail_after_ddl:
            upgrade = command.upgrade
            def unavailable(*args, **kwargs):
                upgrade(*args, **kwargs)
                raise RuntimeError('Simulated failure after actual DDL')
            monkeypatch.setattr(command, 'upgrade', unavailable)
            with pytest.raises(RuntimeError, match='after actual DDL'), op.transaction() as session:
                migrate_fenced_predecessor(session, settings, plan, operation_id=operation,
                    target_database=value['database'], restore=False)
            with op.transaction() as session:
                assert session.scalar(text('SELECT maintenance_token FROM artifact_storage_control')) == operation
                assert session.scalar(text('SELECT version_num FROM alembic_version')) == 'rpl04_runtime_identity'
                assert not inspect(session.connection()).has_table('replica_maintenance_state')
            return
        with op.transaction() as session:
            result = migrate_fenced_predecessor(session, settings, plan, operation_id=operation,
                target_database=value['database'], restore=False)
        with op.transaction() as session:
            assert migrate_fenced_predecessor(session, settings, plan, operation_id=operation,
                target_database=value['database'], restore=False) == result
            assert session.scalar(text('SELECT maintenance_token FROM artifact_storage_control')) == '0' * 32
            assert session.execute(text('SELECT epoch,enabled,operation_kind FROM replica_maintenance_state')).one() == (1, True, 'upgrade')
            assert session.execute(text('SELECT status,maintenance_epoch,attempts FROM work_items')).one() == ('queued', 0, 0)
