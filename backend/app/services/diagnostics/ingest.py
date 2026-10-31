"""Single-backend inbox; never accepts arbitrary logs or browser/backend events."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import threading
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select

from app.db.models import DiagnosticIngestBatch
from app.db.session import session_scope
from .sanitize import encode_event, sanitize_event, valid_uuid
from .store import StorageQuotaError

MAX_BATCH_BYTES = 256 * 1024
_lock = threading.RLock()  # Build still enforces a single backend process.


class IngestConflict(ValueError):
    pass


class DiagnosticBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str
    batch_id: str
    events: list[dict] = Field(min_length=1, max_length=100)

    @field_validator("instance_id", "batch_id")
    @classmethod
    def identity(cls, value):
        if valid_uuid(value) is None:
            raise ValueError("Invalid batch identity")
        return value

    @field_validator("events")
    @classmethod
    def safe_events(cls, values):
        result = []
        seen = set()
        for raw in values:
            event = sanitize_event(raw)
            if (event is None or event["component"] != "frontend" or event["origin"] != "server"
                    or event["event_id"] in seen):
                raise ValueError("Invalid frontend event")
            encode_event(event)  # Also enforces MAX_EVENT_BYTES.
            seen.add(event["event_id"])
            result.append(event)
        return result


class IngestReceipt(BaseModel):
    accepted: int
    duplicate: bool


def current_control(store):
    with store._projection_lock:
        path = store.safe_path(store.root / "control/capture.json")
        if not path.is_file() or path.stat().st_size > 4096:
            raise ValueError("Control unavailable")
        value = json.loads(path.read_bytes())
    now = datetime.now(timezone.utc).timestamp()
    if (not isinstance(value, dict) or value.get("schema_version") != 2
            or not isinstance(value.get("lease_until"), (int, float))
            or not now < value["lease_until"] <= now + 11):
        raise ValueError("Control expired")
    if value.get("active") and value.get("deadline", 0) <= now:
        raise ValueError("Control expired")
    return value


def _deliver(store, row):
    groups = {}
    for event in row.payload:
        stream = event.get("diagnostic_session_id", "baseline")
        groups.setdefault(stream, []).append(event)
    for stream, events in groups.items():
        identity = str(uuid5(NAMESPACE_URL, f"{row.instance_id}/{row.batch_id}/{stream}"))
        store.write_remote_batch(identity, events, stream=stream)
    row.delivered = True
    row.payload = []
    row.payload_bytes = 0


def drain_ingest(store, *, limit: int = 25, now: datetime | None = None):
    """Retry durable admissions after transient spool errors, including restart."""
    now = now or datetime.now(timezone.utc)
    counts = {"delivered": 0, "deferred": 0}
    with _lock:
        with session_scope() as session:
            ids = session.execute(select(DiagnosticIngestBatch.instance_id,
                DiagnosticIngestBatch.batch_id).where(
                DiagnosticIngestBatch.delivered.is_(False), DiagnosticIngestBatch.next_attempt_at <= now,
            ).order_by(DiagnosticIngestBatch.next_attempt_at).limit(limit)).all()
        for identity in ids:
            try:
                with session_scope() as session:
                    row = session.get(DiagnosticIngestBatch, tuple(identity))
                    if row is None or row.delivered:
                        continue
                    _deliver(store, row)
                counts["delivered"] += 1
            except (OSError, StorageQuotaError, ValueError):
                with session_scope() as session:
                    row = session.get(DiagnosticIngestBatch, tuple(identity))
                    if row is not None and not row.delivered:
                        row.next_attempt_at = now + timedelta(seconds=60)
                counts["deferred"] += 1
    return counts


def ingest_events(batch: DiagnosticBatch, *, store, baseline_enabled: bool = True) -> IngestReceipt:
    encoded = json.dumps(batch.events, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    if len(encoded) > MAX_BATCH_BYTES:
        raise ValueError("Batch too large")
    digest = sha256(encoded).hexdigest()
    with _lock, session_scope() as session:
        row = session.get(DiagnosticIngestBatch, (batch.instance_id, batch.batch_id))
        duplicate = row is not None
        if row is not None:
            if row.payload_hash != digest:
                raise IngestConflict("Batch identity conflict")
        else:
            control = None
            for event in batch.events:
                capture = event.get("diagnostic_session_id")
                if capture:
                    control = control or current_control(store)
                    if not control.get("active") or control.get("session_id") != capture:
                        raise ValueError("Capture unavailable")
                elif not baseline_enabled:
                    raise ValueError("Baseline disabled")
            session.execute(delete(DiagnosticIngestBatch).where(
                DiagnosticIngestBatch.delivered.is_(True),
                DiagnosticIngestBatch.created_at < datetime.now(timezone.utc) - timedelta(days=7),
            ))
            count, size = session.execute(select(func.count(), func.coalesce(func.sum(
                DiagnosticIngestBatch.payload_bytes), 0)).select_from(DiagnosticIngestBatch)).one()
            if count >= 10000 or size + len(encoded) > 20 * 1024 * 1024:
                raise StorageQuotaError("Diagnostic inbox full")
            row = DiagnosticIngestBatch(instance_id=batch.instance_id, batch_id=batch.batch_id,
                payload_hash=digest, payload=batch.events, payload_bytes=len(encoded),
                accepted=len(batch.events), delivered=False)
            session.add(row)
            session.flush()
        receipt = IngestReceipt(accepted=row.accepted, duplicate=duplicate)
    # Commit admission BEFORE touching spool; the 200 receipt means durable inbox,
    # not necessarily segment delivery. A later maintenance pass retries failures.
    try:
        drain_ingest(store)
    except (OSError, StorageQuotaError, ValueError):
        pass
    return receipt
