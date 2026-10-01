"""Immutable diagnostic capture policy built from startup settings."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

CaptureLevel = Literal["standard", "detailed"]


@dataclass(frozen=True)
class CapturePolicy:
    level: CaptureLevel
    version: int
    aggregate_interval_ms: int
    success_limit_per_second: int
    trace_limit_per_second: int
    slow_limit_per_second: int
    max_inflight_traces: int
    slow_thresholds_ms: Mapping[str, int]

    def snapshot(self) -> dict:
        return {
            "level": self.level, "version": self.version,
            "aggregate_interval_ms": self.aggregate_interval_ms,
            "success_limit_per_second": self.success_limit_per_second,
            "trace_limit_per_second": self.trace_limit_per_second,
            "slow_limit_per_second": self.slow_limit_per_second,
            "max_inflight_traces": self.max_inflight_traces,
            "slow_thresholds_ms": dict(self.slow_thresholds_ms),
        }


def build_policy(level: CaptureLevel, settings) -> CapturePolicy:
    if level not in ("standard", "detailed"):
        raise ValueError("Unknown capture level")
    return CapturePolicy(
        level=level, version=1,
        aggregate_interval_ms=settings.diagnostics_aggregate_interval_seconds * 1000,
        success_limit_per_second=settings.diagnostics_success_limit_per_second,
        trace_limit_per_second=settings.diagnostics_trace_limit_per_second,
        slow_limit_per_second=settings.diagnostics_slow_limit_per_second,
        max_inflight_traces=settings.diagnostics_max_inflight_traces,
        slow_thresholds_ms=MappingProxyType({
            "http_search": settings.diagnostics_slow_http_search_ms,
            "qdrant_db": settings.diagnostics_slow_qdrant_db_ms,
            "embeddings_proxy": settings.diagnostics_slow_embeddings_proxy_ms,
            "llm_chat": settings.diagnostics_slow_llm_chat_ms,
            "pdf": settings.diagnostics_slow_pdf_ms,
        }),
    )


def safe_policy_snapshot(value) -> dict | None:
    """Project persisted policy through the same narrow public contract as control."""
    if not isinstance(value, dict) or value.get("level") not in {"standard", "detailed"} or value.get("version") != 1:
        return None
    names = ("aggregate_interval_ms", "success_limit_per_second", "trace_limit_per_second",
             "slow_limit_per_second", "max_inflight_traces")
    thresholds = value.get("slow_thresholds_ms")
    threshold_names = ("http_search", "qdrant_db", "embeddings_proxy", "llm_chat", "pdf")
    if (any(type(value.get(name)) is not int or not 0 < value[name] <= 1_000_000 for name in names)
            or not isinstance(thresholds, dict)
            or any(type(thresholds.get(name)) is not int or not 0 < thresholds[name] <= 3_600_000
                   for name in threshold_names)):
        return None
    return {"level": value["level"], "version": 1,
            **{name: value[name] for name in names},
            "slow_thresholds_ms": {name: thresholds[name] for name in threshold_names}}


@dataclass(frozen=True)
class CaptureRuntimeView:
    session_id: str
    revision: int
    scope: str
    doc_id: str | None
    deadline_mono: float
    policy: CapturePolicy


@dataclass(frozen=True)
class CaptureDecision:
    action: Literal["record", "aggregate", "omit"]
    reason: str


class PolicyEngine:
    """One admission per root operation; failed events bypass success sampling."""

    _important = frozenset({
        "server_started", "server_stopped", "server_start_failed", "log_warning",
        "log_error", "operation_failed", "llm_parse_failed", "retry_scheduled",
        "dependency_status_changed", "proxy_failed", "render_failed",
        "browser_error", "diagnostic_gap", "offline_export_requested",
    })

    def __init__(self, *, monotonic=None):
        import threading
        import time
        self.monotonic = monotonic or time.monotonic
        self._lock = threading.Lock()
        self._session = None
        self._selected = set()
        self._rates = {}

    def _rate(self, name, limit):
        second = int(self.monotonic())
        window, used = self._rates.get(name, (-1, 0))
        used = used + 1 if window == second else 1
        self._rates[name] = (second, used)
        return used <= limit

    def _root(self, context):
        if context.operation_kind in {"search_chat", "interface"} and context.request_id:
            return context.request_id
        return context.operation_id or context.request_id

    def _switch_session(self, view):
        identity = (view.session_id, view.revision)
        if self._session is not None and view.revision < self._session[1]:
            return False
        if self._session != identity:
            self._session = identity
            self._selected.clear()
            self._rates.clear()
        return True

    def begin_trace(self, view: CaptureRuntimeView, context) -> bool:
        if view.policy.level != "detailed":
            return False
        root = self._root(context)
        if root is None:
            return False
        with self._lock:
            if not self._switch_session(view):
                return False
            key = (view.session_id, root)
            if key in self._selected:
                return True
            if len(self._selected) >= view.policy.max_inflight_traces:
                return False
            if not self._rate("trace", view.policy.trace_limit_per_second):
                return False
            self._selected.add(key)
            return True

    def finish_trace(self, view: CaptureRuntimeView, context):
        with self._lock:
            self._selected.discard((view.session_id, self._root(context)))

    def decide(self, view: CaptureRuntimeView, event_code: str, context, facts: dict) -> CaptureDecision:
        with self._lock:
            if not self._switch_session(view):
                return CaptureDecision("omit", "stale_session")
            success = facts.get("success")
            status = facts.get("http_status")
            if event_code in self._important or success is False or (
                type(status) is int and status >= 400
            ):
                return CaptureDecision("record", "important")
            policy = view.policy
            duration = facts.get("duration_ms")
            threshold = self._slow_threshold(event_code, facts, policy)
            slow = type(duration) in (int, float) and duration >= threshold
            if slow:
                root = self._root(context)
                if policy.level == "detailed" and (view.session_id, root) in self._selected:
                    return CaptureDecision("record", "selected_trace")
                if self._rate("slow", policy.slow_limit_per_second):
                    return CaptureDecision("record", "slow")
                return CaptureDecision("aggregate", "slow_limit")
            if policy.level == "standard":
                if event_code in {"dependency_call_finished", "request_finished"}:
                    return CaptureDecision("aggregate", "standard_success")
                if event_code == "operation_finished" and context.operation_kind == "search_chat":
                    if self._rate("summary", policy.success_limit_per_second):
                        return CaptureDecision("record", "operation_summary")
                    return CaptureDecision("aggregate", "summary_limit")
                if event_code in {"operation_started", "stage_started", "stage_finished"}:
                    if context.operation_kind == "document" and "chunk_index" not in facts:
                        return CaptureDecision("record", "document_stage")
                    return CaptureDecision("omit", "standard_detail")
                return CaptureDecision("record", "standard_other")
            root = self._root(context)
            if root is None:
                return CaptureDecision("aggregate", "untracked_success")
            key = (view.session_id, root)
            if key in self._selected:
                return CaptureDecision("record", "selected_trace")
            if event_code == "operation_started" and len(self._selected) < policy.max_inflight_traces and (
                self._rate("trace", policy.trace_limit_per_second)
            ):
                self._selected.add(key)
                return CaptureDecision("record", "selected_trace")
            if event_code in {"dependency_call_finished", "request_finished", "operation_finished"}:
                return CaptureDecision("aggregate", "sampled_out_trace")
            return CaptureDecision("omit", "sampled_out_trace")

    @staticmethod
    def _slow_threshold(event_code, facts, policy):
        dependency = facts.get("dependency")
        if dependency in {"qdrant", "database"}:
            name = "qdrant_db"
        elif dependency in {"embeddings", "backend"}:
            name = "embeddings_proxy"
        elif dependency == "pdf":
            name = "pdf"
        elif dependency == "llm" or facts.get("stage") == "chat":
            name = "llm_chat"
        else:
            name = "http_search"
        return policy.slow_thresholds_ms[name]
