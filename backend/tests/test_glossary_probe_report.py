from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from test_scripts import glossary_probe_report


def _source(doc_id: str, slug: str | None, chunk_index: int = 0) -> dict:
    return {"doc_id": doc_id, "slug": slug, "chunk_index": chunk_index}


def _artifact(raw: list[dict], top: list[dict], final: list[dict]) -> dict:
    return {
        "schema_version": 3,
        "manifest": {"case_ids": ["case-1"]},
        "cases": [{
            "id": "case-1",
            "query": "IT 0003",
            "api": "chat",
            "mode": "hybrid",
            "locale": "en",
            "filters": {},
            "raw_candidates": raw,
            "merged_blocks": top,
            "top_k_blocks": top,
            "final_blocks": final,
            "query_plan": {"applied_terms": [{"canonical": "IT0003"}]},
        }],
    }


def test_report_distinguishes_raw_disappearance_and_context_filter():
    main = _source("doc-1", "main")
    sibling = _source("doc-1", "sibling")
    baseline = _artifact(
        [{"rank": 1, "title": "Main", "source": main}, {"rank": 2, "title": "Sibling", "source": sibling}],
        [{"source": main}, {"source": sibling}],
        [{"source": main}, {"source": sibling}],
    )
    current = _artifact(
        [{"rank": 1, "title": "Sibling", "source": sibling}],
        [{"source": sibling}],
        [],
    )

    report = glossary_probe_report.build_report(current, baseline)

    changes = {tuple(item["source"].values()): item for item in report["changes"]}
    assert changes[("doc-1", "main", 0)]["event"] == "sibling_change"
    assert changes[("doc-1", "sibling", 0)]["event"] == "context_filter"
    assert report["summary"]["unresolved"] == 2


def test_report_uses_explicit_relevance_labels():
    source = _source("doc-1", "main")
    baseline = _artifact([{ "rank": 1, "source": source }], [{"source": source}], [{"source": source}])
    current = _artifact([], [], [])
    baseline["cases"][0]["relevance"] = {"doc-1|main|0": "relevant"}

    report = glossary_probe_report.build_report(current, baseline)

    assert report["changes"][0]["relevance"] == "relevant"
    assert report["summary"]["relevant"] == 1


def test_quality_metrics_use_fixed_precision_denominator_and_document_recall():
    sources = [_source("doc-1", f"concept-{index}") for index in range(5)]
    case = _artifact(
        [],
        [],
        [{"source": source} for source in sources],
    )["cases"][0]
    case["category"] = "positive"
    case["expected_documents"] = ["doc-1", "doc-2"]
    case["relevance"] = {
        "doc-1|concept-0|0": "relevant",
        "doc-1|concept-1|0": "relevant",
        "doc-1|concept-2|0": "relevant",
        "doc-1|concept-3|0": "irrelevant",
        "doc-1|concept-4|0": "irrelevant",
    }

    metrics = glossary_probe_report.quality_metrics(case)

    assert metrics["status"] == "READY"
    assert metrics["precision_at_5"] == 0.6
    assert metrics["recall_at_10"] == 0.5
    assert metrics["found_documents"] == ["doc-1"]
    assert metrics["missing_documents"] == ["doc-2"]


def test_quality_metrics_block_when_top_five_has_unresolved_source():
    source = _source("doc-1", "concept")
    case = _artifact([], [], [{"source": source}])["cases"][0]
    case["category"] = "positive"
    case["expected_documents"] = ["doc-1"]

    metrics = glossary_probe_report.quality_metrics(case)

    assert metrics["status"] == "BLOCKED"
    assert metrics["precision_at_5"] == 0.0
    assert metrics["recall_at_10"] == 1.0
    assert metrics["unresolved_sources"] == ["doc-1|concept|0"]
    assert metrics["blocking_reasons"] == ["unresolved_precision_source"]


def test_quality_metrics_blocks_positive_case_when_no_expected_document_is_found():
    case = _artifact([], [], [])["cases"][0]
    case["category"] = "positive"
    case["expected_documents"] = ["doc-expected"]

    metrics = glossary_probe_report.quality_metrics(case)

    assert metrics["status"] == "BLOCKED"
    assert metrics["recall_at_10"] == 0.0
    assert metrics["missing_documents"] == ["doc-expected"]
    assert metrics["blocking_reasons"] == ["expected_document_not_found"]


def test_quality_metrics_blocks_when_mandatory_source_is_missing():
    source = _source("doc-found", "concept")
    case = _artifact([], [], [{"source": source}])["cases"][0]
    case["category"] = "positive"
    case["expected_documents"] = ["doc-found"]
    case["relevance"] = {"doc-found|concept|0": "relevant"}
    case["mandatory_sources"] = [_source("doc-required", "concept")]

    metrics = glossary_probe_report.quality_metrics(case)

    assert metrics["status"] == "BLOCKED"
    assert metrics["mandatory_missing_sources"] == ["doc-required|concept|0"]
    assert metrics["blocking_reasons"] == ["mandatory_source_missing"]


def test_quality_metrics_matches_null_slug_source_key():
    source = _source("doc-1", None)
    case = _artifact([], [], [{"source": source}])["cases"][0]
    case["category"] = "positive"
    case["expected_documents"] = ["doc-1"]
    case["relevance"] = {"doc-1||0": "relevant"}
    case["mandatory_sources"] = [_source("doc-1", "")]

    metrics = glossary_probe_report.quality_metrics(case)

    assert metrics["status"] == "READY"
    assert metrics["precision_at_5"] == 0.2
    assert metrics["recall_at_10"] == 1.0
    assert metrics["mandatory_missing_sources"] == []


def test_quality_metrics_blocks_conflicting_alias_labels():
    source = _source("doc-1", None)
    case = _artifact([], [], [{"source": source}])["cases"][0]
    case["category"] = "positive"
    case["expected_documents"] = ["doc-1"]
    case["relevance"] = {
        "doc-1||0": "relevant",
        "doc-1|None|0": "irrelevant",
    }

    metrics = glossary_probe_report.quality_metrics(case)

    assert metrics["status"] == "BLOCKED"
    assert metrics["conflicting_sources"] == ["doc-1||0"]
    assert "conflicting_relevance_labels" in metrics["blocking_reasons"]


def test_quality_comparison_detects_precision_and_recall_regression():
    sources = [_source("doc-1", f"concept-{index}") for index in range(5)]
    labels = {
        "doc-1|concept-0|0": "relevant",
        "doc-1|concept-1|0": "relevant",
        "doc-1|concept-2|0": "relevant",
        "doc-1|concept-3|0": "irrelevant",
        "doc-1|concept-4|0": "irrelevant",
        "doc-2|concept|0": "relevant",
    }
    baseline = _artifact(
        [],
        [],
        [{"source": _source("doc-2", "concept")}]
        + [{"source": source} for source in sources[:4]],
    )
    current = _artifact([], [], [{"source": sources[3]}, {"source": sources[4]}])
    for artifact in (baseline, current):
        artifact["cases"][0]["category"] = "positive"
        artifact["cases"][0]["expected_documents"] = ["doc-1", "doc-2"]
        artifact["cases"][0]["relevance"] = labels
    quality = glossary_probe_report.compare_quality(current, baseline)

    assert quality["status"] == "FAIL"
    assert quality["summary"]["failed"] == 1
    assert quality["cases"][0]["precision_delta"] < 0
    assert quality["cases"][0]["recall_delta"] < 0


def test_quality_comparison_blocks_different_expected_documents_between_sides():
    source = _source("doc-1", "main")
    baseline = _artifact([], [], [{"source": source}])
    current = _artifact([], [], [{"source": source}])
    for artifact in (baseline, current):
        artifact["cases"][0]["category"] = "positive"
        artifact["cases"][0]["relevance"] = {"doc-1|main|0": "relevant"}
    baseline["cases"][0]["expected_documents"] = ["doc-1", "doc-2"]
    current["cases"][0]["expected_documents"] = ["doc-1"]

    quality = glossary_probe_report.compare_quality(current, baseline)

    assert quality["status"] == "BLOCKED"
    assert quality["cases"][0]["status"] == "BLOCKED"
    assert "expected_documents_differ_between_off_and_on" in quality["cases"][0]["reasons"]


def test_quality_comparison_blocks_missing_relevance_labels_on_one_side():
    source = _source("doc-1", "main")
    baseline = _artifact([], [], [{"source": source}])
    current = _artifact([], [], [{"source": source}])
    baseline["cases"][0].update(
        category="positive",
        relevance={"doc-1|main|0": "relevant"},
        expected_documents=["doc-1"],
    )
    current["cases"][0].update(
        category="positive",
        relevance={},
        expected_documents=["doc-1"],
    )

    quality = glossary_probe_report.compare_quality(current, baseline)

    assert quality["status"] == "BLOCKED"
    assert "labels_differ_between_off_and_on" in quality["cases"][0]["reasons"]
