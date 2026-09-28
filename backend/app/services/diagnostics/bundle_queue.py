"""One-worker durable bundle queue. Database state, not a filename, grants readiness."""
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from uuid import uuid4

from sqlalchemy import func, select

from app import error_codes as codes
from app.db.models import DiagnosticBundle
from app.db.session import session_scope
from app.models.diagnostics import BundleOut, BundleRequest
from app.services import audit
from .bundle import BuiltBundle, BundleTooLarge, estimate_bundle_upper
from .sanitize import valid_uuid
from .snapshot import collect_snapshot
from .store import StorageQuotaError, _is_link


class BundleQueueError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass
class DownloadLease:
    queue: "DiagnosticBundleQueue"
    bundle_id: str
    handle: object
    filename: str
    size_bytes: int
    released: bool = False

    def chunks(self):
        try:
            while True:
                chunk = self.handle.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            self.release()

    def release(self):
        self.queue._release_download(self)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _out(row):
    return BundleOut(id=row.id, status=row.status, created_at=_utc(row.created_at),
                     finished_at=_utc(row.finished_at) if row.finished_at else None,
                     expires_at=_utc(row.expires_at) if row.expires_at else None,
                     size_bytes=row.size_bytes, sha256=row.sha256, error_code=row.error_code,
                     counts=row.counts or {})


class DiagnosticBundleQueue:
    def __init__(self, store, *, frontend_root: Path | None = None, recorder_status=None,
                 recorder_barrier=None):
        self.store = store
        self.frontend_root = frontend_root
        self.recorder_status = recorder_status
        self.recorder_barrier = recorder_barrier
        self._condition = threading.Condition()
        self._pending = deque()
        self._reservations = {}
        self._active = False
        self._running = False
        self._thread = None
        self._child = None
        self._downloads = {}
        self.utcnow = lambda: datetime.now(timezone.utc)

    def _estimate(self):
        # Only queue metadata is reserved at submit time. Selected events are
        # copied into a bounded snapshot by the single worker; build_bundle
        # then reserves the actual snapshot plus ZIP worst case. Reserving all
        # retained segments would reject a narrow request for an unrelated day.
        return 65536

    def submit(self, request: BundleRequest, actor) -> BundleOut:
        if not isinstance(request, BundleRequest):
            request = BundleRequest.model_validate(request)
        with self._condition:
            estimate = self._estimate()
            try:
                reservation = self.store.reserve(estimate)
            except StorageQuotaError as exc:
                raise BundleQueueError("diagnostic_storage_low") from exc
            now = self.utcnow()
            row = DiagnosticBundle(
                id=str(uuid4()), status="queued", created_by_id=actor.user_id,
                created_by=actor.username, created_at=now, cutoff_at=now,
                request=request.model_dump(mode="json"), manifest={}, counts={},
                size_bytes=0, audit_receipts=[], schema_version=1,
            )
            try:
                with session_scope() as db:
                    hourly = db.scalar(select(func.count()).select_from(DiagnosticBundle).where(
                        DiagnosticBundle.created_by_id == actor.user_id,
                        DiagnosticBundle.created_at >= now - timedelta(hours=1)))
                    if hourly >= 3:
                        raise BundleQueueError("diagnostic_bundle_rate_limited")
                    unfinished = db.scalar(select(func.count()).select_from(DiagnosticBundle).where(
                        DiagnosticBundle.created_by_id == actor.user_id,
                        DiagnosticBundle.status.in_(("queued", "building"))))
                    if unfinished:
                        raise BundleQueueError("diagnostic_bundle_busy")
                    queued = db.scalar(select(func.count()).select_from(DiagnosticBundle).where(
                        DiagnosticBundle.status == "queued"))
                    building = db.scalar(select(func.count()).select_from(DiagnosticBundle).where(
                        DiagnosticBundle.status == "building"))
                    if queued >= 2 or building >= 1 and self._active is False:
                        raise BundleQueueError("diagnostic_bundle_busy")
                    db.add(row)
                    audit.record_in_session(
                        db, action_type="diagnostic_bundle_requested", user_id=actor.user_id,
                        username=actor.username, target_type="diagnostic_bundle", target_id=row.id,
                        new_value={"cutoff_at": now.isoformat()},
                    )
            except BundleQueueError:
                reservation.release()
                raise
            except Exception as exc:
                reservation.release()
                raise BundleQueueError("diagnostic_audit_unavailable") from exc
            self._reservations[row.id] = reservation
            self._pending.append(row.id)
            self._condition.notify_all()
            return _out(row)

    def recover(self):
        with self._condition:
            if self._running or self._active:
                raise BundleQueueError("diagnostic_bundle_busy")
            now = self.utcnow()
            try:
                with session_scope() as db:
                    for row in db.scalars(select(DiagnosticBundle).where(
                            DiagnosticBundle.status.in_(("queued", "building")))):
                        row.status = "failed"
                        row.error_code = "server_restarted"
                        row.finished_at = now
                        audit.record_in_session(
                            db, action_type="diagnostic_bundle_failed", user_id="system",
                            username="system", target_type="diagnostic_bundle", target_id=row.id,
                            new_value={"error_code": "server_restarted"},
                        )
            except Exception as exc:
                raise BundleQueueError("diagnostic_audit_unavailable") from exc
            for item in (self.store.root / "bundles").glob("*.part"):
                if valid_uuid(item.stem) is None:
                    continue
                self.store.safe_path(item)
                item.unlink(missing_ok=True)
            for reservation in self._reservations.values():
                reservation.release()
            self._reservations.clear()
            self._pending.clear()

    def start(self):
        with self._condition:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._run, name="diagnostic-bundle", daemon=True)
            self._thread.start()

    def _fail(self, bundle_id, code):
        try:
            with session_scope() as db:
                row = db.get(DiagnosticBundle, bundle_id)
                if row is None or row.status not in {"queued", "building"}:
                    return
                row.status = "failed"
                row.error_code = code
                row.finished_at = self.utcnow()
                audit.record_in_session(
                    db, action_type="diagnostic_bundle_failed", user_id="system", username="system",
                    target_type="diagnostic_bundle", target_id=bundle_id,
                    new_value={"error_code": code},
                )
        except Exception:
            # A failed audit never grants download access. Recovery retires the
            # still-building row when the database becomes available.
            pass

    def _process(self, bundle_id):
        temp = self.store.safe_path(self.store.root / "bundles" / (bundle_id + ".part"))
        final = self.store.safe_path(self.store.root / "bundles" / (bundle_id + ".zip"))
        snapshot = None
        try:
            with session_scope() as db:
                row = db.get(DiagnosticBundle, bundle_id)
                if row is None or row.status != "queued":
                    return
                row.status = "building"
                request = BundleRequest.model_validate(row.request)
                cutoff = _utc(row.cutoff_at)
            snapshot = collect_snapshot(request, cutoff_at=cutoff, store=self.store,
                                        frontend_root=self.frontend_root,
                                        recorder_status=self.recorder_status,
                                        recorder_barrier=self.recorder_barrier)
            built = self._build_isolated(snapshot, temp, bundle_id)
            snapshot.release()
            snapshot = None
            os.replace(temp, final)
            # Rename alone is insufficient; only this audited transaction makes
            # the download endpoint eligible to open final.
            with session_scope() as db:
                row = db.get(DiagnosticBundle, bundle_id)
                if row is None or row.status != "building":
                    raise RuntimeError("Bundle status changed")
                row.status = "ready"
                row.finished_at = self.utcnow()
                row.expires_at = row.finished_at + timedelta(seconds=self.store.limits.bundle_seconds)
                row.size_bytes = built.size_bytes
                row.sha256 = built.sha256
                row.manifest = built.manifest
                row.counts = built.manifest["counts"]
                audit.record_in_session(
                    db, action_type="diagnostic_bundle_ready", user_id="system", username="system",
                    target_type="diagnostic_bundle", target_id=bundle_id,
                    new_value={"size_bytes": built.size_bytes, "sha256": built.sha256},
                )
        except Exception as exc:
            temp.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            code = codes.DIAGNOSTIC_BUNDLE_TOO_LARGE if isinstance(exc, BundleTooLarge) else codes.DIAGNOSTIC_BUNDLE_FAILED
            self._fail(bundle_id, code)
        finally:
            if snapshot:
                snapshot.release()

    def _build_isolated(self, snapshot, destination, bundle_id):
        upper = estimate_bundle_upper(snapshot)
        if upper > self.store.limits.bundle_bytes:
            raise BundleTooLarge()
        directory = snapshot.lease.directory
        if any(path.parent != directory for path in snapshot.paths):
            raise ValueError("Snapshot path outside lease")
        reservation = self.store.reserve(upper)
        try:
            payload = json.dumps({
                "root": str(self.store.root), "bundle_id": bundle_id,
                "snapshot_id": directory.name, "filenames": [path.name for path in snapshot.paths],
                "cutoff_at": snapshot.cutoff_at.isoformat(), "runtime": snapshot.runtime,
                "operations": snapshot.operations, "counts": snapshot.counts,
                "partial": snapshot.partial, "gaps": snapshot.gaps,
                "limits": vars(self.store.limits), "reserved_upper": upper,
            }, separators=(",", ":")).encode("utf-8")
            if len(payload) > 4 * 1048576:
                raise ValueError("Worker payload too large")
            options = {"cwd": str(Path(__file__).resolve().parents[3]),
                       "stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
                       "stderr": subprocess.DEVNULL}
            if os.name == "nt":
                options["creationflags"] = (getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
                                            | getattr(subprocess, "CREATE_NO_WINDOW", 0))
            with self._condition:
                if not self._running:
                    raise RuntimeError("Bundle queue stopping")
                child = subprocess.Popen(
                    [sys.executable, "-m", "app.services.diagnostics.bundle_worker"], **options)
                self._child = child
            try:
                try:
                    output, _ = child.communicate(input=payload, timeout=300)
                except subprocess.TimeoutExpired as exc:
                    child.kill()
                    child.communicate()
                    raise RuntimeError("Bundle builder timed out") from exc
            finally:
                with self._condition:
                    if self._child is child:
                        self._child = None
                    self._condition.notify_all()
            if child.returncode != 0 or len(output) > 2 * 1048576:
                raise RuntimeError("Bundle builder failed")
            result = json.loads(output)
            if result.get("status") == "too_large":
                raise BundleTooLarge()
            if result.get("status") != "ready":
                raise RuntimeError("Bundle builder failed")
            size, digest, manifest = result["size_bytes"], result["sha256"], result["manifest"]
            if (type(size) is not int or not 0 < size <= self.store.limits.bundle_bytes
                    or not re.fullmatch(r"[a-f0-9]{64}", digest)
                    or not isinstance(manifest, dict) or not isinstance(manifest.get("counts"), dict)
                    or destination.stat().st_size != size):
                raise RuntimeError("Invalid bundle builder result")
            return BuiltBundle(destination, destination.name, size, digest, manifest)
        finally:
            reservation.release()

    def _run(self):
        while True:
            with self._condition:
                while self._running and not self._pending:
                    self._condition.wait()
                if not self._running:
                    return
                bundle_id = self._pending.popleft()
                reservation = self._reservations.pop(bundle_id, None)
                self._active = True
            try:
                if reservation:
                    reservation.release()
                self._process(bundle_id)
            finally:
                with self._condition:
                    self._active = False
                    self._condition.notify_all()

    def wait_idle(self, timeout_seconds):
        with self._condition:
            return self._condition.wait_for(lambda: not self._pending and not self._active,
                                            timeout=timeout_seconds)

    def shutdown(self, timeout_seconds=5):
        with self._condition:
            self._running = False
            child = self._child
            for reservation in self._reservations.values():
                reservation.release()
            self._reservations.clear()
            self._pending.clear()
            self._condition.notify_all()
        if child is not None and child.poll() is None:
            try:
                child.terminate()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout_seconds)
            if self._thread.is_alive() and child is not None and child.poll() is None:
                child.kill()
                self._thread.join(1)

    def _release_download(self, lease: DownloadLease):
        with self._condition:
            if lease.released:
                return
            lease.released = True
            lease.handle.close()
            count = self._downloads.get(lease.bundle_id, 0) - 1
            if count > 0:
                self._downloads[lease.bundle_id] = count
            else:
                self._downloads.pop(lease.bundle_id, None)
                try:
                    with session_scope() as db:
                        row = db.get(DiagnosticBundle, lease.bundle_id)
                        retired = row is not None and row.status in {"deleted", "expired"}
                    if retired:
                        self.store.safe_path(self.store.root / "bundles" / (lease.bundle_id + ".zip")).unlink(missing_ok=True)
                except Exception:
                    # Maintenance will clean a retired file when DB recovers.
                    pass

    def acquire_download(self, bundle_id, actor) -> DownloadLease:
        if "admin" not in actor.roles:
            raise BundleQueueError("forbidden")
        bundle_id = str(bundle_id)
        if valid_uuid(bundle_id) is None:
            raise BundleQueueError("invalid_request")
        with self._condition:
            handle = None
            try:
                with session_scope() as db:
                    row = db.get(DiagnosticBundle, bundle_id)
                    if row is None:
                        raise BundleQueueError("diagnostic_bundle_not_found")
                    if row.status in {"deleted", "expired"} or row.expires_at and _utc(row.expires_at) <= self.utcnow():
                        raise BundleQueueError("diagnostic_bundle_gone")
                    if row.status != "ready":
                        raise BundleQueueError("diagnostic_bundle_not_ready")
                    filename = f"okf-diagnostics-{bundle_id}.zip"
                    path = self.store.safe_path(self.store.root / "bundles" / (bundle_id + ".zip"))
                    before = path.lstat()
                    if _is_link(path):
                        raise BundleQueueError("diagnostic_bundle_gone")
                    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
                    handle = os.fdopen(fd, "rb")
                    opened, after = os.fstat(fd), path.lstat()
                    if _is_link(path) or (before.st_dev, before.st_ino, before.st_size) != (
                        opened.st_dev, opened.st_ino, opened.st_size) or (
                        after.st_dev, after.st_ino, after.st_size) != (
                        opened.st_dev, opened.st_ino, opened.st_size) or opened.st_size != row.size_bytes:
                        raise BundleQueueError("diagnostic_bundle_gone")
                    audit.record_in_session(
                        db, action_type="diagnostic_bundle_download_started",
                        user_id=actor.user_id, username=actor.username,
                        target_type="diagnostic_bundle", target_id=bundle_id,
                        new_value={"size_bytes": row.size_bytes},
                    )
                    size = row.size_bytes
                lease = DownloadLease(self, bundle_id, handle, filename, size)
                self._downloads[bundle_id] = self._downloads.get(bundle_id, 0) + 1
                return lease
            except BundleQueueError:
                if handle:
                    handle.close()
                raise
            except Exception as exc:
                if handle:
                    handle.close()
                raise BundleQueueError("diagnostic_audit_unavailable") from exc

    def delete(self, bundle_id, actor) -> BundleOut:
        if "admin" not in actor.roles:
            raise BundleQueueError("forbidden")
        bundle_id = str(bundle_id)
        with self._condition:
            try:
                with session_scope() as db:
                    row = db.get(DiagnosticBundle, bundle_id)
                    if row is None:
                        raise BundleQueueError("diagnostic_bundle_not_found")
                    if row.status != "deleted":
                        row.status = "deleted"
                        row.finished_at = self.utcnow()
                        audit.record_in_session(
                            db, action_type="diagnostic_bundle_deleted", user_id=actor.user_id,
                            username=actor.username, target_type="diagnostic_bundle", target_id=bundle_id,
                            new_value={"deleted": True},
                        )
                    output = _out(row)
            except BundleQueueError:
                raise
            except Exception as exc:
                raise BundleQueueError("diagnostic_audit_unavailable") from exc
            if self._downloads.get(bundle_id, 0) == 0:
                self.store.safe_path(self.store.root / "bundles" / (bundle_id + ".zip")).unlink(missing_ok=True)
            return output

    def sweep(self, now=None):
        """Expire metadata before unlink; active readers retain their open file."""
        now = now or self.utcnow()
        with self._condition:
            try:
                with session_scope() as db:
                    retiring = list(db.scalars(select(DiagnosticBundle).where(
                        DiagnosticBundle.status == "ready",
                        DiagnosticBundle.expires_at <= now)))
                    for row in retiring:
                        row.status = "expired"
                        audit.record_in_session(
                            db, action_type="diagnostic_bundle_expired", user_id="system",
                            username="system", target_type="diagnostic_bundle", target_id=row.id,
                            new_value={"expired": True},
                        )
                    retired_ids = list(db.scalars(select(DiagnosticBundle.id).where(
                        DiagnosticBundle.status.in_(("deleted", "expired")))))
                for bundle_id in retired_ids:
                    if self._downloads.get(bundle_id, 0) == 0:
                        self.store.safe_path(self.store.root / "bundles" / (bundle_id + ".zip")).unlink(missing_ok=True)
                return len(retiring)
            except Exception as exc:
                raise BundleQueueError("diagnostic_audit_unavailable") from exc

    def prune_metadata(self, now=None):
        """Remove old terminal rows only after their files and readers are gone."""
        now = now or self.utcnow()
        removed = 0
        with self._condition:
            with session_scope() as db:
                rows = list(db.scalars(select(DiagnosticBundle).where(
                    DiagnosticBundle.status.in_(("failed", "expired", "deleted")),
                    DiagnosticBundle.finished_at < now - timedelta(days=7),
                ).limit(100)))
                for row in rows:
                    if valid_uuid(row.id) is None or self._downloads.get(row.id, 0):
                        continue
                    paths = [self.store.safe_path(self.store.root / "bundles" / (row.id + suffix))
                             for suffix in (".part", ".zip")]
                    for path in paths:
                        path.unlink(missing_ok=True)
                    if any(path.exists() for path in paths):
                        continue
                    db.delete(row)
                    removed += 1
        return removed
