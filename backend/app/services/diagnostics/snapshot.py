"""Immutable, bounded diagnostic input with no raw application data."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import os
import json

from sqlalchemy import select

from app.db.models import DiagnosticSession, Document, Job
from app.db.session import session_scope
from app.models.diagnostics import BundleRequest
from app.services import health
from .schema import EventFilter
from .sanitize import _encode_validated_event, sanitize_event, valid_uuid
from .schema import MAX_EVENT_BYTES
from .store import SnapshotLease, _is_link


_DEPENDENCIES = frozenset({"database", "qdrant", "llm", "embeddings", "pdf"})
_STATUSES = frozenset({"ok", "down", "degraded", "rate_limited", "unknown"})
_OP_STATUSES = frozenset({"queued", "running", "paused", "done", "failed", "cancelled", "uploaded", "processing"})


def _safe_runtime(value):
    if not isinstance(value, dict):
        return {"status": "unknown", "dependencies": {}}
    status = value.get("status")
    dependencies = value.get("dependencies")
    return {
        "status": status if status in _STATUSES else "unknown",
        "dependencies": {
            key: {"status": item.get("status") if isinstance(item, dict) and item.get("status") in _STATUSES else "unknown"}
            for key, item in (dependencies.items() if isinstance(dependencies, dict) else ())
            if key in _DEPENDENCIES
        },
    }


def _safe_operations(value):
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        identity = item.get("id")
        status = item.get("status")
        valid_identity = (isinstance(identity, str) and (
            (kind == "document" and re.fullmatch(r"[a-f0-9]{16,32}", identity)) or
            (kind == "job" and re.fullmatch(r"[1-9][0-9]{0,18}", identity)
             and int(identity) <= 2**63 - 1)
        ))
        if not valid_identity:
            continue
        result.append({"kind": kind, "id": identity,
                       "status": status if status in _OP_STATUSES else "unknown"})
    return result


def _metadata(request: BundleRequest):
    # A support bundle must never trigger new dependency probes.
    runtime = health.get_cached_diagnostic_status()
    with session_scope() as db:
        doc_id = request.doc_id
        if request.session_id is not None:
            session = db.get(DiagnosticSession, str(request.session_id))
            if session is None:
                return runtime, []
            if session.scope == "document":
                doc_id = session.doc_id
        if doc_id:
            docs = db.execute(select(Document.id, Document.status).where(Document.id == doc_id)).all()
            jobs = []
        else:
            docs = db.execute(select(Document.id, Document.status)
                              .order_by(Document.created_at.desc(), Document.id.desc()).limit(50)).all()
            jobs = db.execute(select(Job.id, Job.status)
                              .order_by(Job.created_at.desc(), Job.id.desc()).limit(50)).all()
    operations = [{"kind": "document", "id": str(doc_id), "status": status} for doc_id, status in docs]
    operations.extend({"kind": "job", "id": str(job_id), "status": status} for job_id, status in jobs)
    return runtime, operations


def _external_safe(path: Path, root: Path):
    path = Path(os.path.abspath(path))
    root = Path(os.path.abspath(root))
    if not path.is_relative_to(root):
        raise ValueError("Frontend diagnostic path escapes root")
    for part in (path, *path.parents):
        if _is_link(part):
            raise ValueError("Frontend diagnostic path contains a link")
    return path


def _frontend_copy(root: Path, store, lease, filt, cutoff_at):
    """Copy only validated lines from stable files; a vanishing file is a gap."""
    root = _external_safe(root, root)
    events_dir = _external_safe(root / "events", root)
    if not events_dir.is_dir():
        raise FileNotFoundError("Frontend diagnostic spool unavailable")
    target = store.safe_path(lease.directory / "frontend.jsonl")
    counts = {"invalid": 0, "truncated": 0, "frontend_events": 0}
    copied = 0
    for stream_dir in sorted(events_dir.iterdir()):
        _external_safe(stream_dir, root)
        if not stream_dir.is_dir() or (stream_dir.name != "baseline" and valid_uuid(stream_dir.name) is None):
            counts["invalid"] += 1
            continue
        for source in sorted(stream_dir.glob("*.jsonl")):
            _external_safe(source, root)
            if not re.fullmatch(r"\d{13}_[a-f0-9-]{36}\.jsonl", source.name):
                counts["invalid"] += 1
                continue
            before = source.stat()
            pending = bytearray()
            with source.open("rb") as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_ino, opened.st_dev) != (before.st_ino, before.st_dev) or opened.st_size < before.st_size:
                    raise OSError("Frontend segment changed during open")
                remaining = before.st_size
                while remaining > 0:
                    line = handle.readline(min(MAX_EVENT_BYTES + 1, remaining))
                    remaining -= len(line)
                    if not line:
                        raise OSError("Frontend segment shrank during copy")
                    if not line.endswith(b"\n"):
                        counts["truncated"] += 1
                        while remaining > 0 and len(line) > MAX_EVENT_BYTES and not line.endswith(b"\n"):
                            line = handle.readline(min(MAX_EVENT_BYTES + 1, remaining))
                            remaining -= len(line)
                            if not line:
                                raise OSError("Frontend segment shrank during copy")
                        continue
                    try:
                        event = sanitize_event(json.loads(line))
                    except (ValueError, TypeError, UnicodeDecodeError):
                        event = None
                    if event is None or event.get("component") != "frontend":
                        counts["invalid"] += 1
                        continue
                    stamp = datetime.fromisoformat(event["timestamp_utc"]).astimezone(timezone.utc)
                    if stamp > cutoff_at or not filt.matches(event):
                        continue
                    age = store.limits.baseline_seconds if stream_dir.name == "baseline" else store.limits.capture_seconds + 3600
                    if stamp.timestamp() < cutoff_at.timestamp() - age:
                        continue
                    encoded = _encode_validated_event(event)
                    copied += len(encoded)
                    if copied > store.limits.frontend_bytes:
                        raise OSError("Frontend snapshot exceeds component budget")
                    pending.extend(encoded)
                    if len(pending) >= 65536:
                        store.write_bytes(target, bytes(pending), append=True)
                        pending.clear()
                    counts["frontend_events"] += 1
            after = source.stat()
            if (after.st_ino, after.st_dev) != (before.st_ino, before.st_dev) or after.st_size < before.st_size:
                raise OSError("Frontend segment changed during copy")
            if pending:
                store.write_bytes(target, bytes(pending), append=True)
    return (target,) if target.exists() else (), counts


def _frontend_status(root: Path) -> tuple[dict, str | None]:
    """Read only bounded counters from Node's atomically replaced status file."""
    source = _external_safe(root / "status.json", root)
    before = source.stat()
    if before.st_size > 4096:
        raise ValueError("Oversized frontend status")
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        if (opened.st_ino, opened.st_dev, opened.st_size) != (before.st_ino, before.st_dev, before.st_size):
            raise OSError("Frontend status changed during open")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            raw = handle.read(4097)
            after = os.fstat(handle.fileno())
    finally:
        if fd >= 0:
            os.close(fd)
    if len(raw) > 4096 or (after.st_ino, after.st_dev, after.st_size) != (
            before.st_ino, before.st_dev, before.st_size):
        raise OSError("Frontend status changed during read")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema_version") != 1 or valid_uuid(value.get("boot_id")) is None:
        raise ValueError("Invalid frontend status")
    if type(value.get("storage_degraded")) is not bool or type(value.get("running")) is not bool:
        raise ValueError("Invalid frontend storage status")
    counters = {}
    for key in ("dropped", "invalid", "expired_queue", "queued"):
        number = value.get(key)
        if type(number) is not int or not 0 <= number <= 1_000_000_000:
            raise ValueError("Invalid frontend counter")
        counters[f"frontend_{key}"] = number
    if datetime.now(timezone.utc).timestamp() - before.st_mtime > 120:
        return counters, "frontend_status_stale"
    if not value["running"] or value["storage_degraded"] or any(counters.values()):
        return counters, "frontend_loss"
    return counters, None


@dataclass
class BundleSnapshot:
    store: object
    lease: SnapshotLease
    cutoff_at: datetime
    paths: tuple[Path, ...]
    runtime: dict
    operations: list[dict]
    counts: dict
    partial: bool
    gaps: tuple[str, ...]

    def release(self):
        self.lease.release()


def collect_snapshot(request: BundleRequest, *, cutoff_at: datetime, store,
                     metadata_provider=None, frontend_root: Path | None = None,
                     recorder_status=None, recorder_barrier=None) -> BundleSnapshot:
    if cutoff_at.tzinfo is None:
        raise ValueError("Cutoff must be timezone-aware")
    cutoff_at = cutoff_at.astimezone(timezone.utc)
    filt = EventFilter(
        from_utc=request.from_utc, to_utc=min(request.to_utc, cutoff_at) if request.to_utc else cutoff_at,
        session_id=str(request.session_id) if request.session_id else None,
        request_id=str(request.request_id) if request.request_id else None,
        operation_id=str(request.operation_id) if request.operation_id else None,
        doc_id=request.doc_id,
    )
    barrier_ok = True
    if recorder_barrier is not None:
        try:
            barrier_ok = recorder_barrier() is True
        except Exception:
            barrier_ok = False
    lease = store.snapshot(filt, cutoff_at)
    gaps = [] if barrier_ok else ["recorder_loss"]
    paths = lease.paths
    counts = dict(lease.counts)
    provider = metadata_provider or _metadata
    try:
        if frontend_root is not None:
            try:
                frontend_paths, frontend_counts = _frontend_copy(frontend_root, store, lease, filt, cutoff_at)
                paths += frontend_paths
                for key, value in frontend_counts.items():
                    counts[key] = counts.get(key, 0) + value
                counts["events"] = counts.get("events", 0) + frontend_counts["frontend_events"]
                try:
                    status_counts, status_gap = _frontend_status(frontend_root)
                    counts.update(status_counts)
                    if status_gap:
                        gaps.append(status_gap)
                except (OSError, ValueError, TypeError):
                    gaps.append("frontend_status_unavailable")
            except (OSError, ValueError):
                gaps.append("frontend_unavailable")
                # Partial copied lines remain valid; never claim complete coverage.
                if (lease.directory / "frontend.jsonl").exists():
                    paths += (lease.directory / "frontend.jsonl",)
        else:
            gaps.append("frontend_unavailable")
        try:
            runtime_raw, operations_raw = provider(request)
            runtime = _safe_runtime(runtime_raw)
            operations = _safe_operations(operations_raw)
        except Exception:
            runtime = {"status": "unknown", "dependencies": {}}
            operations = []
            gaps.append("metadata_unavailable")
        if recorder_status is not None:
            try:
                status = recorder_status()
                if not isinstance(status, dict):
                    raise ValueError("Invalid recorder status")
                sampled = status.get("sampled_success", 0)
                if type(sampled) is not int or not 0 <= sampled <= 1_000_000_000:
                    raise ValueError("Invalid recorder counter")
                counts["recorder_sampled_success"] = sampled
                for key in ("dropped", "invalid", "expired_queue", "storage_errors", "drain_timeouts"):
                    value = status.get(key, 0)
                    if type(value) is not int or not 0 <= value <= 1_000_000_000:
                        raise ValueError("Invalid recorder counter")
                    counts[f"recorder_{key}"] = value
                if any(counts[f"recorder_{key}"] for key in (
                        "dropped", "invalid", "expired_queue", "storage_errors", "drain_timeouts")):
                    if "recorder_loss" not in gaps:
                        gaps.append("recorder_loss")
            except Exception:
                gaps.append("recorder_status_unavailable")
        if counts.get("invalid", 0) or counts.get("truncated", 0):
            gaps.append("invalid_input")
        return BundleSnapshot(store, lease, cutoff_at, paths, runtime, operations,
                              counts, bool(gaps), tuple(gaps))
    except Exception:
        lease.release()
        raise
