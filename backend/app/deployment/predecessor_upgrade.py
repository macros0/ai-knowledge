"""Atomic transition from the real S4 token to the real rpl05 maintenance fence."""
from hashlib import sha256
from pathlib import Path
import re

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from app.deployment.replica_backup import BackupPlan, PreMaintenanceSourceState, canonical
from app.deployment.runtime_identity import SCHEMA_REVISION
from app.deployment.runtime_upgrade import _recorded
from app.services.work.maintenance_control import _require_operator


def migrate_fenced_predecessor(session, settings, plan, *, operation_id, target_database, restore):
    """Caller commits once; a failure rolls back token clearance and all DDL."""
    op = _require_operator(session, operation_id)
    connection = session.connection()
    if (op.settings is not settings or type(restore) is not bool
            or connection.dialect.name != 'postgresql'
            or connection.connection.driver_connection.autocommit is not False
            or not connection.in_transaction() or connection.in_nested_transaction()
            or not isinstance(plan, BackupPlan) or BackupPlan.model_validate_json(plan.model_dump_json()) != plan
            or not isinstance(plan.source, PreMaintenanceSourceState)
            or (plan.schema_revision, SCHEMA_REVISION) != ('rpl04_runtime_identity', 'rpl05_maintenance_epoch')
            or not re.fullmatch('replica_test_[a-z0-9]{8,32}', target_database)
            or make_url(settings.db_url).database != target_database
            or settings.deployment_namespace != 'replica-test-' + target_database.removeprefix('replica_test_')
            or connection.scalar(text('SELECT current_database()')) != target_database
            or restore == (settings.deployment_namespace == plan.source.namespace)):
        raise ValueError('Explicit owned transactional predecessor schema transition required')
    heads = connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all()
    identity = connection.execute(text('SELECT * FROM replica_runtime_identity WHERE id=1 FOR UPDATE')).mappings().one()
    control = connection.execute(text('SELECT * FROM artifact_storage_control WHERE id=1 FOR UPDATE')).mappings().one()
    source = control['active_descriptor']
    if (identity['installation_id'] != plan.source.installation_id
            or sha256(canonical(identity['fingerprint'])).hexdigest() != plan.model_vector_sha256
            or control['installation_id'] != plan.source.installation_id
            or control['active_store_id'] != settings.artifact_store_id or control['migration_id'] or control['readonly_roots']
            or not isinstance(source, dict) or source.get('backend') != 's3'
            or source.get('bucket') != plan.source.namespace
            or source.get('prefix') != f"replica_test/{plan.source.namespace.removeprefix('replica-test-')}/application/"
            or not _recorded(session, plan.backup_id, 0, action='replica_maintenance_enter', target_type='predecessor_artifact_fence')):
        raise ValueError('Predecessor source identity, storage or fence audit differs')
    for statement in (
            "SELECT count(*) FROM work_items WHERE status='running' OR owner_id IS NOT NULL OR lease_until IS NOT NULL",
            "SELECT count(*) FROM chat_runs WHERE status='running'",
            "SELECT count(*) FROM replica_capacity_slots WHERE resource='llm' AND (owner_id IS NOT NULL OR release_required)",
            "SELECT count(*) FROM artifact_objects WHERE state='deleting'"):
        if connection.scalar(text(statement)):
            raise ValueError('Predecessor work or provider state is uncertain')
    for key, table in {'documents': 'documents', 'concepts': 'okf_concepts', 'chunks': 'document_chunks',
            'work_items': 'work_items', 'audit_events': 'audit_log'}.items():
        if connection.scalar(text('SELECT count(*) FROM ' + table)) != getattr(plan.counts, key):
            raise ValueError('Predecessor snapshot counts changed')
    if connection.scalar(text("SELECT count(*) FROM artifact_objects WHERE state='ready'")) != plan.counts.blobs:
        raise ValueError('Predecessor artifact count changed')
    inspector = inspect(connection)
    if heads == [plan.schema_revision]:
        if (identity['schema_revision'] != plan.schema_revision or control['maintenance_token'] != plan.backup_id
                or inspector.has_table('replica_maintenance_state')
                or any(c['name'] == 'maintenance_epoch' for c in inspector.get_columns('work_items'))):
            raise ValueError('Exact artifact-fenced predecessor schema required')
        from alembic import command
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / 'alembic.ini'))
        config.set_main_option('script_location', str(root / 'alembic'))
        if ScriptDirectory.from_config(config).get_heads() != [SCHEMA_REVISION]:
            raise ValueError('Unsupported schema transition target')
        config.attributes['connection'] = connection
        # This NULL is visible only to the migration in the SAME transaction.
        # Other connections see the old token until rpl05 installs tokenzero.
        connection.execute(text('UPDATE artifact_storage_control SET maintenance_token=NULL WHERE id=1'))
        command.upgrade(config, SCHEMA_REVISION)
        session.expire_all()
    elif heads != [SCHEMA_REVISION]:
        raise ValueError('Unsupported predecessor schema outcome')
    state = connection.execute(text('SELECT * FROM replica_maintenance_state WHERE id=1')).mappings().one()
    if (not state['enabled'] or (state['installation_id'], state['epoch'], state['operation_id'], state['operation_kind'],
            state['owner_id'], state['intended_counts_json'], state['writer_receipts_json'])
            != (plan.source.installation_id, 1, '0' * 32, 'upgrade', 'schema-migration', {}, {})
            or connection.scalar(text('SELECT maintenance_token FROM artifact_storage_control')) != '0' * 32
            or connection.scalar(text('SELECT schema_revision FROM replica_runtime_identity')) != SCHEMA_REVISION
            or connection.scalar(text('SELECT count(*) FROM work_items WHERE maintenance_epoch<>0'))):
        raise ValueError('Forward migration did not retain its exact held fence')
    op.assert_owned()
    return {'schema_revision': SCHEMA_REVISION, 'epoch': 1, 'jobs_held': True}
