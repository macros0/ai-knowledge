"""Child-only bounded demultiplex of diagnostic segment prefixes."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

from .read_view import _safe_external
from .sanitize import _encode_validated_event, sanitize_event, valid_uuid
from .schema import EventFilter, MAX_EVENT_BYTES
from .worker_protocol import FrameError, JobChannel


class PrepareTooLarge(RuntimeError):
    pass


def _read_descriptor(item, payload, filt, outgoing, incoming, buffers, counts, gaps):
    if not isinstance(item, dict) or item.get("source") not in {"backend", "frontend"}:
        raise FrameError("Invalid prepare descriptor")
    root = Path(payload["root"] if item["source"] == "backend" else payload["frontend_root"])
    source = _safe_external(Path(item["path"]), root)
    if source.suffix != ".jsonl" or source.parent.parent != root / "events":
        raise FrameError("Invalid prepare source")
    stream = source.parent.name
    if stream != "baseline" and valid_uuid(stream) is None:
        raise FrameError("Invalid prepare stream")
    if filt.session_id and stream not in {"baseline", filt.session_id}:
        return
    identity, length, mtime_ns = item.get("identity"), item.get("length"), item.get("mtime_ns")
    if (not isinstance(identity, list) or len(identity) != 2 or any(type(x) is not int for x in identity)
            or type(length) is not int or length < 0 or type(mtime_ns) is not int):
        raise FrameError("Invalid prepare identity")
    if datetime.now(timezone.utc).timestamp() >= item.get("expires_at", 0):
        counts["expired"] += 1
        gaps.add("segment_expired")
        return
    digest = sha256()
    try:
        fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        try:
            opened = os.fstat(fd)
            if ((opened.st_dev, opened.st_ino) != tuple(identity) or opened.st_size < length
                    or (opened.st_size == length and opened.st_mtime_ns != mtime_ns)):
                raise OSError("Prepare source changed before open")
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                remaining = length
                while remaining > 0:
                    line = handle.readline(min(MAX_EVENT_BYTES + 1, remaining))
                    remaining -= len(line)
                    if not line:
                        raise OSError("Prepare source shrank")
                    digest.update(line)
                    if not line.endswith(b"\n"):
                        counts["truncated"] += 1
                        gaps.add("invalid_input")
                        while remaining > 0 and len(line) > MAX_EVENT_BYTES and not line.endswith(b"\n"):
                            line = handle.readline(min(MAX_EVENT_BYTES + 1, remaining))
                            remaining -= len(line)
                            digest.update(line)
                        continue
                    try:
                        event = sanitize_event(json.loads(line))
                    except (ValueError, TypeError, UnicodeDecodeError):
                        event = None
                    if event is None or (item["source"] == "frontend" and event.get("component") != "frontend") or (
                            item["source"] == "backend" and event.get("component") not in {"backend", "browser"}):
                        counts["invalid"] += 1
                        gaps.add("invalid_input")
                        continue
                    stamp = datetime.fromisoformat(event["timestamp_utc"]).timestamp()
                    age = (payload["limits"]["baseline_seconds"] if stream == "baseline"
                           else payload["limits"]["capture_seconds"] + 3600)
                    if stamp < datetime.fromisoformat(payload["cutoff_at"]).timestamp() - age:
                        counts["expired"] += 1
                        continue
                    if stamp > datetime.fromisoformat(payload["cutoff_at"]).timestamp() or not filt.matches(event):
                        continue
                    component = event["component"]
                    encoded = _encode_validated_event(event)
                    selected = counts.get("_selected_bytes", 0) + len(encoded)
                    if selected + 65536 > payload["limits"]["bundle_bytes"]:
                        raise PrepareTooLarge()
                    if component == "frontend":
                        frontend_selected = counts.get("_frontend_bytes", 0) + len(encoded)
                        if frontend_selected > payload["limits"]["frontend_bytes"]:
                            raise PrepareTooLarge()
                        counts["_frontend_bytes"] = frontend_selected
                    counts["_selected_bytes"] = selected
                    if sum(map(len, buffers[component])) + len(encoded) > 65536:
                        _flush(component, payload, outgoing, incoming, buffers, counts)
                    buffers[component].append(encoded)
                    counts["events"] += 1
        finally:
            if fd >= 0:
                os.close(fd)
        _safe_external(source, root)
        check_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                           | getattr(os, "O_BINARY", 0))
        with os.fdopen(check_fd, "rb") as check:
            after = os.fstat(check.fileno())
            if ((after.st_dev, after.st_ino) != tuple(identity) or after.st_size < length
                    or (after.st_size == length and after.st_mtime_ns != mtime_ns)):
                raise OSError("Prepare source changed after read")
            remaining = length
            verify = sha256()
            while remaining:
                block = check.read(min(65536, remaining))
                if not block:
                    raise OSError("Prepare source shrank on verification")
                verify.update(block)
                remaining -= len(block)
        if verify.digest() != digest.digest():
            raise OSError("Prepare source prefix changed")
    except (OSError, ValueError):
        counts["unavailable"] = counts.get("unavailable", 0) + 1
        gaps.add("segment_unavailable")


def _flush(component, payload, outgoing, incoming, buffers, counts):
    lines = buffers[component]
    if not lines:
        return
    data = b"".join(lines)
    outgoing.send(sys.stdout.buffer, "request_credit", {"component": component, "bytes": len(data)})
    grant = incoming.receive(sys.stdin.buffer)
    if (grant["type"] != "grant" or grant["payload"].get("bytes") != len(data)
            or valid_uuid(grant["payload"].get("grant_id")) is None):
        raise FrameError("Invalid worker grant")
    path = _safe_external(Path(payload["root"]) / "snapshots" / payload["job_id"] / (component + ".jsonl"), Path(payload["root"]))
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_BINARY", 0), 0o660)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if not written:
                raise OSError("Prepare output short write")
            view = view[written:]
    finally:
        os.close(fd)
    outgoing.send(sys.stdout.buffer, "commit_growth", {"grant_id": grant["payload"]["grant_id"], "bytes": len(data)})
    buffers[component] = []


def work(job_id):
    inbound = JobChannel(job_id)
    outbound = JobChannel(job_id)
    payload = inbound.receive_prepare(sys.stdin.buffer)
    if payload.get("job_id") != job_id or not isinstance(payload.get("descriptors"), list):
        raise FrameError("Invalid prepare payload")
    root = _safe_external(Path(payload["root"]), Path(payload["root"]))
    directory = _safe_external(root / "snapshots" / job_id, root)
    if not directory.is_dir():
        raise FrameError("Missing prepare output")
    filter_data = payload.get("filter")
    if not isinstance(filter_data, dict):
        raise FrameError("Invalid prepare filter")
    filt = EventFilter(**{key: datetime.fromisoformat(value) if key in {"from_utc", "to_utc"} and value else value
                          for key, value in filter_data.items()})
    counts = {"events": 0, "invalid": 0, "truncated": 0, "expired": 0}
    gaps = set()
    buffers = {component: [] for component in ("backend", "frontend", "browser")}
    outbound.send(sys.stdout.buffer, "phase", {"name": "prepare"})
    try:
        for item in payload["descriptors"]:
            _read_descriptor(item, payload, filt, outbound, inbound, buffers, counts, gaps)
        for component in buffers:
            _flush(component, payload, outbound, inbound, buffers, counts)
    except PrepareTooLarge:
        outbound.send(sys.stdout.buffer, "result", {"status": "too_large"})
        return
    counts.pop("_selected_bytes", None)
    counts.pop("_frontend_bytes", None)
    names = [component + ".jsonl" for component in buffers if (directory / (component + ".jsonl")).exists()]
    outbound.send(sys.stdout.buffer, "result", {"status": "prepared", "pid": os.getpid(),
                                                "filenames": names, "counts": counts, "gaps": sorted(gaps)})


def main():
    if len(sys.argv) != 2 or valid_uuid(sys.argv[1]) is None:
        return 2
    try:
        work(sys.argv[1])
        return 0
    except Exception:
        # No raw source paths, content or exception text on stdout.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
