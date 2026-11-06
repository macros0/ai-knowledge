"""Explicit change-only notifications to the approved local training receiver.

No scheduler or background polling. Pending intent and authenticated receiver
ACKs are separate durable phases; an uncertain journal write blocks delivery.
"""
import argparse
import hmac
import json
import sys
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from filelock import FileLock
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(Path(__file__).resolve().parent), str(ROOT/'backend')]
from app.deployment.operator_database import read_regular_input
from app.deployment.replica_backup import _regular_open
from app.deployment.replica_backup_health import (
    _assert_no_write,
    _path,
    _persist,
    assess_backup,
)
from app.services.diagnostics.paths import assert_no_links
from backup_alert_protocol import (
    MAX_BODY,
    Digest,
    HealthSnapshot,
    Hex32,
    NotificationEvent,
    StoreName,
    canonical,
    encode_event,
    new_event,
    require_key,
    semantic_digest,
    sign_event,
    verify_ack,
)


class _DeliveryState(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, hide_input_in_errors=True)
    schema_version: Literal[1]
    receiver_binding_sha256: Digest
    last_notified_sha256: Digest | None = None
    alert_active: bool = False
    pending: NotificationEvent | None = None
    pending_sha256: Digest | None = None

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError('Integer version required')
        return value

    @model_validator(mode='after')
    def coherent(self):
        if (self.pending is None) != (self.pending_sha256 is None):
            raise ValueError('Inconsistent pending delivery')
        if self.pending is not None and sha256(encode_event(self.pending)).hexdigest() != self.pending_sha256:
            raise ValueError('Pending notification bytes changed')
        if self.alert_active and self.last_notified_sha256 is None:
            raise ValueError('Missing acknowledged alert identity')
        return self


def _endpoint(value):
    try:
        parsed = urlsplit(value)
        if (not isinstance(value, str) or parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.path != '/v1/backup-alerts' or parsed.port is None or not 1 <= parsed.port <= 65535
                or value != f'http://127.0.0.1:{parsed.port}/v1/backup-alerts'):
            raise ValueError()
        return value
    except (TypeError, ValueError, AttributeError):
        raise ValueError('Explicit training loopback notification endpoint required') from None


def _health(value):
    try:
        raw = value.model_dump_json() if isinstance(value, HealthSnapshot) else json.dumps(value, allow_nan=False)
        return HealthSnapshot.model_validate_json(raw)
    except (TypeError, ValueError, ValidationError):
        raise ValueError('Invalid bounded public backup observation') from None


def _binding(current, endpoint, key):
    return hmac.new(key, b'okf-backup-alert-delivery-authority-v1\n' + canonical({
        'installation_id': current.installation_id, 'destination_store_id': current.destination_store_id,
        'endpoint': endpoint}), sha256).hexdigest()


def _load(path):
    _assert_no_write(path)
    with _regular_open(path, path.parent) as stream:
        body = stream.read(16385)
    _assert_no_write(path)
    if len(body) > 16384:
        raise ValueError('Delivery journal exceeds its bound')
    return _DeliveryState.model_validate_json(body)


def _post(endpoint, body, headers):
    with (
        httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client,
        client.stream('POST', endpoint, content=body, headers={**headers, 'Content-Type': 'application/json'}) as response,
    ):
        data = bytearray()
        for chunk in response.iter_bytes(chunk_size=4096):
            data.extend(chunk)
            if len(data) > MAX_BODY:
                raise ValueError('Notification response exceeds its bound')
        return response.status_code, json.loads(data)


def _apply(current, *, endpoint, key, journal_path, transport):
    path = _path(journal_path)
    lock = path.with_name(path.name+'.lock')
    assert_no_links(lock, path.parent)
    authority = _binding(current, endpoint, key)
    with FileLock(lock, timeout=0):
        _assert_no_write(path)
        state = _load(path) if path.exists() else _DeliveryState(schema_version=1, receiver_binding_sha256=authority)
        if state.receiver_binding_sha256 != authority:
            raise ValueError('Delivery journal belongs to another authority')
        resumed = state.pending is not None
        event = state.pending
        if event is not None:
            if (event.health.installation_id != current.installation_id
                    or event.health.destination_store_id != current.destination_store_id):
                raise ValueError('Pending event belongs to another source')
        else:
            kind = None
            if current.state == 'alert' and semantic_digest(current) != state.last_notified_sha256:
                kind = 'alert'
            elif current.state == 'healthy' and state.alert_active:
                kind = 'recovered'
            if kind is None:
                return {'status': 'quiet', 'health_state': current.state, 'event_id': None,
                    'resumed_pending': False, 'recheck_required': False}
            event = new_event(current, kind=kind)
            state = _DeliveryState(schema_version=1, receiver_binding_sha256=authority,
                last_notified_sha256=state.last_notified_sha256, alert_active=state.alert_active,
                pending=event, pending_sha256=sha256(encode_event(event)).hexdigest())
            # Reuse the proven write-intent/fsync publisher without changing the
            # existing BackupAttempt shape or canonical status bytes.
            _persist(path, state)
        body = encode_event(event)
        result = {'status': 'pending', 'health_state': current.state, 'event_id': event.event_id,
            'resumed_pending': resumed, 'recheck_required': semantic_digest(event.health) != semantic_digest(current)}
        try:
            status, acknowledgement = (transport or _post)(endpoint, body, sign_event(body,key,timestamp=int(time.time())))
            acknowledged = status in {200,201} and verify_ack(event, acknowledgement, key)
        except Exception:  # noqa: BLE001 -- raw HTTP/provider errors never become public result fields.
            acknowledged = False
        if not acknowledged:
            return result
        completed = _DeliveryState(schema_version=1, receiver_binding_sha256=authority,
            last_notified_sha256=semantic_digest(event.health), alert_active=event.kind == 'alert')
        _persist(path, completed)
        return {**result, 'status': 'delivered', 'receiver_status': acknowledgement['status']}


def notify_once(value, *, endpoint, signing_key, journal_path, apply=False, transport=None):
    """Manual one-step delivery; a pending older event always finishes first."""
    current = _health(value)
    endpoint = _endpoint(endpoint)
    if type(apply) is not bool:
        raise ValueError('Explicit boolean apply action required')
    if not apply:
        return {'status': 'dry-run', 'health_state': current.state, 'event_id': None,
            'resumed_pending': False, 'recheck_required': False}
    key = require_key(signing_key)
    try:
        return _apply(current, endpoint=endpoint, key=key, journal_path=journal_path, transport=transport)
    except Exception:  # noqa: BLE001 -- journal/path/schema/lock failures all retain public redaction.
        raise ValueError('Backup notification operation is unavailable or uncertain') from None


class _Configuration(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, hide_input_in_errors=True)
    schema_version: Literal[1]
    scope: Literal['owned-lab']
    archive: Path
    key_file: Path
    installation_id: Hex32
    destination_store_id: StoreName
    receiver_url: str = Field(max_length=255)
    signing_key_file: Path
    delivery_journal: Path
    attempt_journal: Path | None = None
    observation_mode: Literal['real-time', 'fault-shadow'] = 'real-time'

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError('Integer version required')
        return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Explicitly deliver through the configured local receiver')
    parser.add_argument('--now', help='Aware offline health-clock drill; transport signatures still use real wall time')
    args = parser.parse_args(argv)
    try:
        config = _Configuration.model_validate_json(read_regular_input(args.config, max_bytes=16384))
        endpoint = _endpoint(config.receiver_url)
        archive = config.archive.resolve()
        if (any(path.resolve().is_relative_to(archive) for path in (
                config.key_file, config.signing_key_file, config.delivery_journal))
                or config.key_file.resolve() == config.signing_key_file.resolve()):
            raise ValueError('Separate protected key and delivery paths required')
        now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('Aware observation time required')
        archive_key = None
        try:
            archive_key = require_key(read_regular_input(config.key_file, max_bytes=32))
            health = assess_backup(config.archive, archive_key, installation_id=config.installation_id,
                destination_store_id=config.destination_store_id, now=now, attempt_path=config.attempt_journal)
        except Exception:  # noqa: BLE001 -- unavailable cold-health input produces a fixed public alert.
            health = {'schema_version':1,'state':'alert','checked_at':now.isoformat(),
                'alerts':['backup_health_unavailable'],'archive_verified':False,'backup_id':None,
                'recovery_point_age_seconds':None,'attempt_status':None,'restore_verified':False,'rpo_assured':False}
        from backup_alert_protocol import project_health
        current = project_health(health, installation_id=config.installation_id,
            destination_store_id=config.destination_store_id,
            observation_mode='offline-clock-drill' if args.now else config.observation_mode)
        signing_key = require_key(read_regular_input(config.signing_key_file, max_bytes=32)) if args.apply else None
        if signing_key is not None and archive_key is not None and hmac.compare_digest(signing_key, archive_key):
            raise ValueError('Notification signing must not reuse archive encryption key')
        delivery = notify_once(current, endpoint=endpoint, signing_key=signing_key,
            journal_path=config.delivery_journal, apply=args.apply)
        report = {'schema_version':1,'health':current.model_dump(mode='json'),'delivery':delivery,
            'production_schedule':'disabled'}
        print(json.dumps(report))
        if current.state == 'alert' or delivery['status'] == 'pending':
            return 2
        return 1 if current.state == 'pending' else 0
    except Exception:  # noqa: BLE001 -- never echo private configuration/path/key/provider values.
        print(json.dumps({'schema_version':1,'delivery':{'status':'refused','error_code':'backup_alert_unavailable'},
            'production_schedule':'disabled'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
