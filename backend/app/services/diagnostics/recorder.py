"""Nonblocking bounded recording; the worker never needs a database connection."""
from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import logging
import json
from queue import Empty, Full, Queue
import threading
import time
from uuid import uuid4

from .aggregation import AggregateBuffer, AggregateKey
from .policy import PolicyEngine
from .sanitize import encode_event, safe_exception
from .schema import (DEPENDENCIES, EVENT_FIELDS_V2, ROUTE_TEMPLATES, STAGES,
                     DiagnosticContext)
from .store import DiagnosticStore

_runtime_recorder = None
SUCCESS_REQUESTS_PER_SECOND_PER_ROUTE = 20


@dataclass
class _DrainBarrier:
    session_id: str | None
    done: threading.Event


class AdmissionQueue:
    """Single FIFO with a bounded normal share and reserved important slots."""

    def __init__(self, size: int, *, default_reserve=512):
        if type(size) is not int or size < 1:
            raise ValueError("Invalid diagnostic queue size")
        self._queue = Queue(maxsize=size)
        self.reserve = min(default_reserve, max(1, size // 8))
        self._normal_limit = size - self.reserve
        self._normal_queued = 0
        self._lock = threading.Lock()

    def offer(self, envelope, *, important: bool) -> bool:
        with self._lock:
            if not important and self._normal_queued >= self._normal_limit:
                return False
            try:
                self._queue.put_nowait((envelope, important))
            except Full:
                return False
            if not important:
                self._normal_queued += 1
            return True

    def get(self, timeout=None):
        envelope, important = self._queue.get(timeout=timeout)
        if not important:
            with self._lock:
                self._normal_queued -= 1
        return envelope

    def get_nowait(self):
        envelope, important = self._queue.get_nowait()
        if not important:
            with self._lock:
                self._normal_queued -= 1
        return envelope

    def put(self, envelope, timeout=None):
        self._queue.put((envelope, True), timeout=timeout)

    def put_nowait(self, envelope):
        self._queue.put_nowait((envelope, True))

    def task_done(self):
        self._queue.task_done()

    def join(self):
        self._queue.join()

    def empty(self):
        return self._queue.empty()

    def qsize(self):
        return self._queue.qsize()


class DiagnosticLogHandler(logging.Handler):
    def __init__(self, recorder):
        super().__init__(logging.WARNING)
        self.recorder = recorder

    def emit(self, record):
        if record.name != "root" and not record.name.startswith("app."):
            return
        # record.msg/args/getMessage/exception text are never touched.
        exc = record.exc_info[1] if record.exc_info and record.exc_info[1] else None
        if exc is not None:
            from .context import exception_recorded
            if exception_recorded(exc):
                return
        self.recorder.emit("log_error" if record.levelno >= logging.ERROR else "log_warning",
                           exception=exc)


class DiagnosticRecorder:
    def __init__(self, store: DiagnosticStore, *, capture_selector=None, capture_view_selector=None,
                 boot_id=None, baseline_enabled=True, on_capture_written=None, on_capture_failed=None):
        self.store = store
        self.boot_id = str(boot_id or uuid4())
        self.started_at_utc = datetime.now(timezone.utc).isoformat()
        self.capture_selector = capture_selector or (lambda *_: None)
        self.capture_view_selector = capture_view_selector
        self._policy = PolicyEngine()
        self._aggregates = AggregateBuffer()
        self._aggregate_views = {}
        self._aggregate_deadlines = {}
        self._aggregate_next_flush = float("inf")
        self._batches = {}
        self._batch_started = {}
        self._batch_bytes = {}
        self.baseline_enabled = baseline_enabled
        self.on_capture_written = on_capture_written or (lambda *_: None)
        self.on_capture_failed = on_capture_failed or (lambda *_: None)
        self.handler = DiagnosticLogHandler(self)
        self._queue = AdmissionQueue(store.limits.queue_size)
        self._stop = threading.Event()
        self._thread = None
        self._counts = Counter()
        self._counts_lock = threading.Lock()
        self._dedup = OrderedDict()
        self._accepting = True
        self._admission_lock = threading.Lock()
        self._closed_captures = OrderedDict()
        self.monotonic = time.monotonic
        self._success_session = None
        self._success_windows = {}

    def _count(self, key, number=1):
        with self._counts_lock:
            self._counts[key] += number

    def begin_trace(self, context: DiagnosticContext) -> bool:
        if not self._accepting or self.capture_view_selector is None:
            return False
        view = self.capture_view_selector(context, "request_finished")
        return bool(view and self._policy.begin_trace(view, context))

    def emit(self, event_code: str, *, context: DiagnosticContext | None = None,
             exception: BaseException | None = None, fields=None) -> bool:
        if not self._accepting:
            return False
        try:
            if context is None:
                from .context import current_context
                context = current_context()
            fields = fields or {}
            view = self.capture_view_selector(context, event_code) if self.capture_view_selector else None
            session_id = view.session_id if view else self.capture_selector(context, event_code)
            baseline = event_code in {
                "log_warning", "log_error", "operation_failed", "llm_parse_failed",
                "server_started", "server_stopped", "server_start_failed", "dependency_status_changed",
                "proxy_failed", "render_failed", "diagnostic_gap",
            }
            if event_code == "request_finished" and fields.get("http_status", 0) >= 400:
                baseline = True
            level = "ERROR" if exception or event_code.endswith(("failed", "error")) else "WARN" if event_code == "log_warning" else "INFO"
            if event_code == "request_finished":
                status = fields.get("http_status", 0)
                level = "ERROR" if status >= 500 or exception else "WARN" if status >= 400 else "INFO"
            baseline = baseline or level in {"WARN", "ERROR"}
            if not session_id and not (baseline and self.baseline_enabled):
                return False
            if view and session_id:
                facts = {key: fields[key] for key in ("route_template", "stage", "dependency", "duration_ms",
                                                      "http_status", "chunk_index") if key in fields}
                facts["success"] = exception is None and level == "INFO"
                decision = self._policy.decide(view, event_code, context, facts)
                # Document operations have their own root even when spawned by HTTP.
                # Search/interface child operations share the request root until HTTP ends.
                if event_code == "request_finished" or (
                    event_code in {"operation_finished", "operation_failed", "operation_summary"}
                    and (not context.request_id or context.operation_kind not in {"search_chat", "interface"})
                ):
                    self._policy.finish_trace(view, context)
                if decision.action == "aggregate":
                    self._observe_success(view, event_code, context, fields)
                    if decision.reason == "slow_limit":
                        self._count("sampled_out_slow")
                    elif decision.reason == "sampled_out_trace":
                        self._count("sampled_out_traces")
                    return True
                if decision.action == "omit":
                    self._count("sampled_out_traces" if decision.reason == "sampled_out_trace" else "policy_omitted")
                    return False
                if decision.reason == "operation_summary":
                    event_code = "operation_summary"
                    fields = {**fields, "stage": fields.get("stage", "search"), "outcome": "success"}
            event = {
                "schema_version": 2 if view else 1, "event_id": str(uuid4()), "boot_id": self.boot_id,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "component": "backend", "level": level, "event_code": event_code, "origin": "server",
                **{key: value for key, value in asdict(context).items()
                   if value is not None and key != "operation_kind"},
                **fields,
            }
            if exception:
                event.update(safe_exception(exception))
            if session_id:
                event["diagnostic_session_id"] = session_id
            encoded = encode_event(event)
            with self._admission_lock:
                if session_id in self._closed_captures:
                    if not baseline or not self.baseline_enabled:
                        return False
                    event.pop("diagnostic_session_id", None)
                    encoded = encode_event(event)
                    session_id = None
                if (session_id and not view and event_code == "request_finished"
                        and 200 <= event.get("http_status", 0) < 400):
                    if self._success_session != session_id:
                        self._success_session = session_id
                        self._success_windows.clear()
                    route = event.get("route_template", "/unknown")
                    second = int(self.monotonic())
                    window, count = self._success_windows.get(route, (second, 0))
                    count = count + 1 if window == second else 1
                    self._success_windows[route] = (second, count)
                    if count > SUCCESS_REQUESTS_PER_SECOND_PER_ROUTE:
                        self._count("sampled_success")
                        return False
                important = level in {"WARN", "ERROR"} or event_code in {
                    "server_started", "server_stopped", "retry_scheduled", "dependency_status_changed",
                    "diagnostic_gap", "offline_export_requested",
                }
                if not self._queue.offer((encoded, session_id, baseline, context, event_code),
                                         important=important):
                    raise Full
            if exception is not None:
                from .context import current_context, mark_recorded_exception
                if context == current_context():
                    mark_recorded_exception(exception, event_code)
            return True
        except Full:
            self._count("dropped")
        except Exception:
            self._count("invalid")
        return False

    def _observe_success(self, view, event_code, context, fields):
        if set(fields) - EVENT_FIELDS_V2.get(event_code, frozenset()):
            raise ValueError("Invalid aggregate source")
        duration = fields.get("duration_ms", 0)
        if type(duration) not in (int, float) or not 0 <= duration <= 10**9:
            raise ValueError("Invalid aggregate duration")
        route = fields.get("route_template")
        stage = fields.get("stage")
        dependency = fields.get("dependency")
        key = AggregateKey(view.session_id, "backend",
                           route if route in ROUTE_TEMPLATES else "/unknown",
                           stage if stage in STAGES else None,
                           dependency if dependency in DEPENDENCIES else None, "success")
        with self._admission_lock:
            if view.session_id in self._closed_captures:
                raise ValueError("Closed capture")
            self._aggregates.observe(key, int(duration * 1000), "success")
            self._aggregate_views[view.session_id] = view
            if view.session_id not in self._aggregate_deadlines:
                deadline = self.monotonic() + view.policy.aggregate_interval_ms / 1000
                self._aggregate_deadlines[view.session_id] = deadline
                self._aggregate_next_flush = min(self._aggregate_next_flush, deadline)
        self._count("aggregated_success")

    def _flush_aggregates(self, *, due_only=False):
        with self._admission_lock:
            now = self.monotonic()
            views = tuple(view for view in self._aggregate_views.values()
                          if not due_only or now >= self._aggregate_deadlines[view.session_id])
            entries = [(view, aggregate) for view in views for aggregate in
                       self._aggregates.flush(view.session_id, self.monotonic())]
            for view in views:
                self._aggregate_views.pop(view.session_id)
                self._aggregate_deadlines.pop(view.session_id)
            self._aggregate_next_flush = min(self._aggregate_deadlines.values(), default=float("inf"))
        for view, aggregate in entries:
            context = DiagnosticContext(doc_id=view.doc_id, operation_kind=view.scope)
            event = {"schema_version": 2, "event_id": str(uuid4()), "boot_id": self.boot_id,
                     "timestamp_utc": datetime.now(timezone.utc).isoformat(), "component": "backend",
                     "level": "INFO", "event_code": "success_aggregate", "origin": "server",
                     "diagnostic_session_id": view.session_id, **aggregate.event_fields()}
            try:
                self._write(encode_event(event), view.session_id, context, "success_aggregate")
            except Exception:
                self._count("dropped")

    def _checkpoint_counts(self):
        """Persist a bounded process counter snapshot from the writer path."""
        from .control import atomic_json
        with self._counts_lock:
            counters = {key: self._counts[key] for key in (
                "written", "dropped", "invalid", "expired_queue", "storage_errors",
                "drain_timeouts", "sampled_success", "aggregated_success",
                "sampled_out_traces", "policy_omitted")}
        value = {"schema_version": 1, "boot_id": self.boot_id,
                 "started_at_utc": self.started_at_utc,
                 "checkpoint_at_utc": datetime.now(timezone.utc).isoformat(),
                 "counters": counters}
        try:
            atomic_json(self.store, self.store.root / "control/counters.json", value)
        except Exception:
            self._count("storage_errors")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.store.open()
        self._checkpoint_counts()
        self._stop.clear()
        self._accepting = True
        self._thread = threading.Thread(target=self._run, name="diagnostic-recorder", daemon=True)
        self._thread.start()

    def emit_client_event(self, event, *, context, session_id):
        """Authorized, validated reports belong only to the active detail stream."""
        if not self._accepting or self.capture_selector(context, "browser_error") != session_id:
            return False
        try:
            raw = {**event, "schema_version": 1, "boot_id": self.boot_id,
                   "timestamp_utc": datetime.now(timezone.utc).isoformat(), "component": "browser",
                   "origin": "client_reported", "level": "ERROR", "diagnostic_session_id": session_id}
            encoded = encode_event(raw)
            with self._admission_lock:
                if session_id in self._closed_captures:
                    return False
                if not self._queue.offer((encoded, session_id, False, context, "browser_error"),
                                         important=True):
                    raise Full
            return True
        except Full:
            self._count("dropped")
        except Exception:
            self._count("invalid")
        return False

    def _run(self):
        last_sweep = last_checkpoint = time.monotonic()
        while not self._stop.is_set() or not self._queue.empty():
            if self.monotonic() >= self._aggregate_next_flush:
                self._flush_aggregates(due_only=True)
            try:
                item = self._queue.get(timeout=0.1)
            except Empty:
                self._flush_due(time.monotonic())
                self._flush_batches(due_only=True)
                if time.monotonic() - last_sweep >= self.store.limits.maintenance_seconds:
                    try:
                        self.store.sweep(datetime.now(timezone.utc))
                    except Exception:
                        self._count("storage_errors")
                    last_sweep = time.monotonic()
                if time.monotonic() - last_checkpoint >= 5:
                    self._checkpoint_counts()
                    last_checkpoint = time.monotonic()
                continue
            try:
                if isinstance(item, _DrainBarrier):
                    self._flush_aggregates()
                    for key, entry in list(self._dedup.items()):
                        if item.session_id is None or entry["stream"] == item.session_id:
                            self._dedup.pop(key)
                            self._flush_summary(entry)
                    self._flush_batches()
                    item.done.set()
                    continue
                encoded, session_id, baseline, context, event_code = item
                streams = ["baseline"] if baseline and self.baseline_enabled else []
                if session_id:
                    if self.capture_selector(context, event_code) == session_id:
                        streams.append(session_id)
                    else:
                        self._count("expired_queue")
                event = json.loads(encoded)
                repeated = False
                for stream in streams:
                    repeated = self._record_to_stream(event, encoded, stream, context, baseline) or repeated
                if repeated:
                    self._count("repeats")
            except Exception:
                self._count("dropped")
            finally:
                self._flush_batches(due_only=not self._queue.empty())
                self._queue.task_done()
                if time.monotonic() - last_checkpoint >= 5:
                    self._checkpoint_counts()
                    last_checkpoint = time.monotonic()
        self._flush_due(float("inf"))
        self._flush_aggregates()
        self._flush_batches()
        self._checkpoint_counts()

    def _write(self, encoded, stream, context, event_code):
        if stream != "baseline" and self.capture_selector(context, event_code) != stream:
            self._count("expired_queue")
            return
        try:
            event = self.store._validated_from_recorder(encoded, event_code,
                                                        stream if stream != "baseline" else None)
            if stream not in self._batches:
                self._batches[stream] = []
                self._batch_started[stream] = self.monotonic()
                self._batch_bytes[stream] = 0
            self._batches[stream].append(event)
            self._batch_bytes[stream] += len(encoded)
            if self._batch_bytes[stream] >= 65536:
                self._flush_batch(stream)
        except Exception:
            self._count("dropped")

    def _flush_batch(self, stream):
        events = self._batches.pop(stream, [])
        self._batch_started.pop(stream, None)
        self._batch_bytes.pop(stream, None)
        if not events:
            return
        try:
            result = self.store.append_batch(tuple(events), stream=stream)
            self._count("written", result.written_events)
            if stream != "baseline" and result.written_bytes:
                self.on_capture_written(stream, result.written_bytes)
            failed = len(events) - result.written_events
            if failed:
                self._count("dropped", failed)
                if stream != "baseline":
                    self.on_capture_failed(stream, result.failure_reason or "storage_error")
        except Exception:
            self._count("dropped", len(events))
            if stream != "baseline":
                self.on_capture_failed(stream, "storage_error")

    def _flush_batches(self, *, due_only=False):
        now = self.monotonic()
        for stream in tuple(self._batches):
            if not due_only or now - self._batch_started[stream] >= 0.05:
                self._flush_batch(stream)

    def _record_to_stream(self, event, encoded, stream, context, baseline):
        if not baseline or event["level"] not in {"WARN", "ERROR"}:
            self._write(encoded, stream, context, event["event_code"])
            return False
        frames = tuple((frame["module"], frame["function"], frame["line"]) for frame in event.get("frames", []))
        key = (event["event_code"], stream, context, event.get("exception_type"),
               event.get("error_code"), event.get("http_status"), event.get("route_template"), frames)
        now = time.monotonic()
        previous = self._dedup.get(key)
        if previous and now - previous["started"] < 1:
            previous["repeats"] += 1
            previous["last"] = event["timestamp_utc"]
            return True
        if previous:
            self._flush_summary(previous)
        self._dedup[key] = {"event": event, "stream": stream, "context": context,
                            "started": now, "last": event["timestamp_utc"], "repeats": 0}
        self._dedup.move_to_end(key)
        while len(self._dedup) > 1024:
            _, retired = self._dedup.popitem(last=False)
            self._flush_summary(retired)
        self._write(encoded, stream, context, event["event_code"])
        return False

    def _flush_summary(self, entry):
        if not entry["repeats"]:
            return
        event = entry["event"]
        summary = {**event, "event_id": str(uuid4()), "event_code": "repeat_summary",
                   "source_event_code": event["event_code"], "timestamp_utc": entry["last"],
                   "first_timestamp_utc": event["timestamp_utc"], "last_timestamp_utc": entry["last"],
                   "counts": {"repeats": entry["repeats"]}}
        try:
            self._write(encode_event(summary), entry["stream"], entry["context"], event["event_code"])
        except Exception:
            self._count("dropped")

    def _flush_due(self, now):
        for key, entry in list(self._dedup.items()):
            if now - entry["started"] >= 1:
                self._dedup.pop(key)
                self._flush_summary(entry)

    def drain_capture(self, session_id: str, timeout_seconds=2) -> bool:
        """Close new detail admission, then flush accepted records before a manual stop."""
        with self._admission_lock:
            self._closed_captures[str(session_id)] = None
            # Session IDs are one-use UUIDs; old closed IDs cannot be resumed.
            while len(self._closed_captures) > 1024:
                self._closed_captures.popitem(last=False)
            if self._success_session == str(session_id):
                self._success_session = None
                self._success_windows.clear()
        barrier = _DrainBarrier(str(session_id), threading.Event())
        deadline = time.monotonic() + timeout_seconds
        try:
            self._queue.put(barrier, timeout=max(0, deadline - time.monotonic()))
        except Full:
            self._count("drain_timeouts")
            return False
        if not barrier.done.wait(max(0, deadline - time.monotonic())):
            self._count("drain_timeouts")
            return False
        return True

    def flush_pending(self, timeout_seconds=2) -> bool:
        """Wait for all events admitted before this barrier without closing capture."""
        barrier = _DrainBarrier(None, threading.Event())
        with self._admission_lock:
            try:
                self._queue.put_nowait(barrier)
            except Full:
                self._count("drain_timeouts")
                return False
        if not barrier.done.wait(timeout_seconds):
            self._count("drain_timeouts")
            return False
        return True

    def stop(self, timeout_seconds=5):
        self._accepting = False
        self._stop.set()
        if self._thread:
            self._thread.join(timeout_seconds)
        if not self._thread or not self._thread.is_alive():
            self.store.close()
        else:
            self._count("shutdown_timeout")

    def status(self) -> dict:
        with self._counts_lock:
            counts = {key: self._counts[key] for key in (
                "written", "dropped", "invalid", "repeats", "expired_queue", "storage_errors", "shutdown_timeout",
                "drain_timeouts", "sampled_success", "aggregated_success",
                "sampled_out_traces", "sampled_out_slow", "policy_omitted", "aggregate_overflow",
            )}
        with self._admission_lock:
            counts["aggregate_overflow"] = self._aggregates.aggregate_overflow
        return {**self.store.status(), **counts, "queued": self._queue.qsize(),
                "running": bool(self._thread and self._thread.is_alive()),
                "boot_id": self.boot_id, "started_at_utc": self.started_at_utc}


def set_recorder(recorder: DiagnosticRecorder | None):
    global _runtime_recorder
    _runtime_recorder = recorder


def emit_event(event_code: str, *, context: DiagnosticContext | None = None,
               exception: BaseException | None = None, fields=None) -> bool:
    recorder = _runtime_recorder
    if recorder is None:
        return False
    if context is None:
        # Context module added by the HTTP integration; avoid import-time dependency.
        try:
            from .context import current_context
            context = current_context()
        except ImportError:
            context = DiagnosticContext()
    return recorder.emit(event_code, context=context, exception=exception, fields=fields)


def begin_trace(context: DiagnosticContext) -> bool:
    recorder = _runtime_recorder
    return bool(recorder and recorder.begin_trace(context))
