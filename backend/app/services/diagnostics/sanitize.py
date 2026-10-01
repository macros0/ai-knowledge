"""Fail-closed, content-free events. Never format an exception or log message."""
import builtins
import json
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
import re
from uuid import UUID

from app import error_codes, services
from app.services import problem_codes
from .schema import (
    AGGREGATE_BUCKET_KEYS, AGGREGATE_COUNT_KEYS, BASE_FIELDS, COUNT_KEYS, DEPENDENCIES,
    EVENT_FIELDS_V1, EVENT_FIELDS_V2, MAX_EVENT_BYTES,
    MAX_STACK_FRAMES, ROUTE_TEMPLATES, STAGES, FRONTEND_FRAME_MODULES,
)

_APP_ROOT = Path(services.__file__).resolve().parent.parent
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,100}\Z")
_OPAQUE = re.compile(r"(?:[0-9a-f]{16}|[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\Z")
_ERROR_CODES = frozenset(
    value for module in (error_codes, problem_codes) for name, value in vars(module).items()
    if name.isupper() and type(value) is str
) | {"browser_error", "render_failed", "network_error", "incomplete_stream"}
_SAFE_EXCEPTIONS = frozenset({
    "UnknownError", "DependencyUnavailableError", "LLMTruncationError", "VectorStoreError",
    "StorageFullError", "ApiError", "HTTPException", "TimeoutException", "ConnectError",
    "OperationalError", "IntegrityError", "DataError", "ProgrammingError",
    "Error", "ReferenceError", "RangeError", "URIError", "EvalError", "AggregateError",
}) | frozenset(name for name, value in vars(builtins).items()
               if isinstance(value, type) and issubclass(value, BaseException))


def valid_uuid(value: object) -> str | None:
    if type(value) is not str or len(value) != 36:
        return None
    try:
        result = str(UUID(value))
        return result if result == value else None
    except ValueError:
        return None


def _frames(value: object, component: str) -> list[dict] | None:
    if type(value) is not list:
        return None
    result = []
    for frame in value[:MAX_STACK_FRAMES]:
        if type(frame) is not dict or set(frame) != {"module", "function", "line"}:
            return None
        module, function, line = frame["module"], frame["function"], frame["line"]
        if type(module) is not str or type(function) is not str or type(line) is not int:
            return None
        if not 0 <= line <= 100000 or not _IDENTIFIER.fullmatch(function):
            return None
        if component in {"frontend", "browser"}:
            if function not in {"server", "client", "external"}:
                return None
            asset = re.fullmatch(r"frontend/_next/[a-f0-9]{8,64}\.js", module)
            if module != "external_frame" and not asset and (
                component != "frontend" or module not in FRONTEND_FRAME_MODULES
            ):
                return None
        elif module != "external_frame":
            if not re.fullmatch(r"app/(?:[a-zA-Z_][a-zA-Z0-9_]*/)*[a-zA-Z_][a-zA-Z0-9_]*\.py", module):
                return None
            path = _APP_ROOT / module[4:]
            if not path.is_file() or not path.resolve().is_relative_to(_APP_ROOT):
                return None
        result.append({"module": module, "function": function, "line": line})
    return result


def sanitize_event(raw: Mapping[str, object]) -> dict | None:
    if not isinstance(raw, Mapping):
        return None
    version = raw.get("schema_version")
    if type(version) is not int or version not in (1, 2):
        return None
    event_fields = EVENT_FIELDS_V1 if version == 1 else EVENT_FIELDS_V2
    code = raw.get("event_code")
    if type(code) is not str or code not in event_fields:
        return None
    if set(raw) - (BASE_FIELDS | event_fields[code]):
        return None
    if code == "success_aggregate" and ("request_id" in raw or "operation_id" in raw):
        return None
    if type(raw.get("component")) is not str or raw["component"] not in {"backend", "frontend", "browser"}:
        return None
    if type(raw.get("level")) is not str or raw["level"] not in {"INFO", "WARN", "ERROR"}:
        return None
    if type(raw.get("origin")) is not str or raw["origin"] not in {"server", "client_reported"}:
        return None
    if (raw.get("component") == "browser") != (raw.get("origin") == "client_reported"):
        return None
    result = dict(raw)
    for key in ("event_id", "boot_id", "request_id", "operation_id", "diagnostic_session_id"):
        if key in raw and valid_uuid(raw[key]) is None:
            return None
    if "event_id" not in raw or "boot_id" not in raw:
        return None
    for key in ("doc_id", "generation_id"):
        if key in raw and (type(raw[key]) is not str or not _OPAQUE.fullmatch(raw[key])):
            return None
    try:
        for key in ("timestamp_utc", "first_timestamp_utc", "last_timestamp_utc",
                    "window_start_utc", "window_end_utc"):
            if key != "timestamp_utc" and key not in raw:
                continue
            stamp = raw.get(key)
            if type(stamp) is not str or len(stamp) > 40:
                return None
            parsed = datetime.fromisoformat(stamp)
            if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
                return None
            result[key] = parsed.astimezone(timezone.utc).isoformat()
        if code == "repeat_summary" and (
            not {"source_event_code", "first_timestamp_utc", "last_timestamp_utc", "counts"}.issubset(raw)
            or "repeats" not in raw.get("counts", {})
            or result["first_timestamp_utc"] > result["last_timestamp_utc"]
        ):
            return None
        if code == "success_aggregate" and (
            not {"window_start_utc", "window_end_utc", "counts", "outcome"}.issubset(raw)
            or result["window_start_utc"] > result["window_end_utc"]
        ):
            return None
        if code == "operation_summary" and not {"stage", "duration_ms", "counts", "outcome"}.issubset(raw):
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    for key in ("duration_ms", "chunk_index", "retry_index", "http_status"):
        if key in raw:
            value = raw[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10**12:
                return None
            if key != "duration_ms" and type(value) is not int:
                return None
    if "http_status" in raw and not 100 <= raw["http_status"] <= 599:
        return None
    enums = {
        "error_code": _ERROR_CODES, "exception_type": _SAFE_EXCEPTIONS,
        "route_template": ROUTE_TEMPLATES, "stage": STAGES, "dependency": DEPENDENCIES,
        "http_method": {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
        "dependency_status": {"ok", "down", "rate_limited", "unknown"},
        "source_event_code": set(event_fields) - {"repeat_summary"},
        "outcome": {"success", "cancelled"} if code == "operation_summary" else {"success"},
    }
    for key, allowed in enums.items():
        if key in raw and (type(raw[key]) is not str or raw[key] not in allowed):
            return None
    if "build_id" in raw and (
        type(raw["build_id"]) is not str
        or not re.fullmatch(r"(?:unknown|[a-f0-9]{7,64})", raw["build_id"])
    ):
        return None
    if "counts" in raw:
        counts = raw["counts"]
        allowed_counts = AGGREGATE_COUNT_KEYS if code == "success_aggregate" else COUNT_KEYS
        if type(counts) is not dict or set(counts) - allowed_counts:
            return None
        if any(type(v) is not int or not 0 <= v <= 10**12 for v in counts.values()):
            return None
        if code == "success_aggregate" and (
            set(counts) != AGGREGATE_COUNT_KEYS or counts["count"] < 1
            or sum(counts[key] for key in AGGREGATE_BUCKET_KEYS) != counts["count"]
            or counts["duration_max_us"] > counts["duration_sum_us"]
        ):
            return None
    if "frames" in raw:
        frames = _frames(raw["frames"], raw["component"])
        if frames is None:
            return None
        result["frames"] = frames
    return result


def encode_event(event: Mapping[str, object]) -> bytes:
    cleaned = sanitize_event(event)
    if cleaned is None:
        raise ValueError("Invalid diagnostic event")
    return _encode_validated_event(cleaned)


def _encode_validated_event(cleaned: dict) -> bytes:
    """Encode an event returned by sanitize_event without validating it again."""
    encoded = (json.dumps(cleaned, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()
    while len(encoded) > MAX_EVENT_BYTES and cleaned.get("frames"):
        cleaned["frames"].pop()
        encoded = (json.dumps(cleaned, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()
    if len(encoded) > MAX_EVENT_BYTES:
        raise ValueError("Diagnostic event exceeds limit")
    return encoded


def safe_exception(exc: BaseException) -> dict:
    cls = type(exc)
    name = cls.__name__ if cls.__module__ in {"builtins", "app.api.errors", "app.services.errors"} else "UnknownError"
    result = {"exception_type": name if name in _SAFE_EXCEPTIONS else "UnknownError", "frames": []}
    tb = exc.__traceback__
    while tb is not None and len(result["frames"]) < MAX_STACK_FRAMES:
        code = tb.tb_frame.f_code
        path = Path(code.co_filename)
        module = "external_frame"
        if path.is_absolute():
            try:
                relative = path.resolve().relative_to(_APP_ROOT)
                module = "app/" + relative.as_posix()
            except ValueError:
                pass
        function = code.co_name if _IDENTIFIER.fullmatch(code.co_name) else "unknown"
        result["frames"].append({"module": module, "function": function, "line": tb.tb_lineno})
        tb = tb.tb_next
    return result
