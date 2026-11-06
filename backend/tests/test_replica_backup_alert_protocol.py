"""Public signed alert envelopes cannot leak source data or change receiver authority."""
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import sys

import pytest


KEY = b'k' * 32
NOW = datetime(2026, 11, 2, 18, tzinfo=timezone.utc)


def protocol():
    directory = Path(__file__).resolve().parents[2] / 'scripts/kubernetes'
    sys.path.insert(0, str(directory))
    assert importlib.util.find_spec('backup_alert_protocol') is not None, 'Signed bounded backup-alert protocol is missing'
    return importlib.import_module('backup_alert_protocol')


def health(*, state='alert', age=86401, code='backup_recovery_point_stale'):
    return {'schema_version': 1, 'state': state, 'checked_at': NOW.isoformat(),
        'alerts': [code] if state == 'alert' else [], 'archive_verified': True,
        'backup_id': 'a'*32, 'recovery_point_age_seconds': age, 'attempt_status': None,
        'restore_verified': False, 'rpo_assured': False}


def snapshot(tool, value=None):
    return tool.project_health(health() if value is None else value,
        installation_id='7'*32, destination_store_id='separate-pc-training-backup')


def event(tool):
    return tool.new_event(snapshot(tool), kind='alert', event_id='b'*32)


def test_health_projection_allowlists_payload_and_never_promises_restore_or_rpo():
    tool = protocol()
    value = health()
    value.update(database_url='postgresql://user:PRIVATE_CANARY@db/private', raw_provider_error='PRIVATE_CANARY')
    projected = snapshot(tool, value)
    body = tool.encode_event(tool.new_event(projected, kind='alert', event_id='b'*32))
    assert b'PRIVATE_CANARY' not in body and b'database_url' not in body and b'raw_provider_error' not in body
    assert json.loads(body)['health']['restore_verified'] is False
    assert json.loads(body)['health']['rpo_assured'] is False
    assert len(body) <= 8192


@pytest.mark.parametrize('field,value', [
    ('schema_version', True), ('state', 'ready'), ('alerts', ['PRIVATE_CANARY']),
    ('alerts', ['backup_unverifiable']*9), ('archive_verified', 'true'),
    ('recovery_point_age_seconds', -1), ('recovery_point_age_seconds', float('inf')),
    ('checked_at', '2026-11-02T18:00:00'), ('backup_id', '../PRIVATE_CANARY'),
    ('restore_verified', True), ('rpo_assured', True),
])
def test_malformed_or_invented_health_never_becomes_notification(field, value):
    tool = protocol()
    data = health()
    data[field] = value
    with pytest.raises(ValueError) as caught:
        snapshot(tool, data)
    assert 'PRIVATE_CANARY' not in str(caught.value)


def test_stale_age_increment_and_check_time_do_not_change_semantic_signature():
    tool = protocol()
    first = snapshot(tool)
    later = health(age=90000)
    later['checked_at'] = '2026-11-02T19:00:00+00:00'
    assert tool.semantic_digest(first) == tool.semantic_digest(snapshot(tool, later))
    failed = health(code='backup_last_attempt_failed')
    assert tool.semantic_digest(first) != tool.semantic_digest(snapshot(tool, failed))


def test_alert_and_recovery_have_consistent_health_state():
    tool = protocol()
    recovered = snapshot(tool, health(state='healthy', age=10))
    assert tool.new_event(recovered, kind='recovered', event_id='b'*32).kind == 'recovered'
    with pytest.raises(ValueError): tool.new_event(recovered, kind='alert')
    with pytest.raises(ValueError): tool.new_event(snapshot(tool), kind='recovered')


def test_request_signature_authenticates_exact_canonical_body_and_wall_time():
    tool = protocol()
    sent = event(tool)
    body = tool.encode_event(sent)
    headers = tool.sign_event(body, KEY, timestamp=int(NOW.timestamp()))
    received = tool.verify_event(body, headers, KEY, now=NOW.timestamp())
    assert received == sent


@pytest.mark.parametrize('fault', ['key', 'body', 'expired', 'future', 'missing', 'noncanonical', 'oversized'])
def test_bad_signature_replay_window_or_body_is_refused(fault):
    tool = protocol()
    body = tool.encode_event(event(tool))
    timestamp = int(NOW.timestamp()) + (-301 if fault == 'expired' else 31 if fault == 'future' else 0)
    if fault == 'noncanonical': body = json.dumps(json.loads(body), indent=2).encode()
    if fault == 'oversized': body = b'X'*8193
    headers = tool.sign_event(body, KEY, timestamp=timestamp) if fault != 'oversized' else {}
    if fault == 'missing': headers = {}
    if fault == 'body': body = body.replace(b'backup_recovery_point_stale', b'backup_last_attempt_failed')
    with pytest.raises(ValueError):
        tool.verify_event(body, headers, b'j'*32 if fault == 'key' else KEY, now=NOW.timestamp())


def test_acknowledgement_requires_receiver_mac_and_exact_event_body_binding():
    tool = protocol()
    sent = event(tool)
    body = tool.encode_event(sent)
    from hashlib import sha256
    ack = tool.make_ack(sent.event_id, sha256(body).hexdigest(), 'accepted', KEY)
    assert tool.verify_ack(sent, ack, KEY)
    assert not tool.verify_ack(sent, ack, b'j'*32)
    changed = dict(ack)
    changed['event_id'] = 'c'*32
    assert not tool.verify_ack(sent, changed, KEY)
    changed = dict(ack)
    changed['payload_sha256'] = '0'*64
    assert not tool.verify_ack(sent, changed, KEY)


@pytest.mark.parametrize('key', [b'', b'k'*31, b'k'*33, 'PRIVATE_CANARY'])
def test_signing_key_is_exactly_separate_binary_32_bytes(key):
    tool = protocol()
    with pytest.raises(ValueError) as caught:
        tool.sign_event(tool.encode_event(event(tool)), key, timestamp=int(NOW.timestamp()))
    assert 'PRIVATE_CANARY' not in str(caught.value)
