"""Bounded public backup notifications; request and durable ACK domains are separate."""
import hmac
import json
import math
import re
from hashlib import sha256
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

MAX_BODY = 8192
Hex32 = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
StoreName = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')]
AlertCode = Literal['backup_recovery_point_stale', 'backup_unverifiable', 'backup_last_attempt_failed',
    'backup_attempt_overdue', 'backup_selection_stale', 'backup_attempt_unverifiable', 'backup_health_unavailable']


class ProtocolError(ValueError):
    pass


class _Public(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, hide_input_in_errors=True)
    schema_version: Literal[1]

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError('Integer version required')
        return value


class HealthSnapshot(_Public):
    installation_id: Hex32
    destination_store_id: StoreName
    observation_mode: Literal['real-time', 'offline-clock-drill', 'fault-shadow']
    state: Literal['healthy', 'pending', 'alert']
    checked_at: AwareDatetime
    alerts: list[AlertCode] = Field(max_length=8)
    archive_verified: bool
    backup_id: Hex32 | None
    recovery_point_age_seconds: float | None = Field(ge=0, le=2**52, allow_inf_nan=False)
    attempt_status: Literal['running', 'complete', 'failed'] | None
    attempt_id: Hex32 | None
    restore_verified: Literal[False]
    rpo_assured: Literal[False]

    @field_validator('alerts')
    @classmethod
    def unique_alerts(cls, value):
        if len(value) != len(set(value)):
            raise ValueError('Duplicate alert code')
        return sorted(value)

    @model_validator(mode='after')
    def coherent(self):
        if (bool(self.alerts) != (self.state == 'alert')
                or self.archive_verified != (self.backup_id is not None and self.recovery_point_age_seconds is not None)
                or self.state in {'healthy', 'pending'} and not self.archive_verified):
            raise ValueError('Inconsistent backup observation')
        return self


class NotificationEvent(_Public):
    event_id: Hex32
    kind: Literal['alert', 'recovered']
    health: HealthSnapshot

    @model_validator(mode='after')
    def coherent(self):
        if self.kind == 'alert' and self.health.state != 'alert' or self.kind == 'recovered' and self.health.state != 'healthy':
            raise ValueError('Event differs from backup state')
        return self


class Acknowledgement(_Public):
    event_id: Hex32
    payload_sha256: Digest
    status: Literal['accepted', 'duplicate']
    mac: Digest


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _validated(model, value):
    try:
        raw = value.model_dump_json() if isinstance(value, BaseModel) else json.dumps(value, allow_nan=False)
        return model.model_validate_json(raw)
    except (ValidationError, TypeError, ValueError, OverflowError):
        raise ProtocolError('Invalid bounded public backup notification') from None


def project_health(value, *, installation_id, destination_store_id, observation_mode='real-time', attempt_id=None):
    """Only allowlisted fields leave cold health; raw provider metadata is ignored."""
    if type(value) is not dict:
        raise ProtocolError('Invalid bounded public backup notification')
    return _validated(HealthSnapshot, {
        'schema_version': value.get('schema_version'), 'installation_id': installation_id,
        'destination_store_id': destination_store_id, 'observation_mode': observation_mode,
        **{name: value.get(name) for name in ('state', 'checked_at', 'alerts', 'archive_verified', 'backup_id',
            'recovery_point_age_seconds', 'attempt_status')},
        'attempt_id': attempt_id, 'restore_verified': value.get('restore_verified', False),
        'rpo_assured': value.get('rpo_assured', False)})


def semantic_digest(value):
    current = _validated(HealthSnapshot, value)
    # Observing the same ageing recovery point is not a new incident.
    return sha256(canonical(current.model_dump(mode='json', exclude={'checked_at', 'recovery_point_age_seconds'}))).hexdigest()


def new_event(value, *, kind, event_id=None):
    current = _validated(HealthSnapshot, value)
    return _validated(NotificationEvent, {'schema_version': 1, 'event_id': event_id or uuid4().hex,
        'kind': kind, 'health': current.model_dump(mode='json')})


def encode_event(value):
    body = canonical(_validated(NotificationEvent, value).model_dump(mode='json'))
    if len(body) > MAX_BODY:
        raise ProtocolError('Backup notification exceeds its bound')
    return body


def require_key(key):
    if type(key) is not bytes or len(key) != 32:
        raise ProtocolError('Separate binary 32-byte notification key required')
    return key


def sign_event(body, key, *, timestamp):
    require_key(key)
    if type(body) is not bytes or len(body) > MAX_BODY or type(timestamp) is not int or not 0 <= timestamp <= 10**12:
        raise ProtocolError('Invalid bounded notification transport')
    stamp = str(timestamp)
    signature = hmac.new(key, b'okf-backup-alert-v1\n' + stamp.encode() + b'\n' + body, sha256).hexdigest()
    return {'X-OKF-Alert-Time': stamp, 'X-OKF-Alert-Signature': signature}


def verify_event(body, headers, key, *, now):
    try:
        require_key(key)
        if (type(body) is not bytes or not 0 < len(body) <= MAX_BODY
                or type(now) not in {int, float} or not math.isfinite(now)):
            raise ValueError()
        selected = {name.lower(): value for name, value in headers.items()}
        stamp, supplied = selected.get('x-okf-alert-time'), selected.get('x-okf-alert-signature')
        if (not isinstance(stamp, str) or not re.fullmatch(r'[0-9]{1,13}', stamp)
                or not isinstance(supplied, str) or not re.fullmatch('[0-9a-f]{64}', supplied)):
            raise ValueError()
        timestamp = int(stamp)
        if not now - 300 <= timestamp <= now + 30:
            raise ValueError()
        expected = sign_event(body, key, timestamp=timestamp)['X-OKF-Alert-Signature']
        if not hmac.compare_digest(supplied, expected):
            raise ValueError()
        event = NotificationEvent.model_validate_json(body)
        if encode_event(event) != body:
            raise ValueError()
        return event
    except (ValidationError, TypeError, ValueError, AttributeError, OverflowError):
        raise ProtocolError('Backup notification authentication or format refused') from None


def make_ack(event_id, payload_sha256, status, key):
    require_key(key)
    value = _validated(Acknowledgement, {'schema_version': 1, 'event_id': event_id,
        'payload_sha256': payload_sha256, 'status': status, 'mac': '0'*64})
    public = value.model_dump(mode='json', exclude={'mac'})
    public['mac'] = hmac.new(key, b'okf-backup-alert-ack-v1\n' + canonical(public), sha256).hexdigest()
    return public


def verify_ack(event, value, key):
    try:
        acknowledgement = _validated(Acknowledgement, value)
        current = _validated(NotificationEvent, event)
        expected_sha = sha256(encode_event(current)).hexdigest()
        expected = make_ack(current.event_id, expected_sha, acknowledgement.status, key)
        return (acknowledgement.event_id == current.event_id
            and acknowledgement.payload_sha256 == expected_sha
            and hmac.compare_digest(acknowledgement.mac, expected['mac']))
    except (ProtocolError, ValueError, TypeError):
        return False
