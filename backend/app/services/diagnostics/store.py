"""Owned bounded diagnostic storage, independent of the relational database."""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import shutil
import stat
import threading
import time
from uuid import uuid4

from .sanitize import _encode_validated_event, encode_event, sanitize_event, valid_uuid
from .segment_index import ReconcileResult, SegmentIndex
from .read_view import ReadViewLease, SegmentDescriptor, read_event_page as page_from_view
from .schema import DiagnosticLimits, EventFilter, MAX_EVENT_BYTES


class StorageQuotaError(RuntimeError):
    pass


class WriterActiveError(RuntimeError):
    pass


_VALIDATED_TOKEN = object()


class ValidatedEvent:
    __slots__ = ("encoded", "event_code", "timestamp_utc", "session_id")

    def __init__(self, encoded, event_code, timestamp_utc, session_id, *, token):
        if token is not _VALIDATED_TOKEN:
            raise ValueError("Diagnostic event must be validated internally")
        self.encoded = encoded
        self.event_code = event_code
        self.timestamp_utc = timestamp_utc
        self.session_id = session_id


@dataclass(frozen=True)
class BatchWriteResult:
    written_events: int
    written_bytes: int
    failure_reason: str | None = None


@dataclass
class Reservation:
    store: "DiagnosticStore"
    key: str

    @property
    def remaining(self) -> int:
        return self.store._reservations.get(self.key, 0)

    def release(self):
        with self.store._mutex:
            self.store._reservations.pop(self.key, None)


@dataclass
class SnapshotLease:
    store: "DiagnosticStore"
    paths: tuple[Path, ...]
    counts: dict
    directory: Path
    cutoff_at: datetime

    def release(self):
        with self.store._mutex:
            self.store.delete_tree(self.directory)


@dataclass(frozen=True)
class CleanupResult:
    deleted_files: int = 0
    deleted_bytes: int = 0
    unsafe_paths: int = 0


def _is_link(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & 0x400
    )  # Windows FILE_ATTRIBUTE_REPARSE_POINT includes junctions.


class DiagnosticStore:
    def __init__(self, root: Path, limits: DiagnosticLimits):
        self.root = Path(root).absolute()
        self.limits = limits
        self._mutex = threading.RLock()
        self._projection_lock = threading.Lock()
        self._projection_revision = None
        self._reservations: dict[str, int] = {}
        self._stream_credit: dict[str, Reservation] = {}
        self._pins: dict[Path, int] = {}
        self._current: dict[str, Path] = {}
        self._lock_handle = None
        self._degraded = False
        self._last_failure = None
        self._unsafe = 0
        self.safe_path(self.root)
        for relative in ("events/baseline", "snapshots", "bundles", "control", "capture-expiry"):
            path = self.safe_path(self.root / relative)
            path.mkdir(parents=True, exist_ok=True, mode=0o770)
        self._index = SegmentIndex(self.root, self._files)
        self._last_free_bytes = None
        self._free_measured_at = None

    def safe_path(self, path: Path) -> Path:
        path = Path(os.path.abspath(path))
        if not path.is_relative_to(self.root):
            raise ValueError("Diagnostic path escapes root")
        # Include root and its parents: resolving first would hide a junction.
        for part in (path, *path.parents):
            if _is_link(part):
                raise ValueError("Diagnostic path contains a link")
        return path

    def open(self):
        with self._mutex:
            if self._lock_handle is not None:
                return
            path = self.safe_path(self.root / ".writer.lock")
            fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o660)
            handle = os.fdopen(fd, "r+b", buffering=0)
            try:
                if os.fstat(fd).st_size == 0:
                    handle.write(b"0")
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                raise WriterActiveError("Diagnostic writer is active") from exc
            self._lock_handle = handle
            self._index.record(path)

    def close(self):
        with self._mutex:
            for credit in self._stream_credit.values():
                credit.release()
            self._stream_credit.clear()
            if self._lock_handle is not None:
                handle = self._lock_handle
                self._lock_handle = None
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()

    def _files(self, directory: Path):
        directory = self.safe_path(directory)
        for entry in os.scandir(directory):
            path = Path(entry.path)
            if _is_link(path):
                self._unsafe += 1
                continue
            if entry.is_dir(follow_symlinks=False):
                yield from self._files(path)
            elif entry.is_file(follow_symlinks=False):
                yield path

    @property
    def used_bytes(self) -> int:
        with self._mutex:
            return self._index.used_bytes

    def reconcile(self) -> ReconcileResult:
        generation = self._index.generation
        scanned = self._index.scan()  # No store lock while walking the tree.
        with self._mutex:
            if generation != self._index.generation:
                return ReconcileResult(0, self._index.used_bytes, self._index.measured_at,
                                       concurrent_write=True)
            result = self._index.apply_scan(scanned)
            if result.changed_files:
                self._degraded = True
                self._last_failure = "storage_error"
            return result

    def note_rename(self, old: Path, new: Path):
        with self._mutex:
            self._index.rename(self.safe_path(old), self.safe_path(new))

    def note_deleted(self, path: Path):
        with self._mutex:
            self._index.remove(self.safe_path(path))

    def commit_external_growth(self, reservation: Reservation, path: Path):
        """Transfer supervised child growth from an outstanding credit to used bytes."""
        with self._mutex:
            path = self.safe_path(path)
            if reservation.store is not self or reservation.key not in self._reservations:
                raise StorageQuotaError("Unknown diagnostic reservation")
            if not path.is_file():
                raise ValueError("Missing diagnostic child file")
            growth = path.stat().st_size - self._index.size(path)
            if growth < 0 or growth > reservation.remaining:
                self._degraded = True
                raise StorageQuotaError("Diagnostic child exceeded reserved credit")
            self._index.record(path)
            self._reservations[reservation.key] -= growth

    @property
    def reserved_bytes(self) -> int:
        with self._mutex:
            return sum(self._reservations.values())

    def _capacity(self, growth: int, reservation: Reservation | None = None):
        reserved = reservation.remaining if reservation else 0
        if reservation and (reservation.store is not self or reserved < growth):
            raise StorageQuotaError("Insufficient diagnostic reservation")
        # A reservation already passed the full-tree quota check. Consuming it
        # transfers bytes from reserved to used without increasing their sum.
        if (reservation is None or growth > reserved) and (
            self.used_bytes + self.reserved_bytes + max(0, growth) - min(reserved, growth)
            > self.limits.backend_bytes
        ):
            raise StorageQuotaError("Diagnostic storage quota exceeded")
        free = shutil.disk_usage(self.root).free
        self._last_free_bytes = free
        self._free_measured_at = datetime.now().astimezone()
        if free - (self.reserved_bytes + max(0, growth) - min(reserved, growth)) < self.limits.min_free_bytes:
            raise StorageQuotaError("Diagnostic free-space reserve reached")

    def reserve(self, bytes_required: int) -> Reservation:
        if type(bytes_required) is not int or bytes_required < 0:
            raise ValueError("Invalid diagnostic reservation")
        with self._mutex:
            self._capacity(bytes_required)
            key = str(uuid4())
            self._reservations[key] = bytes_required
            return Reservation(self, key)

    def _credit_for_append(self, stream: str, size: int, budget: int) -> Reservation:
        credit = self._stream_credit.get(stream)
        if credit is not None and credit.remaining >= size:
            return credit
        if credit is not None:
            credit.release()
        # The exclusive writer owns the spool; prepaid bytes are counted by
        # every other reservation and write until consumed or released.
        chunk = max(size, min(65536, budget))
        try:
            credit = self.reserve(chunk)
        except StorageQuotaError:
            credit = self.reserve(size)
        self._stream_credit[stream] = credit
        return credit

    def write_bytes(self, path: Path, data: bytes, *, reservation: Reservation | None = None,
                    append: bool = False):
        with self._mutex:
            self._write_bytes_locked(self.safe_path(path), data, reservation=reservation, append=append)

    def _write_bytes_locked(self, path: Path, data: bytes, *, reservation: Reservation | None,
                            append: bool):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o770)
        previous = path.stat().st_size if path.exists() else 0
        growth = len(data) if append else max(0, len(data) - previous)
        self._capacity(growth, reservation)
        flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
        fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o660)
        try:
            # A short write is legal. On append failure, restore the last complete JSONL boundary.
            view = memoryview(data)
            while view:
                try:
                    n = os.write(fd, view)
                except OSError:
                    if append:
                        os.ftruncate(fd, previous)
                    raise
                if not n:
                    if append:
                        os.ftruncate(fd, previous)
                    raise OSError("Diagnostic write failed")
                view = view[n:]
        finally:
            os.close(fd)
            self._index.record(path)
            if reservation:
                actual_growth = max(0, path.stat().st_size - previous)
                self._reservations[reservation.key] -= actual_growth

    def _stream_path(self, stream: str) -> Path:
        if stream != "baseline" and valid_uuid(stream) is None:
            raise ValueError("Invalid diagnostic stream")
        return self.safe_path(self.root / "events" / stream)

    def validate_event(self, encoded: bytes) -> ValidatedEvent:
        if type(encoded) is not bytes or not encoded.endswith(b"\n") or len(encoded) > MAX_EVENT_BYTES:
            raise ValueError("Invalid diagnostic event bytes")
        try:
            event = json.loads(encoded)
            if encode_event(event) != encoded:
                raise ValueError("Noncanonical diagnostic event")
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid diagnostic event") from exc
        return ValidatedEvent(encoded, event["event_code"], event["timestamp_utc"],
                              event.get("diagnostic_session_id"), token=_VALIDATED_TOKEN)

    def _validated_from_recorder(self, encoded: bytes, event_code: str, session_id: str | None) -> ValidatedEvent:
        """Only the recorder calls this with bytes already encoded before queue admission."""
        raw = json.loads(encoded)
        return ValidatedEvent(encoded, event_code, raw["timestamp_utc"], session_id,
                              token=_VALIDATED_TOKEN)

    def append_batch(self, events: tuple[ValidatedEvent, ...], *, stream: str) -> BatchWriteResult:
        directory = self._stream_path(stream)
        if type(events) is not tuple or any(type(event) is not ValidatedEvent for event in events):
            return BatchWriteResult(0, 0, "invalid")
        if any(len(event.encoded) > self.limits.segment_bytes for event in events):
            return BatchWriteResult(0, 0, "invalid")
        written_events = written_bytes = 0
        try:
            with self._mutex:
                self.open()
                directory.mkdir(parents=True, exist_ok=True, mode=0o770)
                budget = self.limits.baseline_bytes if stream == "baseline" else self.limits.session_bytes
                position = 0
                while position < len(events):
                    paths = self._index.stream_paths(stream)
                    used = sum(self._index.size(path) for path in paths)
                    path = self._current.get(stream)
                    if path is not None:
                        self.safe_path(path)
                        if not path.exists() or path.stat().st_size != self._index.size(path):
                            self._degraded = True
                            self._last_failure = "storage_error"
                            return BatchWriteResult(written_events, written_bytes, "storage_error")
                    if path is None or self._index.size(path) + len(events[position].encoded) > self.limits.segment_bytes:
                        path = directory / f"{time.time_ns():020d}-{uuid4().hex}.jsonl"
                        self._current[stream] = path
                    self.safe_path(path)
                    remaining = self.limits.segment_bytes - self._index.size(path)
                    selected = []
                    total = 0
                    for event in events[position:]:
                        if total + len(event.encoded) > min(remaining, 65536):
                            break
                        selected.append(event)
                        total += len(event.encoded)
                    if not selected:
                        self._current.pop(stream, None)
                        continue
                    if stream != "baseline" and used + total > budget:
                        self._last_failure = "size_limit"
                        return BatchWriteResult(written_events, written_bytes, "size_limit")
                    while stream == "baseline" and paths and used + total > budget:
                        oldest = paths.pop(0)
                        if self._pins.get(oldest):
                            self._last_failure = "size_limit"
                            return BatchWriteResult(written_events, written_bytes, "size_limit")
                        used -= self._index.size(oldest)
                        self.safe_path(oldest).unlink()
                        self._index.remove(oldest)
                        if self._current.get(stream) == oldest:
                            self._current.pop(stream, None)
                    credit = self._credit_for_append(stream, total, budget)
                    self._write_bytes_locked(path, b"".join(event.encoded for event in selected),
                                             reservation=credit, append=True)
                    written_events += len(selected)
                    written_bytes += total
                    position += len(selected)
                self._degraded = False
                self._last_failure = None
                return BatchWriteResult(written_events, written_bytes)
        except (OSError, StorageQuotaError, WriterActiveError, ValueError, TypeError) as exc:
            self._degraded = True
            low = isinstance(exc, StorageQuotaError) or (isinstance(exc, OSError) and exc.errno in {
                errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC),
            })
            self._last_failure = "storage_low" if low else "storage_error"
            return BatchWriteResult(written_events, written_bytes, self._last_failure)

    def append(self, encoded: bytes, *, stream: str) -> bool:
        try:
            event = self.validate_event(encoded)
        except ValueError:
            return False
        return self.append_batch((event,), stream=stream).written_events == 1

    def status(self) -> dict:
        with self._mutex:
            return {"used_bytes": self._index.used_bytes, "reserved_bytes": self.reserved_bytes,
                    "quota_bytes": self.limits.backend_bytes, "free_bytes": self._last_free_bytes,
                    "measured_at": self._free_measured_at,
                    "storage_degraded": self._degraded,
                    "unsafe_paths": self._unsafe, "last_failure": self._last_failure}

    def retire_capture(self, session_id: str, stopped_at: datetime):
        if valid_uuid(session_id) is None:
            raise ValueError("Invalid session id")
        expiry = stopped_at.timestamp() + self.limits.capture_seconds

        with self._mutex:
            credit = self._stream_credit.pop(session_id, None)
            if credit is not None:
                credit.release()
        self.write_bytes(self.root / "capture-expiry" / (session_id + ".json"),
                         json.dumps({"expires_at": expiry}).encode())

    def _capture_expiry(self, stream: str) -> float | None:
        path = self.safe_path(self.root / "capture-expiry" / (stream + ".json"))
        try:
            value = json.loads(path.read_bytes())
            return float(value["expires_at"])
        except (FileNotFoundError, ValueError, TypeError, KeyError):
            return None

    def pin_read_view(self, filter: EventFilter, cutoff_at: datetime) -> ReadViewLease:
        if cutoff_at.tzinfo is None:
            raise ValueError("Cutoff must be timezone-aware")
        with self._mutex:
            descriptors = []
            streams = ("baseline", filter.session_id) if filter.session_id else tuple(
                sorted(path.name for path in (self.root / "events").iterdir() if path.is_dir() and not _is_link(path)))
            try:
                for stream in streams:
                    if stream is None or (stream != "baseline" and valid_uuid(stream) is None):
                        continue
                    expiry = self._capture_expiry(stream) if stream != "baseline" else None
                    for path in self._index.stream_paths(stream):
                        path = self.safe_path(path)
                        metadata = path.stat()
                        identity, size = self._index.files[path]
                        if (metadata.st_dev, metadata.st_ino, metadata.st_size) != (*identity, size):
                            raise OSError("Diagnostic segment changed before pin")
                        expires_at = (expiry if expiry is not None else metadata.st_mtime + (
                            self.limits.baseline_seconds if stream == "baseline" else self.limits.capture_seconds + 3600))
                        descriptors.append(SegmentDescriptor(path, stream, identity, size, expires_at, metadata.st_mtime_ns))
                        self._pins[path] = self._pins.get(path, 0) + 1
            except Exception:
                for descriptor in descriptors:
                    count = self._pins.get(descriptor.path, 0)
                    if count <= 1:
                        self._pins.pop(descriptor.path, None)
                    else:
                        self._pins[descriptor.path] = count - 1
                raise
            return ReadViewLease(self, tuple(descriptors), filter, cutoff_at.astimezone(timezone.utc))

    def read_event_page(self, view: ReadViewLease, *, offset: int, limit: int):
        if view.store is not self:
            raise ValueError("Foreign diagnostic read view")
        return page_from_view(view, offset=offset, limit=limit)

    def snapshot(self, filter: EventFilter, cutoff_at: datetime) -> SnapshotLease:
        with self._mutex:
            directory = self.root / "snapshots" / str(uuid4())
            directory.mkdir(mode=0o770)
            counts = Counter({"events": 0, "invalid": 0, "truncated": 0, "expired": 0})
            paths = []
            now_ts = cutoff_at.timestamp()
            try:
                streams = sorted((self.root / "events").iterdir())
                for stream_dir in streams:
                    if _is_link(stream_dir) or not stream_dir.is_dir():
                        counts["invalid"] += 1
                        continue
                    stream = stream_dir.name
                    if stream != "baseline" and valid_uuid(stream) is None:
                        counts["invalid"] += 1
                        continue
                    expiry = self._capture_expiry(stream) if stream != "baseline" else None
                    for source in sorted(stream_dir.glob("*.jsonl")):
                        self.safe_path(source)
                        target = directory / (source.name + ".jsonl")
                        try:
                            handle = source.open("rb")
                        except (PermissionError, FileNotFoundError):
                            # A restored or concurrently removed segment must not
                            # prevent inspection of the readable support evidence.
                            counts["invalid"] += 1
                            continue
                        with handle:
                            pending = bytearray()
                            while line := handle.readline(MAX_EVENT_BYTES + 1):
                                if not line.endswith(b"\n"):
                                    counts["truncated"] += 1
                                    # Discard overlong line until boundary without buffering it.
                                    while len(line) > MAX_EVENT_BYTES and not line.endswith(b"\n"):
                                        line = handle.readline(MAX_EVENT_BYTES + 1)
                                        if not line:
                                            break
                                    continue
                                try:
                                    event = sanitize_event(json.loads(line))
                                except (ValueError, TypeError, UnicodeDecodeError):
                                    event = None
                                if event is None:
                                    counts["invalid"] += 1
                                    continue
                                stamp = datetime.fromisoformat(event["timestamp_utc"]).timestamp()
                                age_limit = self.limits.baseline_seconds if stream == "baseline" else self.limits.capture_seconds + 3600
                                if (expiry is not None and expiry <= now_ts) or stamp < now_ts - age_limit:
                                    counts["expired"] += 1
                                    continue
                                if stamp > now_ts or not filter.matches(event):
                                    continue
                                pending.extend(_encode_validated_event(event))
                                if len(pending) >= 65536:
                                    self.write_bytes(target, bytes(pending), append=True)
                                    pending.clear()
                                counts["events"] += 1
                            if pending:
                                self.write_bytes(target, bytes(pending), append=True)
                        if target.exists():
                            paths.append(target)
                return SnapshotLease(self, tuple(paths), dict(counts), directory, cutoff_at)
            except Exception:
                self.delete_tree(directory)
                raise

    def delete_tree(self, path: Path):
        path = self.safe_path(path)
        if path == self.root:
            raise ValueError("Cannot delete diagnostic root")
        if not path.exists():
            return
        # Do not recursively follow links, and do not delete a tree that contains one.
        children = list(path.iterdir()) if path.is_dir() else []
        for child in children:
            self.safe_path(child)
        for child in children:
            self.delete_tree(child)
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
            self._index.remove(path)

    def sweep(self, now: datetime) -> CleanupResult:
        deleted = size = unsafe = 0
        with self._mutex:
            for directory in (self.root / "events").iterdir():
                if _is_link(directory):
                    unsafe += 1
                    continue
                if not directory.is_dir():
                    continue
                stream = directory.name
                if stream != "baseline" and valid_uuid(stream) is None:
                    continue
                expiry = self._capture_expiry(stream) if stream != "baseline" else None
                for path in directory.glob("*.jsonl"):
                    if _is_link(path):
                        unsafe += 1
                        continue
                    if self._pins.get(path):
                        continue
                    stat = path.stat()
                    ttl = self.limits.baseline_seconds if stream == "baseline" else self.limits.capture_seconds + 3600
                    if (expiry is not None and expiry <= now.timestamp()) or stat.st_mtime < now.timestamp() - ttl:
                        path.unlink()
                        self._index.remove(path)
                        deleted += 1
                        size += stat.st_size
                        if self._current.get(stream) == path:
                            self._current.pop(stream, None)
                # Keep a capture with pinned segments (or unexpected files)
                # until a later sweep. Other streams can still be cleaned.
                if (stream != "baseline" and expiry is not None and expiry <= now.timestamp()
                        and not any(directory.iterdir())):
                    credit = self._stream_credit.pop(stream, None)
                    if credit is not None:
                        credit.release()
                    directory.rmdir()
                    marker = self.safe_path(self.root / "capture-expiry" / (stream + ".json"))
                    marker.unlink(missing_ok=True)
                    self._index.remove(marker)
            self._unsafe += unsafe
        return CleanupResult(deleted, size, unsafe)
