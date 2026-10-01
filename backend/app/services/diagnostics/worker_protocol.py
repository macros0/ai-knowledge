"""Bounded, sequenced IPC and parent-owned credits for diagnostic workers."""
import base64
from hashlib import sha256
import json
import struct
from uuid import uuid4

from .sanitize import valid_uuid
from .store import StorageQuotaError

MAX_FRAME = 64 * 1024
MAX_MESSAGE = 4 * 1024 * 1024
_TYPES = frozenset({"prepare", "prepare_chunk", "request_credit", "grant",
                    "commit_growth", "phase", "result", "cancel"})


class FrameError(ValueError):
    pass


class JobChannel:
    def __init__(self, job_id: str):
        if valid_uuid(job_id) is None:
            raise FrameError("Invalid job identity")
        self.job_id = job_id
        self.sent = 0
        self.received = 0

    def send(self, stream, kind: str, payload: dict):
        if kind not in _TYPES or not isinstance(payload, dict):
            raise FrameError("Invalid worker message")
        sequence = self.sent + 1
        raw = json.dumps({"version": 1, "job_id": self.job_id, "seq": sequence,
                          "type": kind, "payload": payload},
                         ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(raw) > MAX_FRAME:
            raise FrameError("Worker frame too large")
        stream.write(struct.pack(">I", len(raw)) + raw)
        stream.flush()
        self.sent = sequence

    def send_prepare(self, stream, payload: dict):
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
        if len(raw) > MAX_MESSAGE:
            raise FrameError("Prepare message too large")
        chunks = [raw[index:index + 40000] for index in range(0, len(raw), 40000)] or [b""]
        self.send(stream, "prepare", {"bytes": len(raw), "chunks": len(chunks),
                                      "sha256": sha256(raw).hexdigest()})
        for index, chunk in enumerate(chunks):
            self.send(stream, "prepare_chunk", {"index": index,
                                               "data": base64.b64encode(chunk).decode("ascii")})

    def receive_prepare(self, stream) -> dict:
        header = self.receive(stream)
        if header["type"] != "prepare":
            raise FrameError("Expected prepare header")
        meta = header["payload"]
        if (set(meta) != {"bytes", "chunks", "sha256"}
                or type(meta["bytes"]) is not int or not 0 <= meta["bytes"] <= MAX_MESSAGE
                or type(meta["chunks"]) is not int or not 1 <= meta["chunks"] <= 106
                or not isinstance(meta["sha256"], str) or len(meta["sha256"]) != 64):
            raise FrameError("Invalid prepare header")
        parts = []
        total = 0
        for index in range(meta["chunks"]):
            frame = self.receive(stream)
            payload = frame["payload"]
            if frame["type"] != "prepare_chunk" or set(payload) != {"index", "data"} or payload["index"] != index:
                raise FrameError("Incomplete prepare sequence")
            try:
                part = base64.b64decode(payload["data"], validate=True)
            except (ValueError, TypeError) as exc:
                raise FrameError("Invalid prepare chunk") from exc
            total += len(part)
            if total > MAX_MESSAGE:
                raise FrameError("Prepare message too large")
            parts.append(part)
        raw = b"".join(parts)
        if total != meta["bytes"] or sha256(raw).hexdigest() != meta["sha256"]:
            raise FrameError("Incomplete prepare message")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise FrameError("Invalid prepare JSON") from exc
        if not isinstance(result, dict):
            raise FrameError("Invalid prepare message")
        return result

    def receive(self, stream) -> dict:
        header = stream.read(4)
        if len(header) != 4:
            raise FrameError("Truncated worker frame")
        size = struct.unpack(">I", header)[0]
        if not 0 < size <= MAX_FRAME:
            raise FrameError("Worker frame too large")
        raw = stream.read(size)
        if len(raw) != size:
            raise FrameError("Truncated worker frame")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise FrameError("Invalid worker JSON") from exc
        if (not isinstance(value, dict) or set(value) != {"version", "job_id", "seq", "type", "payload"}
                or value["version"] != 1 or value["job_id"] != self.job_id
                or type(value["seq"]) is not int or value["seq"] != self.received + 1
                or value["type"] not in _TYPES or not isinstance(value["payload"], dict)):
            raise FrameError("Invalid worker sequence or message")
        self.received += 1
        return value


class QuotaBroker:
    """Only the parent can grant and commit child growth at a job-owned path."""
    def __init__(self, store):
        self.store = store
        self._grants = {}

    def _owned(self, job_id, path):
        if valid_uuid(job_id) is None:
            raise ValueError("Invalid job identity")
        path = self.store.safe_path(path)
        snapshot_dir = self.store.root / "snapshots" / job_id
        bundle_path = self.store.root / "bundles" / (job_id + ".part")
        if path != bundle_path and not (path.parent == snapshot_dir and path.name in {
                "backend.jsonl", "frontend.jsonl", "browser.jsonl", "prepared.jsonl"}):
            raise ValueError("Worker path does not belong to job")
        return path

    def grant(self, job_id: str, size: int, path):
        path = self._owned(job_id, path)
        if type(size) is not int or not 0 < size <= MAX_MESSAGE or len(self._grants) >= 1024:
            raise ValueError("Invalid worker credit")
        reservation = self.store.reserve(size)
        grant_id = str(uuid4())
        self._grants[grant_id] = (job_id, path, size, reservation)
        return grant_id

    def commit(self, grant_id: str, actual_bytes: int):
        item = self._grants.get(grant_id)
        if item is None:
            raise ValueError("Unknown worker credit")
        _, path, size, reservation = item
        if type(actual_bytes) is not int or not 0 <= actual_bytes <= size:
            raise ValueError("Invalid worker growth")
        with self.store._mutex:
            before = self.store._index.size(path)
            if not path.exists() or path.stat().st_size - before != actual_bytes:
                raise ValueError("Worker growth differs from committed bytes")
            self.store.commit_external_growth(reservation, path)
            reservation.release()
            del self._grants[grant_id]

    def outstanding(self, job_id: str) -> int:
        return sum(identity == job_id for identity, _, _, _ in self._grants.values())

    def release_job(self, job_id: str):
        # Call only after the child has exited. Failed deletion can leave bytes
        # written before its commit frame: settle them before returning credit.
        with self.store._mutex:
            for grant_id, (identity, path, _, reservation) in tuple(self._grants.items()):
                if identity != job_id:
                    continue
                try:
                    try:
                        path.stat()
                    except FileNotFoundError:
                        pass
                    else:
                        self.store.commit_external_growth(reservation, path)
                except (OSError, ValueError, StorageQuotaError):
                    # If even accounting is unavailable, retain the grant. A
                    # reopened store scans the surviving files before writing.
                    self.store._degraded = True
                    self.store._last_failure = "storage_error"
                    continue
                reservation.release()
                del self._grants[grant_id]
