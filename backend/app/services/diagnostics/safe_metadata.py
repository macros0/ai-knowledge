"""Small, dependency-free validators shared by the parent and ZIP child."""
import re


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
