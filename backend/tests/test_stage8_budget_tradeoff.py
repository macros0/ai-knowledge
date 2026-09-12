from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from probe_stage8_budget_tradeoff import _source_diff


def _case(case_id: str, raw: list[tuple], final: list[tuple]) -> dict:
    return {
        "id": case_id,
        "raw_candidates": [
            {"source": {"doc_id": doc, "slug": slug, "chunk_index": chunk}}
            for doc, slug, chunk in raw
        ],
        "final_blocks": [
            {
                "source": {"doc_id": doc, "slug": slug, "chunk_index": chunk}
            }
            for doc, slug, chunk in final
        ],
    }


def test_source_diff_reports_raw_and_final_changes_against_default():
    baseline = [
        _case(
            "one",
            [("doc", "kept", 0), ("doc", "lost", 0)],
            [("doc", "kept", 0), ("doc", "lost", 0)],
        )
    ]
    candidate = [
        _case(
            "one",
            [("doc", "kept", 0), ("doc", "new", 0)],
            [("doc", "kept", 0), ("doc", "new", 0)],
        )
    ]

    assert _source_diff(candidate, baseline) == {
        "changed_cases": 1,
        "raw_sources_added": 1,
        "raw_sources_lost": 1,
        "final_sources_added": 1,
        "final_sources_lost": 1,
    }


def test_source_diff_uses_source_keys_from_sibling_blocks():
    baseline = [_case("one", [("doc", "main", 0)], [("doc", "main", 0)])]
    baseline[0]["final_blocks"][0]["source_keys"] = [
        {"doc_id": "doc", "slug": "main", "chunk_index": 0},
        {"doc_id": "doc", "slug": "sibling", "chunk_index": 0},
    ]
    candidate = [_case("one", [("doc", "main", 0)], [("doc", "main", 0)])]

    assert _source_diff(candidate, baseline)["final_sources_lost"] == 1
