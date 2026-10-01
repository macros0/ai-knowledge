"""Safe bounded duration aggregates for intentionally omitted successes."""
from dataclasses import dataclass
from datetime import datetime, timezone

from .schema import AGGREGATE_BUCKET_KEYS, DEPENDENCIES, ROUTE_TEMPLATES, STAGES
from .sanitize import valid_uuid

_BOUNDARIES_US = (10000, 50000, 250000, 1000000, 5000000, 30000000)
_MAX_VALUE = 10**12


@dataclass(frozen=True)
class AggregateKey:
    session_id: str
    component: str
    route_template: str | None
    stage: str | None
    dependency: str | None
    outcome: str

    def __post_init__(self):
        if (valid_uuid(self.session_id) is None or self.component not in {"backend", "frontend"}
                or self.route_template not in ROUTE_TEMPLATES | {None}
                or self.stage not in STAGES | {None}
                or self.dependency not in DEPENDENCIES | {None}
                or self.outcome != "success"):
            raise ValueError("Invalid aggregate key")


@dataclass(frozen=True)
class SafeAggregate:
    key: AggregateKey
    window_start_utc: str
    window_end_utc: str
    counts: dict[str, int]
    overflow: bool = False

    def event_fields(self):
        result = {"outcome": self.key.outcome, "window_start_utc": self.window_start_utc,
                  "window_end_utc": self.window_end_utc, "counts": dict(self.counts)}
        for name in ("route_template", "stage", "dependency"):
            value = getattr(self.key, name)
            if value is not None:
                result[name] = value
        return result


class AggregateBuffer:
    def __init__(self, *, max_keys=256):
        if type(max_keys) is not int or max_keys < 1 or max_keys > 256:
            raise ValueError("Invalid aggregate key limit")
        self.max_keys = max_keys
        self._entries = {}
        self._overflow = {}
        self.aggregate_overflow = 0

    @staticmethod
    def _new(key, *, overflow=False):
        now = datetime.now(timezone.utc).isoformat()
        counts = {name: 0 for name in ("count", "duration_sum_us", "duration_max_us", *AGGREGATE_BUCKET_KEYS)}
        return {"key": key, "start": now, "end": now, "counts": counts, "overflow": overflow}

    @staticmethod
    def _result(entry):
        return SafeAggregate(entry["key"], entry["start"], entry["end"],
                             dict(entry["counts"]), entry["overflow"])

    def observe(self, key: AggregateKey, duration_us: int, outcome: str) -> None:
        if outcome != "success" or key.outcome != outcome or type(duration_us) is not int or not 0 <= duration_us <= _MAX_VALUE:
            raise ValueError("Invalid aggregate observation")
        entry = self._entries.get(key)
        if entry is None:
            if len(self._entries) < self.max_keys:
                entry = self._new(key)
                self._entries[key] = entry
            else:
                self.aggregate_overflow += 1
                identity = (key.session_id, key.component)
                if identity not in self._overflow:
                    overflow_key = AggregateKey(key.session_id, key.component, "/unknown", None, None, "success")
                    self._overflow[identity] = self._new(overflow_key, overflow=True)
                entry = self._overflow[identity]
        counts = entry["counts"]
        if counts["count"] >= _MAX_VALUE or counts["duration_sum_us"] > _MAX_VALUE - duration_us:
            raise OverflowError("Aggregate must be flushed before numeric limit")
        counts["count"] += 1
        counts["duration_sum_us"] += duration_us
        counts["duration_max_us"] = max(counts["duration_max_us"], duration_us)
        bucket = next((i for i, edge in enumerate(_BOUNDARIES_US) if duration_us <= edge), 6)
        counts[AGGREGATE_BUCKET_KEYS[bucket]] += 1
        entry["end"] = datetime.now(timezone.utc).isoformat()

    def flush(self, session_id: str, cutoff_mono: float) -> tuple[SafeAggregate, ...]:
        del cutoff_mono
        selected = [self._result(entry) for key, entry in self._entries.items() if key.session_id == session_id]
        self._entries = {key: value for key, value in self._entries.items() if key.session_id != session_id}
        for identity, entry in tuple(self._overflow.items()):
            if identity[0] == session_id:
                selected.append(self._result(entry))
                del self._overflow[identity]
        return tuple(selected)
