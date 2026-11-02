"""Source guards and explicit S4 fence for the owned basic rpl04 backup recipe.

Callers retain an offline operator and snapshot connection and fetch Kubernetes,
nodes and CRI afresh on every guard call. No saved digest alone proves a stop.
This module does not stop workloads, export providers or activate an archive.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replica_backup import validate_config
from test_scripts.replica_provider_stop import observation_digest, stopped_cri_facts, stopped_facts


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def controller_snapshot(items, value):
    controllers, pods = [], []
    for obj in items:
        kind, meta = obj.get('kind'), obj.get('metadata', {})
        if kind in {'Deployment', 'StatefulSet'}:
            generation = meta.get('generation')
            if not meta.get('uid') or type(generation) is not int or generation < 1:
                raise ValueError('Explicit controller UID and generation required')
            controllers.append({'kind': kind, 'name': meta['name'], 'uid': meta['uid'],
                'generation': generation, 'spec_sha256': _digest(obj['spec']),
                'labels_sha256': _digest(meta.get('labels', {}))})
        elif kind == 'Pod' and meta.get('labels', {}).get('app.kubernetes.io/instance') == value['storage_release']:
            if not meta.get('uid'):
                raise ValueError('Explicit storage Pod UID required')
            pods.append({'name': meta['name'], 'uid': meta['uid'], 'spec_sha256': _digest(obj['spec']),
                'containers': [{key: c.get(key) for key in ('name', 'imageID', 'containerID')}
                    for c in obj.get('status', {}).get('containerStatuses', [])]})
    if len(controllers) != 7 or len(pods) != 3:
        raise ValueError('Exact four application and three storage controllers/Pods required')
    return _digest({'controllers': sorted(controllers, key=lambda o: (o['kind'], o['name'])),
        'storage_pods': sorted(pods, key=lambda o: o['name'])})


def predecessor_stop_observation(items, nodes, containers, value, binding):
    if any(obj.get('kind') in {'HorizontalPodAutoscaler', 'VerticalPodAutoscaler'} for obj in items):
        raise ValueError('Autoscaled predecessor sources are not supported')
    snapshot = controller_snapshot(items, value)
    if binding.get('controller_snapshot_sha256') != snapshot:
        raise ValueError('Stopped controller generation/spec or storage binding changed')
    facts = stopped_facts(items, nodes, value, binding)
    cri = stopped_cri_facts(containers, value, binding)
    return _digest({'controllers': snapshot, 'physical': observation_digest(facts, cri)})


def validate_predecessor_snapshot(snapshot, config, fingerprint, *, operation_id=None):
    validate_config(config)
    if (operation_id is not None and (not re.fullmatch('[0-9a-f]{32}', operation_id) or operation_id == '0' * 32)
            or not re.fullmatch('[0-9a-f]{64}', fingerprint)
            or snapshot['database'] != config['database']
            or snapshot['schema_revisions'] != ['rpl04_runtime_identity']
            or snapshot['maintenance_table'] is not False or snapshot['work_maintenance_epoch'] is not False
            or len(snapshot['identities']) != 1 or len(snapshot['controls']) != 1
            or any(type(snapshot[key]) is not int or snapshot[key] != 0 for key in (
                'active_work', 'active_chats', 'owned_model_slots', 'deleting_artifacts'))):
        raise ValueError('Exact quiescent predecessor schema and database required')
    identity, control = snapshot['identities'][0], snapshot['controls'][0]
    descriptor = control.get('active_descriptor')
    expected = {'backend': 's3', 'store_id': config['store_id'], 'bucket': config['bucket'],
        'prefix': config['prefix'],
        'endpoint': f"https://{config['storage_release']}-artifacts.{config['namespace']}.svc:9000"}
    if (identity.get('id') != 1 or identity.get('installation_id') != config['installation_id']
            or identity.get('fingerprint') != fingerprint or identity.get('schema_revision') != 'rpl04_runtime_identity'
            or not re.fullmatch('sha256:[0-9a-f]{64}', identity.get('storage_receipt_digest', ''))
            or control.get('id') != 1 or control.get('installation_id') != config['installation_id']
            or control.get('active_store_id') != config['store_id'] or control.get('maintenance_token') != operation_id
            or control.get('migration_id') is not None or control.get('readonly_roots') != []
            or not isinstance(descriptor, dict) or any(descriptor.get(key) != item for key, item in expected.items())):
        raise ValueError('Predecessor identity or storage scope differs')
    return dict(identity), dict(control)


def predecessor_sql_source(connection, config, fingerprint, *, operation_id=None):
    from sqlalchemy import inspect, text
    if connection.dialect.name != 'postgresql':
        raise ValueError('Predecessor source requires an isolated PostgreSQL connection')
    inspector = inspect(connection)
    snapshot = {'database': connection.scalar(text('SELECT current_database()')),
        'schema_revisions': connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all(),
        'maintenance_table': inspector.has_table('replica_maintenance_state'),
        'work_maintenance_epoch': any(c['name'] == 'maintenance_epoch' for c in inspector.get_columns('work_items')),
        'identities': [dict(row) for row in connection.execute(text('SELECT * FROM replica_runtime_identity')).mappings()],
        'controls': [dict(row) for row in connection.execute(text('SELECT * FROM artifact_storage_control')).mappings()]}
    predicates = {
        'active_work': "work_items WHERE status='running' OR owner_id IS NOT NULL OR lease_until IS NOT NULL",
        'active_chats': "chat_runs WHERE status='running'",
        'owned_model_slots': "replica_capacity_slots WHERE resource='llm' AND (owner_id IS NOT NULL OR release_required)",
        'deleting_artifacts': "artifact_objects WHERE state='deleting'",
    }
    snapshot.update({key: connection.scalar(text('SELECT count(*) FROM ' + statement))
        for key, statement in predicates.items()})
    return validate_predecessor_snapshot(snapshot, config, fingerprint, operation_id=operation_id)


def hold_predecessor_source(session, config, fingerprint, *, operation_id):
    """Commit an existing S4 token before drain/export; never manufacture epoch1."""
    from app.db.models import ArtifactStorageControl
    from app.deployment.runtime_upgrade import _recorded
    from app.services.work.maintenance_control import _require_operator
    op = _require_operator(session, operation_id)
    if operation_id == '0' * 32:
        raise ValueError('Reserved predecessor operation id')
    if op.settings is None or op.settings.deployment_namespace != config['namespace']:
        raise ValueError('Owned predecessor audit settings required')
    control = session.get(ArtifactStorageControl, 1, with_for_update=True)
    if control is None or control.maintenance_token not in {None, operation_id}:
        raise ValueError('Another predecessor storage operation is active')
    predecessor_sql_source(session.connection(), config, fingerprint,
        operation_id=operation_id if control.maintenance_token else None)
    recorded = _recorded(session, operation_id, 0, action='replica_maintenance_enter', target_type='predecessor_artifact_fence')
    if control.maintenance_token == operation_id:
        if not recorded:
            raise ValueError('Predecessor fence audit is missing')
        return
    if recorded:
        raise ValueError('A completed predecessor operation cannot be reused')
    control.maintenance_token = operation_id
    from app.services import audit
    from app.services.audit_event import AuditContext
    audit.record_in_session(session, action_type='replica_maintenance_enter', user_id='system', username='system',
        target_type='predecessor_artifact_fence', target_id=operation_id, new_value={'epoch': 0},
        event_context=AuditContext(actor_type='system'), settings=op.settings)
    session.flush()
