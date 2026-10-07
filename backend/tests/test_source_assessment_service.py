"""Policy tests use typed adapters, without JSON, quotes or network calls."""

from copy import deepcopy
import threading
import time

import pytest

from app.services.source_assessment.types import AssessmentConfig, ItemDecision, ProviderAssessment


def blocks(count=6):
    return [
        {"doc_id": "same", "title": str(i), "content": f"Fragment {i}", "score": 1.0}
        for i in range(1, count + 1)
    ]


class TypedAssessor:
    def __init__(self, labels):
        self.labels = labels
        self.requests = []

    def assess(self, request, *, deadline, cancel):
        self.requests.append(request)
        return ProviderAssessment(
            tuple(
                ItemDecision(i.source_index, label) for i, label in zip(request.items, self.labels)
            )
        )


def run(source_blocks, labels, enabled=True, cancel=None, config=None):
    from app.services.source_assessment.service import assess_sources

    adapter = TypedAssessor(labels)
    outcome = assess_sources(
        "Question",
        "ru",
        source_blocks,
        enabled=enabled,
        config=config or AssessmentConfig(),
        assessor=adapter,
        deadline=time.monotonic() + 5,
        cancel=cancel,
    )
    return outcome, adapter


def test_first_n_keeps_duplicates_rank_and_n_plus_one():
    from app.services.source_assessment.service import build_assessment_sample

    original = blocks()
    before = deepcopy(original)
    sample = build_assessment_sample(original, AssessmentConfig())
    assert [i.source_index for i in sample] == [1, 2, 3, 4, 5]
    assert [i.doc_id for i in sample] == ["same"] * 5
    assert original == before and len(original) == 6


@pytest.mark.parametrize(
    ("labels", "decision"),
    [
        (["relevant", "irrelevant"], "allow"),
        (["irrelevant", "irrelevant"], "reject"),
        (["partial", "irrelevant"], "uncertain"),
        (["uncertain", "irrelevant"], "uncertain"),
    ],
)
def test_typed_policy_without_quotes(labels, decision):
    outcome, adapter = run(blocks(2), labels)
    assert outcome.status == "completed" and outcome.decision == decision
    assert len(adapter.requests) == 1
    assert outcome.sampled_count == 2 and outcome.requested_count == 5


@pytest.mark.parametrize(
    ("source_blocks", "enabled", "status"), [(blocks(), False, "disabled"), ([], True, "skipped")]
)
def test_off_and_empty_make_zero_calls(source_blocks, enabled, status):
    outcome, adapter = run(source_blocks, [], enabled)
    assert outcome.status == status and not adapter.requests


def test_truncated_negative_never_rejects_and_preserves_original():
    original = blocks(1)
    original[0]["content"] = "a" * 3000
    outcome, _ = run(original, ["irrelevant"])
    assert outcome.decision == "uncertain" and outcome.truncated_indexes == (1,)
    assert len(original[0]["content"]) == 3000


def test_title_truncation_and_paragraph_boundary():
    from app.services.source_assessment.service import build_assessment_sample

    original = blocks(1)
    original[0].update(title="T" * 300, content="a" * 1300 + "\n\n" + "b" * 1500)
    sample = build_assessment_sample(original, AssessmentConfig())
    assert len(sample[0].title) == 256
    assert sample[0].text == "a" * 1300
    assert sample[0].truncated


@pytest.mark.parametrize("indexes", [[1], [1, 1], [1, 3], [True, 2]])
def test_invalid_ids_unavailable(indexes):
    from app.services.source_assessment.service import assess_sources

    class Invalid:
        def assess(self, *args, **kwargs):
            return ProviderAssessment(tuple(ItemDecision(i, "irrelevant") for i in indexes))

    outcome = assess_sources(
        "q",
        "ru",
        blocks(2),
        enabled=True,
        config=AssessmentConfig(),
        assessor=Invalid(),
        deadline=time.monotonic() + 3,
        cancel=None,
    )
    assert outcome.status == "unavailable" and outcome.reason_code == "invalid_output"


def test_useful_n_plus_one_is_not_inspected_or_deleted():
    source_blocks = blocks()
    source_blocks[5]["content"] = "Answer to the question"
    outcome, adapter = run(source_blocks, ["irrelevant"] * 5)
    assert outcome.decision == "reject"
    assert len(adapter.requests[0].items) == 5
    assert source_blocks[5]["content"] == "Answer to the question"


def test_cancel_propagates_and_no_adapter_call():
    from app.services.llm_scheduler import LLMCancelled

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(LLMCancelled):
        run(blocks(), ["relevant"] * 5, cancel=cancel)


def test_adapter_error_has_no_fallback_or_raw_error():
    from app.services.source_assessment.service import assess_sources

    class Broken:
        def assess(self, *args, **kwargs):
            raise ConnectionError("private provider text")

    result = assess_sources(
        "q",
        "ru",
        blocks(),
        enabled=True,
        config=AssessmentConfig(),
        assessor=Broken(),
        deadline=time.monotonic() + 3,
        cancel=None,
    )
    assert result.status == "unavailable" and result.reason_code == "transport"
    assert "private" not in str(result.public())
