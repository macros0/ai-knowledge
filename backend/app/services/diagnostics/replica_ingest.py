"""Shared admission and atomic SQL segment/receipt publication for frontend batches."""
from datetime import timedelta
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import DiagnosticIngestBatch, DiagnosticSession
from app.db.session import session_scope
from app.services.work.store import _now, _utc
from .replica_store import lock_budget
from .store import StorageQuotaError


def ingest(batch, *, store, baseline_enabled):
    from .ingest import IngestConflict, IngestReceipt, MAX_BATCH_BYTES
    encoded = json.dumps(batch.events, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    if len(encoded) > MAX_BATCH_BYTES:
        raise ValueError("Batch too large")
    digest = sha256(encoded).hexdigest()
    with session_scope() as session:
        lock_budget(session, store.limits)
        now = _now(session)
        row = session.get(DiagnosticIngestBatch, (batch.instance_id, batch.batch_id))
        duplicate = row is not None
        if row is not None:
            if row.payload_hash != digest:
                raise IngestConflict("Batch identity conflict")
        else:
            for event in batch.events:
                capture_id = event.get("diagnostic_session_id")
                if capture_id:
                    capture = session.scalar(select(DiagnosticSession).where(DiagnosticSession.id == capture_id).with_for_update())
                    if (capture is None or capture.active_slot != 1 or capture.status != "active"
                            or capture.scope not in {"system", "interface"} or _utc(capture.expires_at) <= now):
                        raise ValueError("Capture unavailable")
                elif not baseline_enabled:
                    raise ValueError("Baseline disabled")
            stale = session.execute(select(DiagnosticIngestBatch.instance_id, DiagnosticIngestBatch.batch_id).where(
                DiagnosticIngestBatch.delivered.is_(True), DiagnosticIngestBatch.created_at < now - timedelta(days=7))
                .order_by(DiagnosticIngestBatch.created_at).limit(100)).all()
            if stale:
                session.execute(delete(DiagnosticIngestBatch).where(tuple_(
                    DiagnosticIngestBatch.instance_id, DiagnosticIngestBatch.batch_id).in_(stale)))
            count, size = session.execute(select(func.count(), func.coalesce(func.sum(DiagnosticIngestBatch.payload_bytes), 0))
                .select_from(DiagnosticIngestBatch)).one()
            if count >= 10000 or size + len(encoded) > 20 * 1024 * 1024:
                raise StorageQuotaError("Diagnostic inbox full")
            row = DiagnosticIngestBatch(instance_id=batch.instance_id, batch_id=batch.batch_id, payload_hash=digest,
                payload=batch.events, payload_bytes=len(encoded), accepted=len(batch.events), delivered=False,
                next_attempt_at=now, created_at=now)
            session.add(row)
            session.flush()
        receipt = IngestReceipt(accepted=row.accepted, duplicate=duplicate)
    try:
        drain(store)
    except (OSError, ValueError, StorageQuotaError, SQLAlchemyError):
        pass  # The receipt already names the committed SQL inbox admission.
    return receipt


def drain(store, *, limit=25):
    from .ingest import DiagnosticBatch
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid diagnostic drain limit")
    counts = {"delivered": 0, "deferred": 0}
    with session_scope() as session:
        now = _now(session)
        identities = session.execute(select(DiagnosticIngestBatch.instance_id, DiagnosticIngestBatch.batch_id).where(
            DiagnosticIngestBatch.delivered.is_(False), DiagnosticIngestBatch.next_attempt_at <= now)
            .order_by(DiagnosticIngestBatch.next_attempt_at, DiagnosticIngestBatch.instance_id, DiagnosticIngestBatch.batch_id)
            .limit(limit)).all()
    for identity in identities:
        try:
            with session_scope() as session:
                lock_budget(session, store.limits)
                row = session.scalar(select(DiagnosticIngestBatch).where(
                    DiagnosticIngestBatch.instance_id == identity[0], DiagnosticIngestBatch.batch_id == identity[1]).with_for_update())
                if row is None or row.delivered or _utc(row.next_attempt_at) > _now(session):
                    continue
                batch = DiagnosticBatch(instance_id=row.instance_id, batch_id=row.batch_id, events=row.payload)
                digest = sha256(json.dumps(batch.events, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
                if digest != row.payload_hash:
                    raise ValueError("Diagnostic inbox changed")
                groups = {}
                for event in batch.events:
                    groups.setdefault(event.get("diagnostic_session_id", "baseline"), []).append(event)
                for stream, events in groups.items():
                    segment_id = str(uuid5(NAMESPACE_URL, f"{row.instance_id}/{row.batch_id}/{stream}"))
                    store.publish_remote(session, segment_id, events, stream=stream, instance_id=row.instance_id,
                        admitted_at=row.created_at)
                row.delivered, row.payload, row.payload_bytes = True, [], 0
            counts["delivered"] += 1
        except (OSError, ValueError, StorageQuotaError, SQLAlchemyError):
            counts["deferred"] += 1
            try:
                with session_scope() as session:
                    row = session.scalar(select(DiagnosticIngestBatch).where(
                        DiagnosticIngestBatch.instance_id == identity[0], DiagnosticIngestBatch.batch_id == identity[1]).with_for_update())
                    if row is not None and not row.delivered:
                        row.next_attempt_at = _now(session) + timedelta(seconds=60)
            except (OSError, SQLAlchemyError):
                pass
    return counts
