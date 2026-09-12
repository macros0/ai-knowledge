from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import validate_stage8_labels


def _case(**overrides: object) -> dict:
    value = {
        "id": "pos-it",
        "category": "positive",
        "query": "IT 0003",
        "expected_canonicals": ["IT0003"],
        "expected_documents": ["doc-1"],
        "relevance": {"doc-1|main|0": "relevant"},
        "mandatory_sources": [{"doc_id": "doc-1", "slug": "main", "chunk_index": 0}],
    }
    value.update(overrides)
    return value


def test_validate_cases_accepts_complete_subject_matter_labels():
    result = validate_stage8_labels.validate_cases({"version": 1, "cases": [_case()]})

    assert result["status"] == "READY"
    assert result["errors"] == []
    assert result["summary"] == {
        "total": 1,
        "quality_applicable": 1,
        "complete": 1,
        "incomplete": 0,
    }


def test_validate_cases_blocks_positive_case_without_labels():
    result = validate_stage8_labels.validate_cases(
        {"version": 1, "cases": [_case(relevance={}, expected_documents=[])]}
    )

    assert result["status"] == "BLOCKED"
    assert result["summary"]["incomplete"] == 1
    assert {error["code"] for error in result["errors"]} == {
        "relevance_missing",
        "expected_documents_missing",
    }


def test_validate_cases_rejects_invalid_source_keys_and_label_values():
    result = validate_stage8_labels.validate_cases(
        {
            "version": 1,
            "cases": [
                _case(
                    relevance={"doc-1|main": "relevant", "doc-1|main|x": "maybe"},
                    mandatory_sources=[{"doc_id": "doc-1", "slug": "main"}],
                )
            ],
        }
    )

    assert result["status"] == "BLOCKED"
    assert {error["code"] for error in result["errors"]} == {
        "invalid_relevance_key",
        "invalid_relevance_value",
        "invalid_mandatory_source",
    }


def test_validate_cases_can_require_explicit_approval_metadata():
    payload = {"version": 1, "cases": [_case()]}

    result = validate_stage8_labels.validate_cases(payload, require_approved=True)

    assert result["status"] == "BLOCKED"
    assert result["errors"] == [{"code": "labels_not_approved", "message": "labels.approved must be true"}]


def test_validate_cases_accepts_chunk_source_with_null_slug():
    result = validate_stage8_labels.validate_cases(
        {
            "version": 1,
            "cases": [
                _case(
                    relevance={"doc-1||0": "relevant"},
                    mandatory_sources=[
                        {"doc_id": "doc-1", "slug": None, "chunk_index": 0}
                    ],
                )
            ],
        }
    )

    assert result["status"] == "READY"
    assert result["errors"] == []


def test_validate_cases_rejects_conflicting_aliases_for_same_source():
    result = validate_stage8_labels.validate_cases(
        {
            "version": 1,
            "cases": [
                _case(
                    relevance={
                        "doc-1||0": "relevant",
                        "doc-1|None|0": "irrelevant",
                    },
                    mandatory_sources=[
                        {"doc_id": "doc-1", "slug": None, "chunk_index": 0}
                    ],
                )
            ],
        }
    )

    assert result["status"] == "BLOCKED"
    assert {error["code"] for error in result["errors"]} == {
        "conflicting_relevance_labels"
    }
