"""Pre-maintenance backups never invent an epoch or change old archive bytes."""
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json

import pytest

from app.deployment.replica_backup import (BackupError, BackupManifest, BackupPlan, BackupReader,
    BackupWriter, canonical, verify_backup, _seal)
from tests.test_replica_backup import KEY, fixture_plan


def previous_plan():
    plan, payloads = fixture_plan()
    value = plan.model_dump(mode='json')
    value['schema_revision'] = 'rpl04_runtime_identity'
    source = value['source']
    source['epoch'] = 0
    source['fencing_kind'] = 'owned-kind-normal-stop-rpl04-v1'
    proof = {'recipe': 'owned-kind-normal-stop-rpl04-v1',
        'namespace_uid': '77777777-7777-4777-8777-777777777777',
        'controller_snapshot_sha256': '5' * 64, 'process_observation_sha256': '6' * 64,
        'runtime_fingerprint_sha256': value['model_vector_sha256'],
        'artifact_fence_operation_id': source['operation_id'],
        'source_scope_sha256': '',
        'observed_at': (plan.source.consistent_at - timedelta(seconds=1)).isoformat(),
        'unknown_writers': 0, 'unreachable_nodes': 0, 'provider_requests': 0}
    scope = {key: source[key] for key in ('installation_id', 'namespace', 'operation_id')}
    scope.update(namespace_uid=proof['namespace_uid'], schema_revision=value['schema_revision'],
        runtime_fingerprint_sha256=value['model_vector_sha256'])
    proof['source_scope_sha256'] = hashlib.sha256(canonical(scope)).hexdigest()
    source['stop_proof'] = proof
    return value, payloads


def test_existing_format3_canonical_bytes_and_authentication_are_unchanged():
    plan, _ = fixture_plan(consistent_at=datetime(2026, 11, 1, tzinfo=timezone.utc))
    manifest = BackupManifest(plan=plan, status='incomplete', authentication='0' * 64)
    assert len(canonical(plan)) == 2609
    assert hashlib.sha256(canonical(plan)).hexdigest() == 'db4386abe275b62874873b1e8d49acd723442cff91fa799ec1bf3b419d73cf80'
    assert _seal(manifest, KEY).authentication == '1e92d6a1eb43cd1e8204aa2624b0d978a295d69b4a0406d5a52d6c21ca95b5b9'


def test_predecessor_archive_roundtrip_authenticates_zero_epoch_with_stop_proof(tmp_path):
    value, payloads = previous_plan()
    plan = BackupPlan.model_validate(value)
    assert plan.source.epoch == 0
    assert BackupPlan.model_validate_json(plan.model_dump_json()).source == plan.source
    root = tmp_path / 'archive'
    with BackupWriter(root, plan, KEY, source_guard=lambda: plan.source,
            destination_guard=lambda: plan.destination) as writer:
        for entry, body in payloads.items():
            writer.add(entry, BytesIO(body))
        writer.finish()
    reader = BackupReader(root, KEY)
    assert reader.manifest.plan == plan
    for entry, body in payloads.items():
        result = BytesIO()
        reader.copy(entry, result)
        assert result.getvalue() == body
    assert reader.verify().status == 'complete'


@pytest.mark.parametrize('failure', ['missing_proof', 'fake_epoch', 'boolean_epoch', 'current_schema',
    'unknown_schema', 'wrong_scope', 'wrong_runtime', 'unknown_writer', 'unreachable_node',
    'provider_request', 'drained_writer', 'stale_proof', 'future_proof', 'missing_namespace_uid', 'zero_operation', 'wrong_fence'])
def test_predecessor_proof_cannot_hide_unknown_or_unbound_source(failure):
    value, _ = previous_plan()
    source, proof = value['source'], value['source']['stop_proof']
    if failure == 'missing_proof':
        source.pop('stop_proof')
    elif failure in {'fake_epoch', 'boolean_epoch'}:
        source['epoch'] = 1 if failure == 'fake_epoch' else False
    elif failure in {'current_schema', 'unknown_schema'}:
        value['schema_revision'] = 'rpl05_maintenance_epoch' if failure == 'current_schema' else 'unknown'
    elif failure == 'wrong_scope':
        source['installation_id'] = '9' * 32
    elif failure == 'wrong_runtime':
        value['model_vector_sha256'] = '9' * 64
    elif failure == 'unknown_writer':
        proof['unknown_writers'] = 1
    elif failure == 'unreachable_node':
        proof['unreachable_nodes'] = 1
    elif failure == 'provider_request':
        proof['provider_requests'] = 1
    elif failure == 'drained_writer':
        source['writers']['api'] = 'drained'
    elif failure in {'stale_proof', 'future_proof'}:
        offset = -10 if failure == 'stale_proof' else 10
        proof['observed_at'] = (datetime.fromisoformat(source['consistent_at']) + timedelta(seconds=offset)).isoformat()
    elif failure == 'missing_namespace_uid':
        proof.pop('namespace_uid')
    elif failure == 'wrong_fence':
        proof['artifact_fence_operation_id'] = '9' * 32
    else:
        source['operation_id'] = value['backup_id'] = '0' * 32
    with pytest.raises(ValueError):
        BackupPlan.model_validate(value)


def test_previous_schema_cannot_use_a_fabricated_maintenance_source():
    plan, _ = fixture_plan()
    value = plan.model_dump(mode='json')
    value['schema_revision'] = 'rpl04_runtime_identity'
    with pytest.raises(ValueError):
        BackupPlan.model_validate(value)


def test_changed_physical_stop_observation_cannot_complete_backup(tmp_path):
    value, payloads = previous_plan()
    plan = BackupPlan.model_validate(value)
    observed = plan.source
    root = tmp_path / 'partial'
    with BackupWriter(root, plan, KEY, source_guard=lambda: observed,
            destination_guard=lambda: plan.destination) as writer:
        for entry, body in payloads.items():
            writer.add(entry, BytesIO(body))
        observed = plan.source.model_copy(update={'stop_proof': plan.source.stop_proof.model_copy(
            update={'process_observation_sha256': '9' * 64})})
        with pytest.raises(BackupError):
            writer.finish()
    assert json.loads((root / 'manifest.json').read_bytes())['status'] == 'incomplete'
    with pytest.raises(BackupError):
        verify_backup(root, KEY)
