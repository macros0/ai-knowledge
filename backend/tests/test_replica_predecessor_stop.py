"""Pre-epoch backups require live CRI and pinned controller generation/specs."""
import copy
import importlib.util
from pathlib import Path

import pytest

from tests.test_replica_provider_stop import fixture
from tests.test_replica_backup_coordinator import config


def load():
    path = Path(__file__).resolve().parents[2] / 'scripts/kubernetes/replica_predecessor_backup.py'
    assert path.is_file(), 'Predecessor physical source guard missing'
    spec = importlib.util.spec_from_file_location('predecessor_backup_coordinator', path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def observations():
    value, binding, items, nodes = copy.deepcopy(fixture())
    binding['deployments'] = {o['metadata']['name']: o['metadata']['uid'] for o in items if o['kind'] == 'Deployment'}
    for role in ('postgres', 'qdrant', 'artifacts'):
        items.append({'kind': 'StatefulSet', 'metadata': {'name': 'storage-' + role,
            'uid': 'controller-' + role, 'generation': 1,
            'labels': {'app.kubernetes.io/instance': value['storage_release']}}, 'spec': {'replicas': 1}})
    return value, binding, items, nodes, []


def test_predecessor_stop_requires_matching_controller_scope_and_physical_observation():
    tool = load()
    value, binding, items, nodes, containers = observations()
    binding['controller_snapshot_sha256'] = tool.controller_snapshot(items, value)
    first = tool.predecessor_stop_observation(items, nodes, containers, value, binding)
    nodes[0]['status']['conditions'][0]['lastHeartbeatTime'] = 'different'
    assert tool.predecessor_stop_observation(items, nodes, containers, value, binding) == first


@pytest.mark.parametrize('failure', ['spec', 'generation', 'uid', 'missing_binding', 'hpa', 'storage_pod_uid',
    'orphan', 'active_pod', 'node_unreachable', 'scheduled_job'])
def test_predecessor_cannot_back_up_changed_or_live_source(failure):
    tool = load()
    value, binding, items, nodes, containers = observations()
    binding['controller_snapshot_sha256'] = tool.controller_snapshot(items, value)
    api = next(o for o in items if o['kind'] == 'Deployment' and o['metadata']['name'] == 'api')
    if failure == 'spec':
        api['spec']['unexpected'] = 'changed'
    elif failure == 'generation':
        api['metadata']['generation'] += 2
        api['status']['observedGeneration'] += 2
    elif failure == 'uid':
        api['metadata']['uid'] = 'replaced'
    elif failure == 'missing_binding':
        binding.pop('controller_snapshot_sha256')
    elif failure == 'hpa':
        items.append({'kind': 'HorizontalPodAutoscaler'})
    elif failure == 'storage_pod_uid':
        next(o for o in items if o['kind'] == 'Pod')['metadata']['uid'] = 'replaced'
    elif failure == 'orphan':
        containers.append({'state': 'CONTAINER_RUNNING', 'labels': {'io.kubernetes.pod.uid': 'old-worker'}})
    elif failure == 'active_pod':
        items.append({'kind': 'Pod', 'metadata': {'name': 'unknown'}, 'status': {'phase': 'Running'}})
    elif failure == 'node_unreachable':
        nodes[0]['status']['conditions'][0]['status'] = 'False'
    else:
        items.append({'kind': 'CronJob', 'spec': {'suspend': False}})
    with pytest.raises(ValueError):
        tool.predecessor_stop_observation(items, nodes, containers, value, binding)


def sql_snapshot():
    value = config()
    identity = {'id': 1, 'installation_id': value['installation_id'], 'fingerprint': '2' * 64,
        'storage_receipt_digest': 'sha256:' + '3' * 64, 'schema_revision': 'rpl04_runtime_identity'}
    control = {'id': 1, 'installation_id': value['installation_id'], 'active_store_id': value['store_id'],
        'maintenance_token': None, 'migration_id': None, 'readonly_roots': [],
        'active_descriptor': {'backend': 's3', 'store_id': value['store_id'], 'bucket': value['bucket'],
            'prefix': value['prefix'], 'endpoint': f"https://{value['storage_release']}-artifacts.{value['namespace']}.svc:9000"}}
    return value, {'database': value['database'], 'schema_revisions': ['rpl04_runtime_identity'],
        'maintenance_table': False, 'work_maintenance_epoch': False,
        'identities': [identity], 'controls': [control], 'active_work': 0,
        'active_chats': 0, 'owned_model_slots': 0, 'deleting_artifacts': 0}


def test_predecessor_sql_scope_is_read_only_and_exact():
    tool = load()
    value, snapshot = sql_snapshot()
    before = copy.deepcopy(snapshot)
    identity, control = tool.validate_predecessor_snapshot(snapshot, value, '2' * 64)
    assert identity == before['identities'][0] and control == before['controls'][0]
    assert snapshot == before


def test_predecessor_sql_guard_requires_its_committed_artifact_fence():
    tool = load()
    value, snapshot = sql_snapshot()
    snapshot['controls'][0]['maintenance_token'] = '7' * 32
    assert tool.validate_predecessor_snapshot(snapshot, value, '2' * 64, operation_id='7' * 32)[0]['fingerprint'] == '2' * 64
    with pytest.raises(ValueError):
        tool.validate_predecessor_snapshot(snapshot, value, '2' * 64, operation_id='8' * 32)


def test_reserved_migration_operation_cannot_acquire_a_predecessor_fence(monkeypatch):
    from types import SimpleNamespace
    from app.db.session import get_engine
    from app.services.work.maintenance_control import MaintenanceOperator
    value, _ = sql_snapshot()
    settings = SimpleNamespace(deployment_namespace=value['namespace'])
    with MaintenanceOperator(get_engine(), operation_id='0' * 32, owner_id='reserved-id-test',
            settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            def forbidden(*_args, **_kwargs):
                pytest.fail('Reserved operation reached the source control row')
            monkeypatch.setattr(session, 'get', forbidden)
            with pytest.raises(ValueError, match='Reserved'):
                load().hold_predecessor_source(session, value, '2' * 64, operation_id='0' * 32)


@pytest.mark.parametrize('failure', ['database', 'schema', 'maintenance_table', 'epoch_column',
    'identity', 'control', 'active_work', 'active_chats', 'owned_model_slots', 'deleting_artifacts', 'storage_operation'])
def test_predecessor_sql_refuses_foreign_or_uncertain_snapshot(failure):
    tool = load()
    value, snapshot = sql_snapshot()
    if failure == 'database':
        snapshot['database'] = 'foreign'
    elif failure == 'schema':
        snapshot['schema_revisions'] = ['rpl05_maintenance_epoch']
    elif failure == 'maintenance_table':
        snapshot['maintenance_table'] = True
    elif failure == 'epoch_column':
        snapshot['work_maintenance_epoch'] = True
    elif failure == 'identity':
        snapshot['identities'][0]['fingerprint'] = '9' * 64
    elif failure == 'control':
        snapshot['controls'][0]['active_descriptor']['bucket'] = 'foreign'
    elif failure == 'storage_operation':
        snapshot['controls'][0]['maintenance_token'] = '8' * 32
    else:
        snapshot[failure] = 1
    with pytest.raises(ValueError):
        tool.validate_predecessor_snapshot(snapshot, value, '2' * 64)
