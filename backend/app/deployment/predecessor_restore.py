"""Owned restoration of an authenticated rpl04 import after real rpl05 migration.

Source-stop, provider imports/checksums and disjoint target verification belong
to the coordinator. This SQL step never migrates, releases or starts workloads.
"""
from hashlib import sha256
import re

from sqlalchemy import inspect, text

from app.db.models import ArtifactStorageControl, MaintenanceState, ReplicaRuntimeIdentity
from app.deployment.bootstrap import _receipt, canonical
from app.deployment.replica_backup import BackupPlan, PreMaintenanceSourceState
from app.deployment.runtime_identity import SCHEMA_REVISION, identity_document
from app.deployment.runtime_upgrade import _recorded
from app.services.artifacts.control import descriptor
from app.services.audit_hold import validate_outbox_contract
from app.services.work.maintenance_control import _require_operator
from app.services.work.store import _now


def rebind_predecessor_target(session, settings, plan, *, installation_id, operation_id, selection_digest):
    op = _require_operator(session, operation_id)
    if (op.settings is not settings or not isinstance(plan, BackupPlan)
            or BackupPlan.model_validate_json(plan.model_dump_json()) != plan
            or not isinstance(plan.source, PreMaintenanceSourceState)
            or (plan.schema_revision, SCHEMA_REVISION) != ('rpl04_runtime_identity', 'rpl05_maintenance_epoch')
            or not re.fullmatch('[0-9a-f]{32}', installation_id) or installation_id == plan.source.installation_id
            or operation_id == '0' * 32 or not re.fullmatch('sha256:[0-9a-f]{64}', selection_digest)
            or not re.fullmatch('replica-test-[a-z0-9]{8,32}', settings.deployment_namespace)
            or settings.deployment_namespace == plan.source.namespace or settings.artifact_backend != 's3'
            or settings.okf_deployment_schema != 'replicas-v1'):
        raise ValueError('Explicit supported predecessor recovery scope required')
    if session.execute(text('SELECT version_num FROM alembic_version')).scalars().all() != [SCHEMA_REVISION]:
        raise ValueError('Predecessor import requires its real forward schema migration')
    state = session.get(MaintenanceState, 1, with_for_update=True)
    control = session.get(ArtifactStorageControl, 1, with_for_update=True)
    identity = session.get(ReplicaRuntimeIdentity, 1, with_for_update=True)
    if (state is None or control is None or identity is None or not state.enabled
            or identity.schema_revision != SCHEMA_REVISION
            or control.active_store_id != settings.artifact_store_id
            or control.migration_id is not None or control.readonly_roots):
        raise ValueError('Held migrated predecessor identity or storage differs')
    active = (
        "SELECT count(*) FROM work_items WHERE status='running' OR owner_id IS NOT NULL OR lease_until IS NOT NULL",
        "SELECT count(*) FROM chat_runs WHERE status='running'",
        "SELECT count(*) FROM replica_capacity_slots WHERE resource='llm' AND (owner_id IS NOT NULL OR release_required)",
        "SELECT count(*) FROM artifact_objects WHERE state='deleting'",
    )
    if any(session.scalar(text(statement)) for statement in active):
        raise ValueError('Imported work or provider state is uncertain')
    document = identity_document(settings, installation_id=installation_id,
        storage_receipt_digest='sha256:' + sha256(canonical(_receipt(settings, installation_id, selection_digest))).hexdigest())
    replay = identity.installation_id == installation_id
    recorded = _recorded(session, operation_id, 2, action='replica_maintenance_enter', target_type='replica_operation')
    if replay:
        if (identity.fingerprint != document['runtime_fingerprint']
                or identity.storage_receipt_digest != document['storage_receipt_digest']
                or (state.installation_id, state.epoch, state.operation_id, state.operation_kind, state.owner_id)
                    != (installation_id, 2, operation_id, 'restore', op.owner_id)
                or (control.installation_id, control.maintenance_token, control.active_descriptor)
                    != (installation_id, operation_id, descriptor(settings))
                or not recorded or session.scalar(text('SELECT count(*) FROM auth_sessions'))):
            raise ValueError('Predecessor recovery resume differs')
    elif (identity.installation_id != plan.source.installation_id
            or sha256(canonical(identity.fingerprint)).hexdigest() != plan.model_vector_sha256
            or (state.installation_id, state.epoch, state.operation_id, state.operation_kind, state.owner_id,
                state.intended_counts_json, state.writer_receipts_json)
                != (plan.source.installation_id, 1, '0' * 32, 'upgrade', 'schema-migration', {}, {})
            or control.installation_id != plan.source.installation_id or control.maintenance_token != '0' * 32
            or not isinstance(control.active_descriptor, dict)
            or control.active_descriptor.get('backend') != 's3'
            or control.active_descriptor.get('bucket') != plan.source.namespace
            or control.active_descriptor.get('prefix') != f"replica_test/{plan.source.namespace.removeprefix('replica-test-')}/application/"
            or control.active_descriptor == descriptor(settings) or recorded):
        raise ValueError('Imported predecessor does not match the authenticated source and migration fence')
    from app.services.audit_modes import get_audit_mode
    counts = plan.counts.model_dump()
    for key, table in {'documents': 'documents', 'concepts': 'okf_concepts', 'chunks': 'document_chunks',
            'work_items': 'work_items', 'audit_events': 'audit_log'}.items():
        expected = counts[key] + int(replay and key == 'audit_events' and get_audit_mode(settings).db_enabled)
        if session.scalar(text('SELECT count(*) FROM ' + table)) != expected:
            raise ValueError('Imported predecessor metadata counts differ')
    if session.scalar(text("SELECT count(*) FROM artifact_objects WHERE state='ready'")) != counts['blobs']:
        raise ValueError('Imported predecessor artifact inventory differs')
    has_oidc = 'oidc_login_flows' in inspect(session.connection()).get_table_names()
    if replay:
        if has_oidc and session.scalar(text('SELECT count(*) FROM oidc_login_flows')):
            raise ValueError('Restored source login flows remain')
        return document, {'invalidated_sessions': 0, 'invalidated_oidc_flows': 0, 'held_audit_events': 0}
    validate_outbox_contract(session.connection())
    invalidated = session.execute(text('DELETE FROM auth_sessions')).rowcount
    flows = session.execute(text('DELETE FROM oidc_login_flows')).rowcount if has_oidc else 0
    held = session.execute(text("UPDATE audit_outbox SET status='held',last_error_code='restored_backup' WHERE status='pending'")).rowcount
    identity.installation_id, identity.fingerprint = installation_id, document['runtime_fingerprint']
    identity.storage_receipt_digest, identity.created_at = document['storage_receipt_digest'], _now(session)
    control.installation_id, control.active_descriptor, control.maintenance_token = installation_id, descriptor(settings), operation_id
    state.installation_id, state.epoch = installation_id, 2
    state.operation_id, state.operation_kind, state.owner_id = operation_id, 'restore', op.owner_id
    state.started_at, state.completed_at, state.writer_receipts_json = _now(session), None, {}
    from app.services import audit
    from app.services.audit_event import AuditContext
    audit.record_in_session(session, action_type='replica_maintenance_enter', user_id='system', username='system',
        target_type='replica_operation', target_id=operation_id, new_value={'epoch': 2},
        event_context=AuditContext(actor_type='system'), settings=settings)
    session.flush()
    return document, {'invalidated_sessions': invalidated, 'invalidated_oidc_flows': flows, 'held_audit_events': held}
