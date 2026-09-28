"""Bounded filesystem control journal; independent of DB and business pipelines."""
from datetime import datetime
import json
import os

from .sanitize import valid_uuid
from .store import DiagnosticStore, StorageQuotaError


def atomic_json(store: DiagnosticStore, path, value: dict):
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    temporary = store.safe_path(path.with_suffix(".tmp"))
    try:
        store.write_bytes(temporary, encoded)
        os.replace(temporary, store.safe_path(path))
    finally:
        temporary.unlink(missing_ok=True)


class DeferredControlJournal:
    def __init__(self, store: DiagnosticStore):
        self.store = store
        self.path = store.root / "control/deferred.jsonl"

    def append(self, event: dict):
        if valid_uuid(event.get("event_id")) is None or valid_uuid(event.get("target_id")) is None:
            raise ValueError("Invalid diagnostic control event")
        if event.get("action") != "diagnostic_session_stopped":
            raise ValueError("Unsupported control action")
        if event.get("reason") not in {"manual", "expired", "size_limit", "storage_low", "storage_error", "server_restarted", "disabled"}:
            raise ValueError("Invalid control reason")
        encoded = (json.dumps(event, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode()
        if len(encoded) > 4096:
            raise ValueError("Oversized control event")
        with self.store._mutex:
            path = self.store.safe_path(self.path)
            if path.exists() and path.stat().st_size + len(encoded) > self.store.limits.control_bytes:
                raise StorageQuotaError("Diagnostic control journal is full")
            if any(item["event_id"] == event["event_id"] for item in self.items()):
                return
            self.store.write_bytes(path, encoded, append=True)
            # Stop events must survive a process crash after local capture was disabled.
            with path.open("r+b") as handle:
                os.fsync(handle.fileno())

    def items(self) -> list[dict]:
        path = self.store.safe_path(self.path)
        if not path.exists():
            return []
        if path.stat().st_size > self.store.limits.control_bytes:
            raise ValueError("Oversized control journal")
        result = []
        with path.open("rb") as handle:
            for line in handle:
                if len(line) > 4096 or not line.endswith(b"\n"):
                    raise ValueError("Invalid control journal line")
                item = json.loads(line)
                if valid_uuid(item.get("event_id")) is None or valid_uuid(item.get("target_id")) is None:
                    raise ValueError("Invalid control event identity")
                result.append(item)
        return result

    def remove(self, event_id: str):
        with self.store._mutex:
            remaining = [event for event in self.items() if event["event_id"] != event_id]
            temporary = self.path.with_suffix(".tmp")
            encoded = b"".join((json.dumps(e, separators=(",", ":"), ensure_ascii=False) + "\n").encode() for e in remaining)
            try:
                self.store.write_bytes(temporary, encoded)
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)


def write_projection(store: DiagnosticStore, *, boot_id: str, active: dict | None, now: datetime):
    value = {"schema_version": 1, "boot_id": boot_id, "active": False,
             "lease_until": now.timestamp() + 10}
    if active and active["scope"] in {"system", "interface"}:
        value.update(active=True, session_id=active["id"], scope=active["scope"],
                     deadline=active["expires_at"].timestamp())
    atomic_json(store, store.root / "control/capture.json", value)
