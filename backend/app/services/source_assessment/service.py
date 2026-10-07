"""Deterministic sampling and provider-independent policy. Never mutate retrieval."""

import threading
import time

from app.services.llm_scheduler import LLMCancelled
from app.services.llm_client import LLMBusyError
from .types import (
    AssessmentConfig,
    AssessmentItem,
    AssessmentOutcome,
    AssessmentRequest,
    AssessmentUnavailable,
    ProviderAssessment,
    SourceAssessor,
)


def build_assessment_sample(
    blocks: list[dict], config: AssessmentConfig
) -> tuple[AssessmentItem, ...]:
    selected = blocks[: config.sample_size]
    if not selected:
        return ()
    budget = min(config.max_chars_per_fragment, config.max_total_chars // len(selected))
    items = []
    for number, block in enumerate(selected, 1):
        content = block.get("content") or ""
        title = block.get("title") or ""
        text = content[:budget]
        if len(content) > budget:
            boundary = text.rfind("\n\n")
            if boundary >= budget // 2:
                text = text[:boundary]
        items.append(
            AssessmentItem(
                source_index=block.get("_source_index") or number,
                doc_id=block.get("doc_id", ""),
                title=title[:256],
                text=text,
                truncated=len(text) < len(content) or len(title) > 256,
            )
        )
    return tuple(items)


def decide_assessment(request: AssessmentRequest, result: ProviderAssessment) -> str:
    wanted = [i.source_index for i in request.items]
    indexes = [i.source_index for i in result.items]
    if (
        not wanted
        or any(type(i) is not int for i in indexes)
        or len(indexes) != len(set(indexes))
        or set(indexes) != set(wanted)
        or any(
            i.label not in {"relevant", "partial", "irrelevant", "uncertain"} for i in result.items
        )
    ):
        raise AssessmentUnavailable("invalid_output")
    labels = [i.label for i in result.items]
    if "relevant" in labels:
        return "allow"
    if all(label == "irrelevant" for label in labels) and not any(
        i.truncated for i in request.items
    ):
        return "reject"
    return "uncertain"


def assess_sources(
    query: str,
    locale: str,
    blocks: list[dict],
    *,
    enabled: bool,
    config: AssessmentConfig,
    assessor: SourceAssessor | None,
    deadline: float,
    cancel: threading.Event | None,
) -> AssessmentOutcome:
    start = time.monotonic()
    sample = build_assessment_sample(blocks, config) if enabled else ()
    common = dict(
        requested_count=config.sample_size,
        sampled_count=len(sample),
        source_indexes=tuple(i.source_index for i in sample),
        truncated_indexes=tuple(i.source_index for i in sample if i.truncated),
        backend=config.backend,
        model_id=config.model_id,
        policy_version=config.policy_version,
    )

    def outcome(status, **fields):
        metrics = getattr(assessor, "metrics", {}) or {}
        for key in ("completion_count", "input_tokens", "output_tokens", "queue_ms"):
            if key not in fields and key in metrics:
                fields[key] = metrics[key]
        return AssessmentOutcome(
            status=status,
            duration_ms=round((time.monotonic() - start) * 1000, 2),
            **common,
            **fields,
        )

    if cancel is not None and cancel.is_set():
        raise LLMCancelled()
    if not enabled:
        return outcome("disabled", reason_code="user_disabled", completion_count=0)
    if not sample:
        return outcome("skipped", reason_code="no_sources", completion_count=0)
    try:
        if time.monotonic() >= deadline:
            raise TimeoutError()
        if assessor is None:
            raise AssessmentUnavailable("transport")
        request = AssessmentRequest(
            query=query, locale=locale, items=sample, policy_version=config.policy_version
        )
        result = assessor.assess(request, deadline=deadline, cancel=cancel)
        if cancel is not None and cancel.is_set():
            raise LLMCancelled()
        if time.monotonic() >= deadline:
            raise TimeoutError()
        decision = decide_assessment(request, result)
        by_id = {i.source_index: i for i in result.items}
        return outcome(
            "completed",
            decision=decision,
            assessed_count=len(result.items),
            items=tuple(by_id[i.source_index] for i in sample),
            completion_count=result.completion_count,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            queue_ms=result.queue_ms,
        )
    except LLMCancelled:
        raise
    except AssessmentUnavailable as exc:
        return outcome("unavailable", reason_code=exc.reason_code)
    except LLMBusyError:
        return outcome("unavailable", reason_code="busy")
    except TimeoutError:
        return outcome("unavailable", reason_code="timeout")
    except (ValueError, TypeError, AttributeError):
        return outcome("unavailable", reason_code="invalid_output")
    except Exception:
        return outcome("unavailable", reason_code="transport")
