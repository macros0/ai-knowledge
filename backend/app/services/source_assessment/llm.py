"""One LLM call. Strict output, hidden stream, isolated budget and quoted positives."""

import json
import time
from pathlib import Path

from pydantic import ValidationError
from app import error_codes
from app.services.llm_client import LLMClient, LLMTruncationError
from app.services.llm_profiles import request_scope, SourceAssessmentJSON
from app.services.chat_token_budget import ChatTokenBudget, ChatBudgetUnavailable
from app.services.llm_scheduler import LLMCancelled
from .types import AssessmentUnavailable, ItemDecision, ProviderAssessment

PROMPT = Path(__file__).resolve().parents[3] / "prompts/source_assessment.md"


def normalize_quote(value):
    return " ".join(value.split())


class LLMSourceAssessor:
    def __init__(self, config, connection):
        self.config = config
        self.client = LLMClient(interactive=True, settings=connection.settings)
        self.metrics = {
            "completion_count": 0,
            "queue_ms": None,
            "input_tokens": None,
            "output_tokens": None,
        }

    def assess(self, request, *, deadline, cancel):
        system = PROMPT.read_text(encoding="utf-8")
        user = json.dumps(
            {
                "question": request.query,
                "locale": request.locale,
                "items": [
                    {
                        "source_index": i.source_index,
                        "title": i.title,
                        "text": i.text,
                        "truncated": i.truncated,
                    }
                    for i in request.items
                ],
            },
            ensure_ascii=False,
        )
        if cancel is not None and cancel.is_set():
            raise LLMCancelled()
        with request_scope(
            on_text=None,
            single_pass=True,
            deadline=deadline,
            cancel=cancel,
            assessment_metrics=self.metrics,
        ):
            if self.client.local:
                counter = ChatTokenBudget(
                    self.client.settings,
                    self.client.model,
                    deadline=deadline,
                    cancel=cancel,
                    max_input_tokens=self.config.max_input_tokens,
                )
                try:
                    fits = counter.fits(system, user, output_tokens=self.config.max_output_tokens)
                except ChatBudgetUnavailable as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError() from exc
                    reason = (
                        "transport" if exc.code == error_codes.DEPENDENCY_UNAVAILABLE else "input_budget"
                    )
                    raise AssessmentUnavailable(reason) from exc
            else:
                # Unknown tokenizer: UTF-8 bytes + template reserve is deliberately
                # conservative. Never present this estimate as billable usage.
                import litellm

                info = litellm.model_cost.get(self.client.model)
                if info:
                    count = litellm.token_counter(
                        model=self.client.model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                    )
                    window = info.get("max_input_tokens") or info.get("max_tokens")
                else:
                    count = len((system + user).encode("utf-8")) + 512
                    window = None
                fits = count <= self.config.max_input_tokens and (
                    not window or count + self.config.max_output_tokens + 256 <= window
                )
            if not fits:
                raise AssessmentUnavailable("input_budget")
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError()
            try:
                raw = self.client.assess_json_once(
                    system, user, max_tokens=self.config.max_output_tokens, timeout_seconds=left
                )
                parsed = SourceAssessmentJSON.model_validate(raw, strict=True)
            except (ValidationError, ValueError, LLMTruncationError) as exc:
                raise AssessmentUnavailable("invalid_output") from exc
        texts = {i.source_index: i.text for i in request.items}
        decisions = []
        for item in parsed.items:
            if item.source_index not in texts:
                raise AssessmentUnavailable("invalid_output")
            label, quote = item.label, item.evidence_quote
            if label in {"relevant", "partial"} and (
                not quote
                or not normalize_quote(quote)
                or len(quote) > 240
                or normalize_quote(quote) not in normalize_quote(texts[item.source_index])
            ):
                label = "uncertain"
            decisions.append(ItemDecision(item.source_index, label, quote))
        return ProviderAssessment(tuple(decisions), **self.metrics)
