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
        store.note_rename(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
        store.note_deleted(temporary)


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
                self.store.note_rename(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
                self.store.note_deleted(temporary)


def write_projection(store: DiagnosticStore, *, boot_id: str, active: dict | None,
                     now: datetime, revision: int = 0) -> bool:
    # Serialize the small control projection independently of the hot session
    # selector. A delayed heartbeat/start cannot revive a stopped revision.
    with store._projection_lock:
        current = store._projection_revision
        if current is not None and current[0] == boot_id and revision < current[1]:
            return False
        value = {"schema_version": 2, "boot_id": boot_id, "active": False,
                 "revision": revision, "lease_until": now.timestamp() + 10}
        if active and active["scope"] in {"system", "interface"}:
            source = active.get("policy_snapshot") or {}
            thresholds = source.get("slow_thresholds_ms")
            keys = ("aggregate_interval_ms", "success_limit_per_second",
                    "trace_limit_per_second", "slow_limit_per_second", "max_inflight_traces")
            threshold_keys = ("http_search", "qdrant_db", "embeddings_proxy", "llm_chat", "pdf")
            if (source.get("level") in {"standard", "detailed"} and source.get("version") == 1
                    and all(type(source.get(key)) is int and source[key] > 0 for key in keys)
                    and isinstance(thresholds, dict)
                    and all(type(thresholds.get(key)) is int and thresholds[key] > 0 for key in threshold_keys)):
                policy = {"level": source["level"], "version": 1,
                          **{key: source[key] for key in keys},
                          "slow_thresholds_ms": {key: thresholds[key] for key in threshold_keys}}
                value.update(active=True, session_id=active["id"], scope=active["scope"],
                             deadline=active["expires_at"].timestamp(), policy=policy)
        atomic_json(store, store.root / "control/capture.json", value)
        store._projection_revision = (boot_id, revision)
        return True
