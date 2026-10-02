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
from .policy import safe_policy_snapshot
from .sanitize import _encode_validated_event, sanitize_event, valid_uuid
from .schema import MAX_EVENT_BYTES
from .paths import assert_no_links
from .store import SnapshotLease
from .read_view import attach_frontend
from .prepare import prepare_in_child
from .safe_metadata import _safe_operations, _safe_runtime


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
    with session_scope() as db:
        if request.session_id is not None:
            rows = [row for row in [db.get(DiagnosticSession, str(request.session_id))] if row is not None]
        else:
            rows = list(db.scalars(select(DiagnosticSession).where(
                DiagnosticSession.created_at <= request.to_utc,
                DiagnosticSession.expires_at >= request.from_utc,
            ).order_by(DiagnosticSession.created_at, DiagnosticSession.id).limit(200)))
    policies = []
    for row in rows:
        safe = safe_policy_snapshot(row.policy_snapshot)
        created_at = row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None else row.created_at
        policies.append({"session_id": row.id, "created_at_utc": created_at.astimezone(timezone.utc).isoformat(),
                         "capture_level": row.capture_level if safe else "legacy" if row.policy_version == 0 else "unknown",
                         "policy_version": row.policy_version if safe or row.policy_version == 0 else None,
                         "policy_snapshot": safe})
    return runtime, operations, policies


def _external_safe(path: Path, root: Path):
    path = Path(os.path.abspath(path))
    root = Path(os.path.abspath(root))
    if not path.is_relative_to(root):
        raise ValueError("Frontend diagnostic path escapes root")
    assert_no_links(path, root)
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


def _counter_history_unknown(started_at, requested_from: datetime | None) -> bool:
    try:
        started = datetime.fromisoformat(started_at)
        return (started.tzinfo is None or requested_from is None
                or requested_from.astimezone(timezone.utc) < started.astimezone(timezone.utc))
    except (TypeError, ValueError, AttributeError):
        return True


def _frontend_status(root: Path, requested_from: datetime | None = None) -> tuple[dict, str | None]:
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
    for key in ("sampled_out_slow", "intentional_aggregated", "intentional_sampled"):
        if key not in value:  # Existing frontend status remains readable.
            continue
        number = value[key]
        if type(number) is not int or not 0 <= number <= 1_000_000_000:
            raise ValueError("Invalid frontend sampling counter")
        counters[f"frontend_{key}"] = number
    counters["frontend_counters_unknown"] = int(_counter_history_unknown(
        value.get("started_at_utc"), requested_from))
    if datetime.now(timezone.utc).timestamp() - before.st_mtime > 120:
        return counters, "frontend_status_stale"
    if not value["running"] or value["storage_degraded"] or any(
            counters[f"frontend_{key}"] for key in ("dropped", "invalid", "expired_queue", "queued")):
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
    capture_policies: list[dict]
    normalized: bool = False

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
                    status_counts, status_gap = _frontend_status(frontend_root, request.from_utc)
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
            metadata = provider(request)
            runtime_raw, operations_raw = metadata[:2]
            policies_raw = metadata[2] if len(metadata) > 2 else []
            runtime = _safe_runtime(runtime_raw)
            operations = _safe_operations(operations_raw)
            policies = [item for item in policies_raw[:200] if isinstance(item, dict)] if isinstance(policies_raw, list) else []
        except Exception:
            runtime = {"status": "unknown", "dependencies": {}}
            operations = []
            policies = []
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
                counts["recorder_counters_unknown"] = int(_counter_history_unknown(
                    status.get("started_at_utc"), request.from_utc))
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
                              counts, bool(gaps), tuple(gaps), policies)
    except Exception:
        lease.release()
        raise


@dataclass
class PreparedLease:
    store: object
    view: object
    directory: Path
    released: bool = False

    def release(self):
        if self.released:
            return
        try:
            self.store.delete_tree(self.directory)
        finally:
            self.view.release()
            self.released = True


def collect_prepared_snapshot(request: BundleRequest, *, cutoff_at: datetime, store, job_id: str,
                              frontend_root: Path | None = None, metadata_provider=None,
                              recorder_status=None, recorder_barrier=None, on_child=None) -> BundleSnapshot:
    """Gather bounded metadata in parent; scan/filter source prefixes in child."""
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
    gaps = []
    if recorder_barrier is not None:
        try:
            if recorder_barrier() is not True:
                gaps.append("recorder_loss")
        except Exception:
            gaps.append("recorder_loss")
    view = store.pin_read_view(filt, cutoff_at)
    directory = store.root / "snapshots" / job_id
    try:
        if frontend_root is not None:
            attach_frontend(view, frontend_root)
            gaps.extend(view.initial_gaps)
        else:
            gaps.append("frontend_unavailable")
        provider = metadata_provider or _metadata
        try:
            metadata = provider(request)
            runtime_raw, operations_raw = metadata[:2]
            policies_raw = metadata[2] if len(metadata) > 2 else []
            runtime = _safe_runtime(runtime_raw)
            operations = _safe_operations(operations_raw)
            policies = [item for item in policies_raw[:200] if isinstance(item, dict)] if isinstance(policies_raw, list) else []
        except Exception:
            runtime = {"status": "unknown", "dependencies": {}}
            operations = []
            policies = []
            gaps.append("metadata_unavailable")
        coverage_from = request.from_utc
        if coverage_from is None and request.session_id is not None:
            for item in policies:
                if item.get("session_id") == str(request.session_id):
                    try:
                        coverage_from = datetime.fromisoformat(item["created_at_utc"])
                    except (KeyError, TypeError, ValueError):
                        pass
                    break
        prepared = prepare_in_child(store, view, job_id, frontend_root=frontend_root, on_child=on_child)
        counts = dict(prepared.counts)
        gaps.extend(prepared.gaps)
        if frontend_root is not None:
            try:
                front_counts, front_gap = _frontend_status(frontend_root, coverage_from)
                counts.update(front_counts)
                if front_gap:
                    gaps.append(front_gap)
            except (OSError, ValueError, TypeError):
                gaps.append("frontend_status_unavailable")
        if recorder_status is not None:
            try:
                status = recorder_status()
                counts["recorder_counters_unknown"] = int(_counter_history_unknown(
                    status.get("started_at_utc"), coverage_from))
                for key in ("dropped", "invalid", "expired_queue", "storage_errors", "drain_timeouts",
                            "aggregated_success", "sampled_out_traces", "sampled_out_slow"):
                    value = status.get(key, 0)
                    if type(value) is not int or not 0 <= value <= 1_000_000_000:
                        raise ValueError("Invalid recorder counter")
                    counts[f"recorder_{key}"] = value
                if any(counts[f"recorder_{key}"] for key in (
                        "dropped", "invalid", "expired_queue", "storage_errors", "drain_timeouts")):
                    gaps.append("recorder_loss")
            except Exception:
                gaps.append("recorder_status_unavailable")
        lease = PreparedLease(store, view, prepared.directory)
        return BundleSnapshot(store, lease, cutoff_at, prepared.paths, runtime, operations,
                              counts, bool(gaps), tuple(dict.fromkeys(gaps)), policies, True)
    except Exception:
        try:
            store.delete_tree(directory)
        finally:
            view.release()
        raise
