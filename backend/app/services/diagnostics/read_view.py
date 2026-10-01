"""Short read leases over immutable prefixes of owned diagnostic segments."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re
import stat
from pathlib import Path

from .sanitize import sanitize_event
from .schema import EventFilter, MAX_EVENT_BYTES


@dataclass(frozen=True)
class SegmentDescriptor:
    path: Path
    stream: str
    identity: tuple[int, int]
    length: int
    expires_at: float
    mtime_ns: int
    source: str = "backend"
    root: Path | None = None


@dataclass
class ReadViewLease:
    store: object
    descriptors: tuple[SegmentDescriptor, ...]
    filter: EventFilter
    cutoff_at: datetime
    released: bool = False
    initial_gaps: tuple[str, ...] = ()

    def release(self):
        if self.released:
            return
        with self.store._mutex:
            if self.released:
                return
            for descriptor in self.descriptors:
                if descriptor.source != "backend":
                    continue
                count = self.store._pins.get(descriptor.path, 0)
                if count <= 1:
                    self.store._pins.pop(descriptor.path, None)
                else:
                    self.store._pins[descriptor.path] = count - 1
            self.released = True


@dataclass(frozen=True)
class EventPage:
    events: tuple[dict, ...]
    scanned_count: int
    partial: bool
    gaps: tuple[str, ...]
    next_offset: int | None
    counts: dict[str, int]


def _safe_external(path: Path, root: Path) -> Path:
    path = Path(os.path.abspath(path))
    root = Path(os.path.abspath(root))
    if not path.is_relative_to(root):
        raise ValueError("Frontend diagnostic path escapes root")
    for part in (path, *path.parents):
        try:
            metadata = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError("Frontend diagnostic path contains link")
    return path


def attach_frontend(view: ReadViewLease, root: Path) -> None:
    if view.released:
        raise ValueError("Read view released")
    root = _safe_external(root, root)
    events_dir = _safe_external(root / "events", root)
    if not events_dir.is_dir():
        view.initial_gaps += ("frontend_unavailable",)
        return
    descriptors = []
    try:
        for stream_dir in sorted(events_dir.iterdir()):
            _safe_external(stream_dir, root)
            stream = stream_dir.name
            if not stream_dir.is_dir() or (stream != "baseline" and not re.fullmatch(
                    r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", stream)):
                continue
            if view.filter.session_id and stream not in {"baseline", view.filter.session_id}:
                continue
            for path in sorted(stream_dir.glob("*.jsonl")):
                _safe_external(path, root)
                if not re.fullmatch(r"\d{13}_[a-f0-9-]{36}\.jsonl", path.name):
                    continue
                metadata = path.stat()
                age = view.store.limits.baseline_seconds if stream == "baseline" else view.store.limits.capture_seconds + 3600
                descriptors.append(SegmentDescriptor(path, stream, (metadata.st_dev, metadata.st_ino),
                                                     metadata.st_size, metadata.st_mtime + age, metadata.st_mtime_ns,
                                                     source="frontend", root=root))
    except (OSError, ValueError):
        view.initial_gaps += ("frontend_unavailable",)
    view.descriptors += tuple(descriptors)


def read_event_page(view: ReadViewLease, *, offset: int, limit: int) -> EventPage:
    if view.released or not 0 <= offset <= 100000 or not 1 <= limit <= 100:
        raise ValueError("Invalid diagnostic read page")
    rows = []
    counts = {"events": 0, "invalid": 0, "truncated": 0, "expired": 0}
    gaps = list(view.initial_gaps)
    scanned = 0
    for descriptor in view.descriptors:
        if datetime.now(timezone.utc).timestamp() >= descriptor.expires_at:
            counts["expired"] += 1
            gaps.append("segment_expired")
            continue
        try:
            path = (view.store.safe_path(descriptor.path) if descriptor.source == "backend"
                    else _safe_external(descriptor.path, descriptor.root))
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
            try:
                opened = os.fstat(fd)
                if ((opened.st_dev, opened.st_ino) != descriptor.identity
                        or opened.st_size < descriptor.length
                        or (opened.st_size == descriptor.length and opened.st_mtime_ns != descriptor.mtime_ns)):
                    raise OSError("Diagnostic segment changed")
                with os.fdopen(fd, "rb") as handle:
                    fd = -1
                    remaining = descriptor.length
                    while remaining > 0:
                        line = handle.readline(min(MAX_EVENT_BYTES + 1, remaining))
                        remaining -= len(line)
                        if not line:
                            raise OSError("Diagnostic segment shrank")
                        if not line.endswith(b"\n"):
                            counts["truncated"] += 1
                            while remaining > 0 and len(line) > MAX_EVENT_BYTES and not line.endswith(b"\n"):
                                line = handle.readline(min(MAX_EVENT_BYTES + 1, remaining))
                                remaining -= len(line)
                            continue
                        try:
                            event = sanitize_event(json.loads(line))
                        except (ValueError, TypeError, UnicodeDecodeError):
                            event = None
                        if (event is None
                                or (descriptor.source == "frontend" and event.get("component") != "frontend")
                                or (descriptor.source == "backend" and event.get("component") not in {"backend", "browser"})):
                            counts["invalid"] += 1
                            continue
                        stamp = datetime.fromisoformat(event["timestamp_utc"]).timestamp()
                        age = view.store.limits.baseline_seconds if descriptor.stream == "baseline" else view.store.limits.capture_seconds + 3600
                        if stamp < view.cutoff_at.timestamp() - age:
                            counts["expired"] += 1
                            continue
                        if stamp > view.cutoff_at.timestamp() or not view.filter.matches(event):
                            continue
                        counts["events"] += 1
                        scanned += 1
                        if scanned > offset and len(rows) < limit:
                            rows.append(event)
                        elif scanned > offset + limit:
                            return EventPage(tuple(rows), scanned, bool(gaps or counts["invalid"] or counts["truncated"]),
                                             tuple(gaps), offset + len(rows), counts)
            finally:
                if fd >= 0:
                    os.close(fd)
        except (OSError, ValueError):
            counts["invalid"] += 1
            gaps.append("segment_unavailable")
    return EventPage(tuple(rows), scanned, bool(gaps or counts["invalid"] or counts["truncated"]),
                     tuple(gaps), None, counts)
