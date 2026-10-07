"""Immutable adapter contracts. Quotes and connection never cross the public boundary."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Literal, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings

Label = Literal["relevant", "partial", "irrelevant", "uncertain"]


@dataclass(frozen=True)
class AssessmentConnection:
    settings: Settings = field(repr=False)


@dataclass(frozen=True)
class AssessmentConfig:
    backend: str = "llm"
    model_id: str = "configured-model"
    policy_version: str = "1"
    sample_size: int = 5
    timeout_seconds: float = 8
    max_chars_per_fragment: int = 2400
    max_total_chars: int = 12000
    max_input_tokens: int = 8192
    max_output_tokens: int = 1024
    config_fingerprint: str = ""


@dataclass(frozen=True)
class AssessmentItem:
    source_index: int
    doc_id: str
    title: str
    text: str
    truncated: bool = False


@dataclass(frozen=True)
class AssessmentRequest:
    query: str
    locale: str
    items: tuple[AssessmentItem, ...]
    policy_version: str = "1"


@dataclass(frozen=True)
class ItemDecision:
    source_index: int
    label: Label
    evidence_quote: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ProviderAssessment:
    items: tuple[ItemDecision, ...]
    completion_count: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    queue_ms: float | None = None


@dataclass(frozen=True)
class AssessmentOutcome:
    status: str
    decision: str | None = None
    reason_code: str | None = None
    requested_count: int = 0
    sampled_count: int = 0
    assessed_count: int = 0
    source_indexes: tuple[int, ...] = ()
    items: tuple[ItemDecision, ...] = ()
    truncated_indexes: tuple[int, ...] = ()
    duration_ms: float = 0
    backend: str = "llm"
    model_id: str = "configured-model"
    policy_version: str = "1"
    completion_count: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    queue_ms: float | None = None

    def public(self):
        from app.models.schemas import SourceAssessmentOut

        return SourceAssessmentOut(
            status=self.status,
            decision=self.decision,
            reason_code=self.reason_code,
            requested_count=self.requested_count,
            sampled_count=self.sampled_count,
            assessed_count=self.assessed_count,
            source_indexes=list(self.source_indexes),
            items=[{"source_index": i.source_index, "label": i.label} for i in self.items],
            truncated_indexes=list(self.truncated_indexes),
            duration_ms=self.duration_ms,
        )


class SourceAssessor(Protocol):
    def assess(
        self, request: AssessmentRequest, *, deadline: float, cancel: threading.Event | None
    ) -> ProviderAssessment: ...


class AssessmentUnavailable(Exception):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code
