"""Diagnostics v1 contracts. This module does not load config, DB or workers."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

MIB = 1048576
MAX_EVENT_BYTES = 8192
MAX_STACK_FRAMES = 32
CaptureScope = Literal["system", "document", "search_chat", "interface"]
StopReason = Literal[
    "manual", "expired", "size_limit", "storage_low", "storage_error",
    "server_restarted", "disabled",
]


@dataclass(frozen=True)
class DiagnosticContext:
    request_id: str | None = None
    operation_id: str | None = None
    doc_id: str | None = None
    generation_id: str | None = None
    # Internal selector only; never serialized as an event field.
    operation_kind: CaptureScope | None = None


@dataclass(frozen=True)
class DiagnosticLimits:
    total_bytes: int = 500 * MIB
    backend_bytes: int = 480 * MIB
    frontend_bytes: int = 20 * MIB
    baseline_bytes: int = 50 * MIB
    session_bytes: int = 100 * MIB
    segment_bytes: int = 5 * MIB
    bundle_bytes: int = 200 * MIB
    min_free_bytes: int = 2048 * MIB
    baseline_seconds: int = 7 * 86400
    capture_seconds: int = 24 * 3600
    bundle_seconds: int = 24 * 3600
    queue_size: int = 4096
    maintenance_seconds: int = 60
    control_bytes: int = MIB

    def __post_init__(self):
        for key, value in vars(self).items():
            if type(value) is not int or value < (0 if key == "min_free_bytes" else 1):
                raise ValueError("Invalid diagnostics limit")
        if self.backend_bytes + self.frontend_bytes != self.total_bytes:
            raise ValueError("Diagnostics budgets must add up to the total")
        if max(self.baseline_bytes, self.session_bytes, self.bundle_bytes) > self.backend_bytes:
            raise ValueError("Diagnostics sub-budget exceeds backend budget")
        if self.segment_bytes > min(self.baseline_bytes, self.session_bytes):
            raise ValueError("Diagnostics segment exceeds stream budget")


@dataclass(frozen=True)
class EventFilter:
    from_utc: datetime | None = None
    to_utc: datetime | None = None
    session_id: str | None = None
    request_id: str | None = None
    operation_id: str | None = None
    doc_id: str | None = None

    def matches(self, event: dict) -> bool:
        stamp = datetime.fromisoformat(event["timestamp_utc"]).astimezone(timezone.utc)
        if self.from_utc and stamp < self.from_utc:
            return False
        if self.to_utc and stamp > self.to_utc:
            return False
        return all(not value or event.get(key) == value for key, value in (
            ("diagnostic_session_id", self.session_id), ("request_id", self.request_id),
            ("operation_id", self.operation_id), ("doc_id", self.doc_id),
        ))

    @classmethod
    def last_hour(cls, now: datetime | None = None):
        now = now or datetime.now(timezone.utc)
        return cls(from_utc=now - timedelta(hours=1), to_utc=now)


BASE_FIELDS = frozenset({
    "schema_version", "event_id", "timestamp_utc", "boot_id", "component", "level",
    "event_code", "origin", "request_id", "operation_id", "diagnostic_session_id",
    "doc_id", "generation_id",
})
ERROR_FIELDS = frozenset({"error_code", "exception_type", "frames"})
HTTP_FIELDS = frozenset({"route_template", "http_method", "http_status", "duration_ms"})
OPERATION_FIELDS = frozenset({"stage", "chunk_index", "retry_index", "counts", "duration_ms"})
EVENT_FIELDS = {
    "server_started": frozenset({"build_id"}), "server_stopped": frozenset(),
    "server_start_failed": ERROR_FIELDS,
    "log_warning": ERROR_FIELDS, "log_error": ERROR_FIELDS,
    "request_finished": HTTP_FIELDS | ERROR_FIELDS,
    "operation_started": OPERATION_FIELDS,
    "stage_started": OPERATION_FIELDS, "stage_finished": OPERATION_FIELDS,
    "dependency_call_finished": OPERATION_FIELDS | ERROR_FIELDS | {"dependency", "http_status"},
    "retry_scheduled": OPERATION_FIELDS | ERROR_FIELDS | {"dependency"},
    "operation_failed": OPERATION_FIELDS | ERROR_FIELDS,
    "operation_finished": OPERATION_FIELDS,
    "dependency_status_changed": frozenset({"dependency", "dependency_status", "error_code"}),
    "llm_parse_failed": OPERATION_FIELDS | ERROR_FIELDS,
    "proxy_failed": HTTP_FIELDS | ERROR_FIELDS,
    "render_failed": HTTP_FIELDS | ERROR_FIELDS,
    "browser_error": HTTP_FIELDS | frozenset({"error_code", "frames", "build_id"}),
    "diagnostic_gap": frozenset({"counts"}),
    "offline_export_requested": frozenset(),
    "repeat_summary": ERROR_FIELDS | HTTP_FIELDS | OPERATION_FIELDS | frozenset({
        "source_event_code", "first_timestamp_utc", "last_timestamp_utc", "dependency",
    }),
}
COUNT_KEYS = frozenset({
    "processed", "total", "concepts", "chunks", "points", "attempts", "repeats",
    "dropped", "invalid", "truncated", "bytes", "omitted_frames", "expired_queue",
})
STAGES = frozenset({"queue", "parse", "split", "generate", "index", "publish", "cleanup", "search", "chat"})
DEPENDENCIES = frozenset({"database", "qdrant", "llm", "embeddings", "pdf", "backend"})
FRONTEND_FRAME_MODULES = frozenset({
    "frontend/src/instrumentation.js", "frontend/src/proxy.js",
    "frontend/src/lib/backendFetch.js", "frontend/src/lib/requestContext.mjs",
    "frontend/src/lib/diagnosticServer.mjs", "frontend/src/lib/diagnosticSchema.mjs",
    "frontend/src/app/api/[...path]/route.js", "frontend/src/app/layout.js",
    "frontend/src/app/page.js", "frontend/src/app/error.js", "frontend/src/app/global-error.js",
    "frontend/src/app/admin/page.js", "frontend/src/app/admin/diagnostics/page.js",
    "frontend/src/components/DiagnosticsPanel.jsx", "frontend/src/components/ChatPanel.jsx",
})
# Only static route templates; unknown routes use /unknown, never the raw path.
ROUTE_TEMPLATES = frozenset({
    "/unknown", "/", "/health", "/health/ready", "/api/chat", "/api/chat/stream",
    "/api/search", "/api/documents", "/api/documents/{doc_id}",
    "/api/documents/{doc_id}/resume", "/api/documents/{doc_id}/regenerate",
    "/api/documents/{doc_id}/chunks", "/api/documents/{doc_id}/fulltext",
    "/api/jobs", "/api/jobs/{job_id}", "/api/jobs/{job_id}/export/{part_number}",
    "/api/auth/me", "/api/auth/login", "/api/auth/callback", "/api/auth/logout",
    "/api/auth/simulate", "/api/settings", "/api/i18n/{locale}",
    "/admin/diagnostics", "/documents/{doc_id}",
    "/api/admin/diagnostics/status", "/api/admin/diagnostics/events/query",
    "/api/admin/diagnostics/sessions", "/api/admin/diagnostics/sessions/{session_id}/stop",
    "/api/admin/diagnostics/bundles", "/api/admin/diagnostics/bundles/{bundle_id}/preview",
    "/api/admin/diagnostics/bundles/{bundle_id}/download",
})
