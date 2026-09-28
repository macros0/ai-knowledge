"""Immutable correlation passed explicitly across worker boundaries."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, replace
from functools import wraps
import re
from uuid import UUID, uuid4

from .schema import DiagnosticContext

_current = ContextVar("diagnostic_context", default=DiagnosticContext())
_recorded_exceptions = ContextVar("diagnostic_recorded_exceptions", default=())


def canonical_request_id(value):
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return str(parsed) if str(parsed) == value.lower() else None


def current_context() -> DiagnosticContext:
    return _current.get()


def set_generation_id(generation_id: str):
    """Replace the immutable value within a caller-owned bind_context scope."""
    _current.set(replace(current_context(), generation_id=generation_id))


@contextmanager
def bind_context(context: DiagnosticContext):
    parent = current_context()
    boundary = parent.request_id != context.request_id or (
        context.request_id is None and parent.operation_id != context.operation_id
    )
    error_token = _recorded_exceptions.set(()) if boundary else None
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)
        if error_token is not None:
            _recorded_exceptions.reset(error_token)


def mark_recorded_exception(exception, event_code):
    context = current_context()
    if context.request_id or context.operation_id:
        _recorded_exceptions.set((*_recorded_exceptions.get(), (id(exception), event_code))[-32:])


def exception_recorded(exception, event_code=None):
    return any(identity == id(exception) and (event_code is None or code == event_code)
               for identity, code in _recorded_exceptions.get())


def new_operation(parent: DiagnosticContext, *, doc_id=None, generation_id=None, operation_kind=None) -> DiagnosticContext:
    return DiagnosticContext(request_id=parent.request_id, operation_id=str(uuid4()),
                             doc_id=doc_id, generation_id=generation_id,
                             operation_kind=operation_kind or ("document" if doc_id else parent.operation_kind or "system"))


def run_bound(context: DiagnosticContext, function, *args, **kwargs):
    with bind_context(context):
        return function(*args, **kwargs)


def context_metadata(context: DiagnosticContext) -> dict:
    return {key: value for key, value in asdict(context).items() if value is not None}


def context_from_metadata(value) -> DiagnosticContext:
    if not isinstance(value, dict):
        value = {}
    fields = {}
    for key in ("request_id", "operation_id"):
        fields[key] = canonical_request_id(value.get(key))
    for key in ("doc_id", "generation_id"):
        candidate = value.get(key)
        fields[key] = candidate if isinstance(candidate, str) and (
            canonical_request_id(candidate) or re.fullmatch(r"[0-9a-f]{16}|[0-9a-f]{32}", candidate)
        ) else None
    kind = value.get("operation_kind")
    fields["operation_kind"] = kind if isinstance(kind, str) and kind in {
        "system", "document", "search_chat", "interface",
    } else None
    return DiagnosticContext(**fields)


def operation_context(kind):
    """Scope a synchronous operation without changing its public signature."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            parent = current_context()
            context = parent if parent.operation_id and parent.operation_kind == kind else new_operation(
                parent, operation_kind=kind,
            )
            with bind_context(context):
                return function(*args, **kwargs)
        return wrapped
    return decorate
