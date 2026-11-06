"""Explicit internal kind/version registry; no payload-driven imports or calls."""
from .models import WorkSpec
from dataclasses import dataclass

TRANSIENT_CODES = frozenset({"storage_unavailable", "model_unavailable", "transport_unavailable"})


@dataclass(frozen=True)
class WorkDeferred:
    """A coordinator yields its slot while durable child work makes progress."""
    delay_seconds: int

    def __post_init__(self):
        if type(self.delay_seconds) is not int or not 1 <= self.delay_seconds <= 3600:
            raise ValueError("Invalid bounded work deferral")


class TransientWorkError(RuntimeError):
    def __init__(self, code):
        if code not in TRANSIENT_CODES:
            raise ValueError("Unknown transient work failure")
        self.code = code
        super().__init__(code)


class HandlerRegistry:
    def __init__(self):
        self._handlers = {}

    def register(self, kind, version, handler):
        WorkSpec(kind, version, {}, "validate")
        if not callable(handler) or (kind, version) in self._handlers:
            raise ValueError("Invalid or duplicate work handler")
        self._handlers[kind, version] = handler

    @property
    def supported(self):
        result = {}
        for kind, version in self._handlers:
            result.setdefault(kind, set()).add(version)
        return result

    def resolve(self, spec):
        return self._handlers[spec.kind, spec.payload_version]
