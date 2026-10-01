"""Explicit operation and stage events; never derive fields from content."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import time
from functools import wraps

from app.services.errors import public_error_code
from .recorder import emit_event
from .context import current_context
from .sanitize import _ERROR_CODES


@dataclass
class OperationOutcome:
    failed: bool = False
    counts: dict = field(default_factory=dict)


_outcome = ContextVar("diagnostic_operation_outcome", default=None)


def diagnostic_error_code(exception):
    """Preserve only registered public codes; never persist arbitrary exception text."""
    code = getattr(exception, "code", None)
    return code if type(code) is str and code in _ERROR_CODES else public_error_code(exception)


def record_failure(exception, *, stage=None, **fields):
    outcome = _outcome.get()
    if outcome is not None:
        outcome.failed = True
    if stage:
        fields["stage"] = stage
    fields["error_code"] = diagnostic_error_code(exception)
    emit_event("operation_failed", exception=exception, fields=fields)


@contextmanager
def operation_span(*, stage=None):
    outcome = OperationOutcome()
    token = _outcome.set(outcome)
    started = time.monotonic()
    emit_event("operation_started", fields={"stage": stage} if stage else {})
    if stage:
        start_stage(stage)
    try:
        yield outcome
    except Exception as exc:
        record_failure(exc, stage=stage)
        raise
    else:
        if not outcome.failed:
            if stage:
                finish_stage(stage, started, counts=outcome.counts)
            fields = {"duration_ms": elapsed_ms(started), "counts": outcome.counts}
            if stage:
                fields["stage"] = stage
            emit_event("operation_finished", fields=fields)
    finally:
        _outcome.reset(token)


def elapsed_ms(started):
    return max(0, (time.monotonic() - started) * 1000)


def start_stage(stage, **fields):
    emit_event("stage_started", fields={"stage": stage, **fields})
    return time.monotonic()


def finish_stage(stage, started, **fields):
    emit_event("stage_finished", fields={"stage": stage, "duration_ms": elapsed_ms(started), **fields})


def observed_operation(stage):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with operation_span(stage=stage):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def dependency_call(dependency):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            started = time.monotonic()
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                fields = {"dependency": dependency, "duration_ms": elapsed_ms(started),
                          "error_code": public_error_code(exc)}
                for candidate in (exc, exc.__cause__, getattr(exc, "cause", None)):
                    status = getattr(candidate, "status_code", None)
                    if type(status) is int and 100 <= status <= 599:
                        fields["http_status"] = status
                        break
                emit_event("dependency_call_finished", exception=exc, fields=fields)
                raise
            emit_event("dependency_call_finished", fields={"dependency": dependency, "duration_ms": elapsed_ms(started)})
            return result
        return wrapped
    return decorate


def observed_llm_parse(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            chunk = kwargs.get("chunk_idx", args[3] if len(args) > 3 else 0)
            fields = {"stage": "chat" if current_context().operation_kind == "search_chat" else "generate",
                      "error_code": public_error_code(exc)}
            if type(chunk) is int and 0 <= chunk <= 10**12:
                fields["chunk_index"] = max(0, chunk - 1)
            emit_event("llm_parse_failed", exception=exc, fields=fields)
            raise
    return wrapped
