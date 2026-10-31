"""Versioned internal work DTOs; payloads are JSON data, never executable code."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import json
import re


class LostOwnership(RuntimeError):
    """The worker must stop publishing or acknowledging this attempt."""


@dataclass(frozen=True)
class WorkSpec:
    kind: str
    payload_version: int
    payload: dict
    dedupe_key: str
    doc_id: str | None = None

    def __post_init__(self):
        if (not isinstance(self.kind, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.kind)
                or type(self.payload_version) is not int or not 1 <= self.payload_version <= 2147483647
                or not isinstance(self.dedupe_key, str) or not 1 <= len(self.dedupe_key) <= 255
                or self.doc_id is not None and (not isinstance(self.doc_id, str) or not 1 <= len(self.doc_id) <= 64)
                or type(self.payload) is not dict):
            raise ValueError("Invalid work specification")
        body = json.dumps(self.payload, sort_keys=True, allow_nan=False, separators=(",", ":"))
        if len(body.encode("utf8")) > 65536:
            raise ValueError("Work payload exceeds limit")
        decoded = json.loads(body)
        if decoded != self.payload:
            raise ValueError("Work payload must be canonical JSON data")
        object.__setattr__(self, "payload", deepcopy(decoded))


@dataclass(frozen=True)
class WorkClaim:
    work_id: str
    owner_id: str
    fence: int
    lease_until: datetime
    spec: WorkSpec
    maintenance_epoch: int = 0
