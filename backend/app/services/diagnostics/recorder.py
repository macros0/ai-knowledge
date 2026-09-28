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

from .sanitize import encode_event, safe_exception
from .schema import DiagnosticContext
from .store import DiagnosticStore

_runtime_recorder = None
SUCCESS_REQUESTS_PER_SECOND_PER_ROUTE = 20


@dataclass
class _DrainBarrier:
    session_id: str | None
    done: threading.Event


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
    def __init__(self, store: DiagnosticStore, *, capture_selector=None, boot_id=None,
                 baseline_enabled=True, on_capture_written=None, on_capture_failed=None):
        self.store = store
        self.boot_id = str(boot_id or uuid4())
        self.capture_selector = capture_selector or (lambda *_: None)
        self.baseline_enabled = baseline_enabled
        self.on_capture_written = on_capture_written or (lambda *_: None)
        self.on_capture_failed = on_capture_failed or (lambda *_: None)
        self.handler = DiagnosticLogHandler(self)
        self._queue = Queue(maxsize=store.limits.queue_size)
        self._stop = threading.Event()
        self._thread = None
        self._counts = Counter()
        self._counts_lock = threading.Lock()
        self._dedup = OrderedDict()
        self._accepting = True
        self._admission_lock = threading.Lock()
        self._closed_captures = set()
        self.monotonic = time.monotonic
        self._success_session = None
        self._success_windows = {}

    def _count(self, key, number=1):
        with self._counts_lock:
            self._counts[key] += number

    def emit(self, event_code: str, *, context: DiagnosticContext | None = None,
             exception: BaseException | None = None, fields=None) -> bool:
        if not self._accepting:
            return False
        try:
            if context is None:
                from .context import current_context
                context = current_context()
            session_id = self.capture_selector(context, event_code)
            baseline = event_code in {
                "log_warning", "log_error", "operation_failed", "llm_parse_failed",
                "server_started", "server_stopped", "server_start_failed", "dependency_status_changed",
                "proxy_failed", "render_failed", "diagnostic_gap",
            }
            if event_code == "request_finished" and (fields or {}).get("http_status", 0) >= 400:
                baseline = True
            level = "ERROR" if exception or event_code.endswith(("failed", "error")) else "WARN" if event_code == "log_warning" else "INFO"
            if event_code == "request_finished":
                status = (fields or {}).get("http_status", 0)
                level = "ERROR" if status >= 500 else "WARN" if status >= 400 else "INFO"
            baseline = baseline or level in {"WARN", "ERROR"}
            if not session_id and not (baseline and self.baseline_enabled):
                return False
            event = {
                "schema_version": 1, "event_id": str(uuid4()), "boot_id": self.boot_id,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "component": "backend", "level": level, "event_code": event_code, "origin": "server",
                **{key: value for key, value in asdict(context).items()
                   if value is not None and key != "operation_kind"},
                **(fields or {}),
            }
            if exception:
                event.update(safe_exception(exception))
            if session_id:
                event["diagnostic_session_id"] = session_id
            encoded = encode_event(event)  # Before queueing, not only before export.
            with self._admission_lock:
                if session_id in self._closed_captures:
                    if not baseline or not self.baseline_enabled:
                        return False
                    event.pop("diagnostic_session_id", None)
                    encoded = encode_event(event)
                    session_id = None
                if (session_id and event_code == "request_finished"
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
                self._queue.put_nowait((encoded, session_id, baseline, context, event_code))
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

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.store.open()
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
                self._queue.put_nowait((encoded, session_id, False, context, "browser_error"))
            return True
        except Full:
            self._count("dropped")
        except Exception:
            self._count("invalid")
        return False

    def _run(self):
        last_sweep = time.monotonic()
        while not self._stop.is_set() or not self._queue.empty():
            try:
                item = self._queue.get(timeout=0.1)
            except Empty:
                self._flush_due(time.monotonic())
                if time.monotonic() - last_sweep >= self.store.limits.maintenance_seconds:
                    try:
                        self.store.sweep(datetime.now(timezone.utc))
                    except Exception:
                        self._count("storage_errors")
                    last_sweep = time.monotonic()
                continue
            try:
                if isinstance(item, _DrainBarrier):
                    for key, entry in list(self._dedup.items()):
                        if item.session_id is None or entry["stream"] == item.session_id:
                            self._dedup.pop(key)
                            self._flush_summary(entry)
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
                self._queue.task_done()
        self._flush_due(float("inf"))

    def _write(self, encoded, stream, context, event_code):
        if stream != "baseline" and self.capture_selector(context, event_code) != stream:
            self._count("expired_queue")
            return
        try:
            written = self.store.append(encoded, stream=stream)
            reason = (self.store.status().get("last_failure") or "storage_error") if not written else None
        except Exception:
            written, reason = False, "storage_error"
        if written:
            self._count("written")
            if stream != "baseline":
                self.on_capture_written(stream, len(encoded))
        else:
            self._count("dropped")
            if stream != "baseline":
                self.on_capture_failed(stream, reason)

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
            self._closed_captures.add(str(session_id))
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
                "drain_timeouts", "sampled_success",
            )}
        return {**self.store.status(), **counts, "queued": self._queue.qsize(),
                "running": bool(self._thread and self._thread.is_alive()), "boot_id": self.boot_id}


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
