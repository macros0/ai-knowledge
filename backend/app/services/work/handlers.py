"""Explicit internal kind/version registry; no payload-driven imports or calls."""
from .models import WorkSpec

TRANSIENT_CODES = frozenset({"storage_unavailable", "model_unavailable", "transport_unavailable"})


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
