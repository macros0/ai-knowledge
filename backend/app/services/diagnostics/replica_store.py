"""Bounded immutable SQL segments and disposable per-instance read scratch."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models import DiagnosticIngestBatch, DiagnosticInstance, DiagnosticReplicaState, DiagnosticSegment, DiagnosticSession
from app.db.session import session_scope
from app.services.work.store import _now, _utc
from .read_view import ReadViewLease, SegmentDescriptor
from .paths import assert_no_links, canonical_root
from .sanitize import encode_event, valid_uuid
from .schema import EventFilter
from .store import BatchWriteResult, DiagnosticStore, StorageQuotaError, ValidatedEvent

MAX_SEGMENTS = 16384
COUNTERS = {"dropped", "invalid", "expired_queue", "storage_errors", "drain_timeouts",
    "written", "aggregated_success", "sampled_out_traces", "sampled_out_slow"}


def lock_budget(session, limits):
    values = {key: getattr(limits, key) for key in ("baseline_bytes", "session_bytes", "frontend_bytes",
        "backend_bytes", "baseline_seconds", "capture_seconds")}
    fingerprint = sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    session.execute(insert(DiagnosticReplicaState).values(id=1, policy_hash=fingerprint, evicted_segments=0)
        .on_conflict_do_nothing(index_elements=[DiagnosticReplicaState.id]))
    session.execute(update(DiagnosticReplicaState).where(DiagnosticReplicaState.id == 1).values(id=1))
    row = session.get(DiagnosticReplicaState, 1, populate_existing=True)
    if row.policy_hash != fingerprint:
        if (session.scalar(select(DiagnosticSegment.id).limit(1)) is not None
                or session.scalar(select(DiagnosticSession.id).where(DiagnosticSession.active_slot == 1)) is not None):
            raise ValueError("Diagnostic limits differ from the active shared policy")
        row.policy_hash = fingerprint
    return row


def _instance(session, instance_id, role, now, counters=None):
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    session.execute(insert(DiagnosticInstance).values(id=instance_id, role=role,
        created_at=now, heartbeat_at=now, counters={}).on_conflict_do_nothing(index_elements=[DiagnosticInstance.id]))
    row = session.get(DiagnosticInstance, instance_id, populate_existing=True)
    if row.role != role:
        raise ValueError("Diagnostic instance role conflict")
    row.heartbeat_at = now
    row.closed_at = None
    if counters is not None:
        row.counters = {key: value for key, value in counters.items()
            if key in COUNTERS and type(value) is int and 0 <= value <= 1_000_000_000}


class DiagnosticSqlRepository:
    """Shared lifecycle operations do not create a recorder or local directory."""
    def __init__(self, settings, *, limits=None):
        self.settings = settings
        self.limits = limits or settings.diagnostics_limits()

    def _publish(self, session, encoded, *, stream, identity, instance_id, component, admitted_at=None):
        budget = lock_budget(session, self.limits)
        if stream != "baseline" and valid_uuid(stream) is None:
            raise ValueError("Invalid diagnostic stream")
        size = len(encoded)
        if not 0 < size <= min(65536, self.limits.segment_bytes):
            raise StorageQuotaError("Diagnostic segment too large")
        events = [json.loads(line) for line in encoded.splitlines(keepends=True)]
        if b"".join(encode_event(event) for event in events) != encoded or any(
                event.get("diagnostic_session_id", "baseline") != stream or
                event.get("component") not in ({"frontend"} if component == "frontend" else {"backend", "browser"}) for event in events):
            raise ValueError("Invalid diagnostic segment")
        digest = sha256(encoded).hexdigest()
        existing = session.get(DiagnosticSegment, identity)
        if existing is not None:
            if (existing.sha256, existing.size, existing.instance_id, existing.stream, existing.component) != (
                    digest, size, instance_id, stream, component):
                raise ValueError("Diagnostic segment identity conflict")
            return
        now = _now(session)
        expires_at = now + timedelta(seconds=self.limits.baseline_seconds)
        stale = list(session.scalars(select(DiagnosticSegment.id).where(DiagnosticSegment.expires_at <= now)
            .order_by(DiagnosticSegment.expires_at, DiagnosticSegment.id).limit(100)))
        if stale:
            session.execute(delete(DiagnosticSegment).where(DiagnosticSegment.id.in_(stale)))
        if stream != "baseline":
            capture = session.scalar(select(DiagnosticSession).where(DiagnosticSession.id == stream).with_for_update())
            active = (capture is not None and capture.status == "active" and capture.active_slot == 1 and _utc(capture.expires_at) > now)
            admitted = (capture is not None and component == "frontend" and admitted_at is not None
                and capture.status in {"active", "stopped"}
                and _utc(capture.created_at) <= _utc(admitted_at) <= min(_utc(capture.expires_at),
                    _utc(capture.stopped_at) if capture.stopped_at else _utc(capture.expires_at)))
            if not (active or admitted):
                raise ValueError("Capture no longer active")
            expires_at = _utc(capture.stopped_at or capture.expires_at) + timedelta(seconds=self.limits.capture_seconds)
            if expires_at <= now:
                raise ValueError("Capture retention expired")
            if capture.bytes_written + size > self.limits.session_bytes:
                raise StorageQuotaError("Diagnostic capture full")
            capture.bytes_written += size
        else:
            used = session.scalar(select(func.coalesce(func.sum(DiagnosticSegment.size), 0)).where(DiagnosticSegment.stream == "baseline"))
            if used + size > self.limits.baseline_bytes:
                old = list(session.scalars(select(DiagnosticSegment).where(DiagnosticSegment.stream == "baseline")
                    .order_by(DiagnosticSegment.created_at, DiagnosticSegment.id).limit(100)))
                for row in old:
                    session.delete(row)
                    used -= row.size
                    budget.evicted_segments += 1
                    if used + size <= self.limits.baseline_bytes:
                        break
                session.flush()
                if used + size > self.limits.baseline_bytes:
                    raise StorageQuotaError("Diagnostic baseline full")
        total, frontend, count = session.execute(select(func.coalesce(func.sum(DiagnosticSegment.size), 0),
            func.coalesce(func.sum(DiagnosticSegment.size).filter(DiagnosticSegment.component == "frontend"), 0),
            func.count()).select_from(DiagnosticSegment)).one()
        if total + size > self.limits.backend_bytes + self.limits.frontend_bytes or count >= MAX_SEGMENTS:
            raise StorageQuotaError("Shared diagnostic storage full")
        if component == "frontend" and frontend + size > self.limits.frontend_bytes:
            raise StorageQuotaError("Frontend diagnostic storage full")
        _instance(session, instance_id, "frontend" if component == "frontend" else self.settings.app_role, now)
        stamps = [datetime.fromisoformat(item["timestamp_utc"]).astimezone(timezone.utc) for item in events]
        session.add(DiagnosticSegment(id=identity, instance_id=instance_id, stream=stream, component=component,
            payload=encoded.decode(), sha256=digest, size=size, event_count=len(events), first_at=min(stamps),
            last_at=max(stamps), created_at=now, expires_at=expires_at))
        session.flush()

    def publish_remote(self, session, identity, events, *, stream, instance_id, admitted_at=None):
        chunks, pending = [], bytearray()
        for item in events:
            encoded = encode_event(item)
            if pending and len(pending) + len(encoded) > min(65536, self.limits.segment_bytes):
                chunks.append(bytes(pending))
                pending.clear()
            pending.extend(encoded)
        if pending:
            chunks.append(bytes(pending))
        for ordinal, data in enumerate(chunks):
            self._publish(session, data, stream=stream, identity=str(uuid5(NAMESPACE_URL, f"{identity}/{ordinal}")),
                instance_id=instance_id, component="frontend", admitted_at=admitted_at)

    def coverage(self, requested_from=None):
        counts = {"recorder_" + key: 0 for key in COUNTERS}
        gaps, unknown, frontend = [], False, False
        with session_scope() as session:
            now = _now(session)
            rows = list(session.scalars(select(DiagnosticInstance).order_by(DiagnosticInstance.id).limit(10001)))
            if len(rows) > 10000:
                gaps.append("recorder_status_unavailable")
            for row in rows[:10000]:
                frontend |= row.role == "frontend"
                values = row.counters or {}
                unknown |= (not values or requested_from is None or _utc(row.created_at) > requested_from)
                for key in COUNTERS:
                    value = values.get(key, 0)
                    if type(value) is int and 0 <= value <= 1_000_000_000:
                        counts["recorder_" + key] += value
                    else:
                        unknown = True
                if any(values.get(key, 0) for key in ("dropped", "invalid", "expired_queue", "storage_errors", "drain_timeouts")):
                    gaps.append("recorder_loss")
                if row.closed_at is None and _utc(row.heartbeat_at) < now - timedelta(seconds=20):
                    # Frontend batches do not carry an idle heartbeat; silence
                    # cannot establish that its process or recorder was lost.
                    gaps.append("frontend_status_unavailable" if row.role == "frontend" else "recorder_loss")
            state = session.get(DiagnosticReplicaState, 1)
            counts["diagnostic_evicted_segments"] = state.evicted_segments if state else 0
            if counts["diagnostic_evicted_segments"]:
                gaps.append("recorder_loss")
        counts["recorder_counters_unknown"] = int(unknown or not rows)
        counts["frontend_counters_unknown"] = 1  # The bounded frontend wire has no historical counters.
        if not frontend:
            gaps.append("frontend_unavailable")
        return counts, tuple(dict.fromkeys(gaps))

    def maintain(self):
        result = {"segments": 0, "instances": 0, "captures": 0, "receipts": 0, "more": False}
        with session_scope() as session:
            lock_budget(session, self.limits)
            now = _now(session)
            ids = list(session.scalars(select(DiagnosticSegment.id).where(DiagnosticSegment.expires_at <= now)
                .order_by(DiagnosticSegment.expires_at, DiagnosticSegment.id).limit(100)))
            if ids:
                session.execute(delete(DiagnosticSegment).where(DiagnosticSegment.id.in_(ids)))
            result["segments"] = len(ids)
            result["more"] = len(ids) == 100
            pending = list(session.scalars(select(DiagnosticIngestBatch).where(DiagnosticIngestBatch.delivered.is_(False))
                .order_by(DiagnosticIngestBatch.created_at).limit(100)))
            for row in pending:
                expiry = _utc(row.created_at) + timedelta(seconds=self.limits.baseline_seconds)
                for stream in {item.get("diagnostic_session_id") for item in row.payload if isinstance(item, dict)} - {None}:
                    capture = session.get(DiagnosticSession, stream)
                    if capture is not None:
                        expiry = min(expiry, _utc(capture.stopped_at or capture.expires_at) + timedelta(seconds=self.limits.capture_seconds))
                if expiry <= now:
                    _instance(session, row.instance_id, "frontend", now)
                    instance = session.get(DiagnosticInstance, row.instance_id)
                    instance.counters = {**instance.counters, "expired_queue": min(1_000_000_000,
                        instance.counters.get("expired_queue", 0) + row.accepted)}
                    row.delivered, row.payload, row.payload_bytes = True, [], 0
            session.flush()
            receipts = session.execute(select(DiagnosticIngestBatch.instance_id, DiagnosticIngestBatch.batch_id).where(
                DiagnosticIngestBatch.delivered.is_(True), DiagnosticIngestBatch.created_at < now - timedelta(days=7))
                .order_by(DiagnosticIngestBatch.created_at).limit(100)).all()
            for identity in receipts:
                session.delete(session.get(DiagnosticIngestBatch, tuple(identity)))
            session.flush()
            result["receipts"] = len(receipts)
            instances = list(session.scalars(select(DiagnosticInstance).where(
                DiagnosticInstance.heartbeat_at < now - timedelta(days=7),
                ~select(DiagnosticSegment.id).where(DiagnosticSegment.instance_id == DiagnosticInstance.id).exists(),
                ~select(DiagnosticIngestBatch.batch_id).where(DiagnosticIngestBatch.instance_id == DiagnosticInstance.id).exists())
                .order_by(DiagnosticInstance.id).limit(100)))
            for row in instances:
                session.delete(row)
            result["instances"] = len(instances)
            if session.scalar(select(DiagnosticIngestBatch.batch_id).where(DiagnosticIngestBatch.delivered.is_(False)).limit(1)) is None:
                captures = list(session.scalars(select(DiagnosticSession).where(DiagnosticSession.status == "stopped",
                    DiagnosticSession.stopped_at < now - timedelta(days=7),
                    ~select(DiagnosticSegment.id).where(DiagnosticSegment.stream == DiagnosticSession.id).exists())
                    .order_by(DiagnosticSession.id).limit(100)))
                for row in captures:
                    if not row.audit_receipts:
                        session.delete(row)
                        result["captures"] += 1
        return result

class ReplicaDiagnosticStore(DiagnosticSqlRepository, DiagnosticStore):
    def __init__(self, settings, *, instance_id, limits=None):
        if valid_uuid(instance_id) is None:
            raise ValueError("Invalid diagnostic instance")
        DiagnosticSqlRepository.__init__(self, settings, limits=limits)
        self.instance_id = instance_id
        self.replica_settings, self.replica_boot_id = settings, instance_id
        self.durable_segments = True
        legacy_root = canonical_root(settings.diagnostics_dir)
        pending = legacy_root / "control/deferred.jsonl"
        assert_no_links(pending, legacy_root)
        if pending.exists() and pending.stat().st_size:
            raise ValueError("Legacy diagnostic control requires verified adoption")
        instance_root = settings.diagnostics_dir / "instances" / instance_id
        assert_no_links(instance_root, legacy_root)
        if instance_root.exists():
            raise ValueError("Diagnostic instance already exists")
        DiagnosticStore.__init__(self, instance_root, self.limits)
        from .scratch import MARKER, marker_value
        self.write_bytes(self.root / MARKER, json.dumps(marker_value(instance_id)).encode())

    def heartbeat(self, counters=None):
        with session_scope() as session:
            lock_budget(session, self.limits)
            _instance(session, self.instance_id, self.settings.app_role, _now(session), counters)

    def append_batch(self, events, *, stream):
        if type(events) is not tuple or any(type(item) is not ValidatedEvent for item in events):
            return BatchWriteResult(0, 0, "invalid")
        count = size = 0
        try:
            position = 0
            while position < len(events):
                selected, data = [], bytearray()
                for item in events[position:]:
                    if len(data) + len(item.encoded) > min(65536, self.limits.segment_bytes):
                        break
                    selected.append(item)
                    data.extend(item.encoded)
                if not selected:
                    raise ValueError("Diagnostic event too large")
                with session_scope() as session:
                    self._publish(session, bytes(data), stream=stream, identity=str(uuid4()),
                        instance_id=self.instance_id, component="backend")
                count += len(selected)
                size += len(data)
                position += len(selected)
            return BatchWriteResult(count, size)
        except StorageQuotaError:
            return BatchWriteResult(count, size, "size_limit")
        except Exception:
            return BatchWriteResult(count, size, "storage_error")

    def write_remote_batch(self, identity, events, *, stream):
        with session_scope() as session:
            self.publish_remote(session, identity, events, stream=stream, instance_id=identity)

    def pin_read_view(self, filter: EventFilter, cutoff_at):
        if cutoff_at.tzinfo is None:
            raise ValueError("Cutoff must be timezone-aware")
        directory = self.safe_path(self.root / "inputs" / str(uuid4()))
        directory.mkdir(parents=True)
        descriptors, gaps = [], []
        try:
            with session_scope() as session:
                now = _now(session)
                query = select(DiagnosticSegment).where(DiagnosticSegment.created_at <= cutoff_at,
                    DiagnosticSegment.expires_at > now)
                if filter.session_id:
                    query = query.where(DiagnosticSegment.stream.in_(("baseline", filter.session_id)))
                if filter.from_utc:
                    query = query.where(DiagnosticSegment.last_at >= filter.from_utc)
                if filter.to_utc:
                    query = query.where(DiagnosticSegment.first_at <= filter.to_utc)
                rows = session.scalars(query.order_by(DiagnosticSegment.created_at, DiagnosticSegment.id)
                    .limit(MAX_SEGMENTS + 1).execution_options(yield_per=25))
                for row in rows:
                    if len(descriptors) >= MAX_SEGMENTS:
                        gaps.append("segment_unavailable")
                        break
                    try:
                        data = row.payload.encode()
                        if len(data) != row.size or sha256(data).hexdigest() != row.sha256:
                            raise ValueError("Diagnostic segment changed")
                        for line in data.splitlines(keepends=True):
                            self.validate_event(line)
                        path = self.safe_path(directory / "events" / row.stream / (row.id + ".jsonl"))
                        path.parent.mkdir(parents=True, exist_ok=True)
                        self.write_bytes(path, data)
                        stat = path.stat()
                        descriptors.append(SegmentDescriptor(path, row.stream, (stat.st_dev, stat.st_ino),
                            row.size, _utc(row.expires_at).timestamp(), stat.st_mtime_ns,
                            source="frontend" if row.component == "frontend" else "backend", root=directory))
                    except (OSError, ValueError, StorageQuotaError):
                        gaps.append("segment_unavailable")
            return ReadViewLease(self, tuple(descriptors), filter, cutoff_at, initial_gaps=tuple(dict.fromkeys(gaps)),
                input_root=directory, cleanup=lambda: self.delete_tree(directory))
        except BaseException:
            self.delete_tree(directory)
            raise
