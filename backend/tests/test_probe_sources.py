from __future__ import annotations

import sys
from dataclasses import dataclass, replace
import os
from pathlib import Path
import subprocess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from test_scripts import probe_sources
from app.services.fusion import Hit
from app.services.glossary.types import MatchGroup, MatchSpan


_MATCH_GROUP = MatchGroup(
    term_id=1,
    canonical="IT0003",
    kind="sap_infotype",
    canonical_locale="en",
    original_name="Payroll Status",
    term_version=1,
    source_revision=1,
    spans=(MatchSpan(0, 11, "alias query", "alias", "alias query"),),
    matched_forms=("IT0003",),
    match_type="alias",
)


@dataclass(frozen=True)
class _Plan:
    original_query: str = "alias query"
    dense_query: str = "alias query\n[Domain term: IT0003 — Payroll Status]"
    added_sparse_texts: tuple[str, ...] = ("IT0003", "инфотип 3")
    match_groups: tuple = (_MATCH_GROUP,)
    strict_groups: tuple = (_MATCH_GROUP,)
    applied_terms: tuple = ()
    status: str = "applied"
    skipped_reasons: tuple = ()
    rules_version: str = "glossary-v1"


class _Settings:
    search_per_branch_top_k = 20


class _Embedder:
    def __init__(self):
        self.queries = []

    def embed(self, query):
        self.queries.append(query)
        return [1.0]


class _VectorStore:
    def __init__(self):
        self.calls = []

    def search_composite(self, **kwargs):
        self.calls.append(kwargs)
        return [
            Hit(
                point_id="point-1",
                score=0.9,
                payload={
                    "doc_id": "doc-full-id",
                    "slug": "concept-full-slug",
                    "chunk_index": 4,
                    "title": "Alias title",
                    "content": "Alias content",
                    "point_type": "concept",
                },
            )
        ]


@pytest.mark.parametrize("use_strict_groups", [False, True])
def test_run_case_uses_api_query_pipeline_and_keeps_full_source_identity(monkeypatch, use_strict_groups):
    strict_group = replace(_MATCH_GROUP, canonical="PA30", kind="sap_transaction", matched_forms=("PA30",))
    plan = _Plan(strict_groups=(strict_group,) if use_strict_groups else ())
    expected_groups = plan.strict_groups or plan.match_groups
    embedder = _Embedder()
    vector_store = _VectorStore()
    events = []

    monkeypatch.setattr(probe_sources, "prepare_query", lambda *args, **kwargs: events.append(("prepare", args[0], kwargs["enabled"])) or plan)
    monkeypatch.setattr(probe_sources, "resolve_branches", lambda *args, **kwargs: {"dense", "bm25"})
    monkeypatch.setattr(probe_sources, "build_query_sparse", lambda *args, **kwargs: events.append(("sparse", args[0])) or "sparse")
    hydration_calls = []
    monkeypatch.setattr(
        probe_sources,
        "load_visible_retrieval_hits",
        lambda hits, **kwargs: hydration_calls.append(kwargs) or (hits, {}),
    )
    merge_calls = []
    monkeypatch.setattr(probe_sources, "merge_and_format", lambda *args, **kwargs: merge_calls.append(kwargs) or [{
        "title": "Alias title",
        "content": "Alias content",
        "tags": [],
        "filepath": "doc-full-id/concept-full-slug.md",
        "doc_id": "doc-full-id",
        "score": 0.9,
        "point_type": "concept",
        "chunk_index": 4,
    }])
    matched = lambda item, query: ["alias"]
    monkeypatch.setattr(probe_sources, "matched_terms", matched)
    monkeypatch.setattr(
        probe_sources,
        "drop_unmatched_blocks",
        lambda blocks, query, *, match_groups, domain_cache=None, lexical_cache=None: events.append(("unmatched", match_groups, domain_cache, lexical_cache)) or blocks,
    )
    monkeypatch.setattr(
        probe_sources,
        "drop_partial_title_matches",
        lambda blocks, query, *, match_groups, domain_cache=None: events.append(("partial", match_groups, domain_cache)) or blocks,
    )

    result = probe_sources.run_case(
        {"id": "case-1", "query": "alias query", "mode": "hybrid", "top_k": 10},
        glossary_mode="on",
        settings=_Settings(),
        vector_store=vector_store,
        embedder=embedder,
    )

    assert events[:2] == [("prepare", "alias query", True), ("sparse", plan)]
    assert embedder.queries == [plan.dense_query]
    assert vector_store.calls[0]["dense_vec"] == [1.0]
    assert vector_store.calls[0]["sparse_vec"] == "sparse"
    assert result["raw_candidates"][0]["source"] == {
        "doc_id": "doc-full-id",
        "slug": "concept-full-slug",
        "chunk_index": 4,
    }
    assert result["final_blocks"][0]["source"]["doc_id"] == "doc-full-id"
    assert result["final_blocks"][0]["source"]["slug"] == "concept-full-slug"
    assert "expected_documents" not in result
    assert "mandatory_sources" not in result
    assert hydration_calls[0]["exact_groups"] == expected_groups
    assert merge_calls[0]["exact_groups"] == expected_groups
    assert any(event[:2] == ("unmatched", expected_groups) and event[2] is not None for event in events)
    assert any(event[:2] == ("partial", expected_groups) and event[2] is not None for event in events)


def test_run_case_preserves_quality_annotations_for_the_report(monkeypatch):
    plan = _Plan()
    vector_store = _VectorStore()

    monkeypatch.setattr(probe_sources, "prepare_query", lambda *args, **kwargs: plan)
    monkeypatch.setattr(probe_sources, "resolve_branches", lambda *args, **kwargs: {"bm25"})
    monkeypatch.setattr(probe_sources, "build_query_sparse", lambda *args, **kwargs: "sparse")
    monkeypatch.setattr(probe_sources, "load_visible_retrieval_hits", lambda hits, **kwargs: (hits, {}))
    monkeypatch.setattr(probe_sources, "merge_and_format", lambda *args, **kwargs: [])

    case = {
        "id": "quality-annotations",
        "query": "alias query",
        "api": "search",
        "mode": "bm25",
        "category": "positive",
        "relevance": {"doc-1|slug-1|0": "relevant"},
        "expected_documents": ["doc-1"],
        "mandatory_sources": [{"doc_id": "doc-1", "slug": "slug-1", "chunk_index": 0}],
    }

    result = probe_sources.run_case(
        case,
        glossary_mode="off",
        settings=_Settings(),
        vector_store=vector_store,
        embedder=_Embedder(),
    )

    assert result["category"] == "positive"
    assert result["relevance"] == case["relevance"]
    assert result["expected_documents"] == case["expected_documents"]
    assert result["mandatory_sources"] == case["mandatory_sources"]


def test_compare_results_reports_only_full_source_loss(tmp_path):
    baseline = {
        "schema_version": 3,
        "cases": [{
            "id": "case-1",
            "raw_candidates": [{"source": {"doc_id": "doc-1", "slug": "slug-1", "chunk_index": 0}}],
            "final_blocks": [{"source": {"doc_id": "doc-1", "slug": "slug-1", "chunk_index": 0}}],
        }]
    }
    current = {
        "schema_version": 3,
        "cases": [{
            "id": "case-1",
            "raw_candidates": [{"source": {"doc_id": "doc-2", "slug": "slug-2", "chunk_index": 0}}],
            "final_blocks": [{"source": {"doc_id": "doc-2", "slug": "slug-2", "chunk_index": 0}}],
        }]
    }

    report = probe_sources.compare_results(current, baseline)

    assert report["full_source_losses"] == {
        "case-1": [{"doc_id": "doc-1", "slug": "slug-1", "chunk_index": 0}]
    }
    assert report["new_sources"]["case-1"] == [
        {"doc_id": "doc-2", "slug": "slug-2", "chunk_index": 0}
    ]

    current["cases"][0]["raw_candidates"] = [
        {"source": {"doc_id": "doc-1", "slug": "slug-1", "chunk_index": 0}}
    ]
    report = probe_sources.compare_results(current, baseline)
    assert report["full_source_losses"] == {}
    assert report["final_only_losses"]["case-1"] == [
        {"doc_id": "doc-1", "slug": "slug-1", "chunk_index": 0}
    ]

    current["cases"][0]["raw_candidates"] = [
        {"source": {"doc_id": "doc-1", "slug": "sibling-slug", "chunk_index": 0}}
    ]
    current["cases"][0]["final_blocks"] = [
        {"source": {"doc_id": "doc-1", "slug": "sibling-slug", "chunk_index": 0}}
    ]
    report = probe_sources.compare_results(current, baseline)
    assert report["full_source_losses"]["case-1"] == [
        {"doc_id": "doc-1", "slug": "slug-1", "chunk_index": 0}
    ]
    assert report["final_only_losses"] == {}


def test_final_block_keeps_only_the_merged_block_source():
    raw_hits = [
        {
            "payload": {
                "doc_id": "doc-1",
                "slug": "main",
                "chunk_index": 0,
                "point_type": "concept",
            },
            "score": 0.9,
        },
        {
            "payload": {
                "doc_id": "doc-1",
                "slug": "sibling",
                "chunk_index": 0,
                "point_type": "concept",
            },
            "score": 0.8,
        },
    ]

    block = {
        "doc_id": "doc-1",
        "slug": "main",
        "chunk_index": 0,
        "source_slug": "main",
        "point_type": "concept",
    }

    result = probe_sources._final_block(block, raw_hits, "query")

    assert result["source"] == {
        "doc_id": "doc-1",
        "slug": "main",
        "chunk_index": 0,
    }
    assert result["source_keys"] == [result["source"]]


def test_compare_results_does_not_fallback_from_empty_raw_to_final():
    baseline = {
        "schema_version": 3,
        "cases": [{
            "id": "case-1",
            "raw_candidates": [],
            "final_blocks": [{"source": {"doc_id": "doc-1", "slug": "s", "chunk_index": 0}}],
        }],
    }
    current = {
        "schema_version": 3,
        "cases": [{"id": "case-1", "raw_candidates": [], "final_blocks": []}],
    }

    report = probe_sources.compare_results(current, baseline)

    assert report["full_source_losses"] == {}
    assert report["diagnostics"]["empty_raw_cases"] == ["case-1"]


def test_compare_results_rejects_incompatible_case_manifests():
    baseline = {
        "schema_version": 3,
        "manifest": {"cases_sha256": "before"},
        "cases": [{"id": "case-1", "query": "before", "raw_candidates": []}],
    }
    current = {
        "schema_version": 3,
        "manifest": {"cases_sha256": "after"},
        "cases": [{"id": "case-1", "query": "after", "raw_candidates": []}],
    }

    with pytest.raises(ValueError, match="incompatible probe artifacts"):
        probe_sources.compare_results(current, baseline)


def test_run_case_search_does_not_apply_chat_context_filters(monkeypatch):
    plan = _Plan()
    vector_store = _VectorStore()
    called = []

    monkeypatch.setattr(probe_sources, "prepare_query", lambda *args, **kwargs: plan)
    monkeypatch.setattr(probe_sources, "resolve_branches", lambda *args, **kwargs: {"bm25"})
    monkeypatch.setattr(probe_sources, "build_query_sparse", lambda *args, **kwargs: "sparse")
    monkeypatch.setattr(probe_sources, "load_visible_retrieval_hits", lambda hits, **kwargs: (hits, {}))
    monkeypatch.setattr(probe_sources, "merge_and_format", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        probe_sources,
        "drop_unmatched_blocks",
        lambda *args, **kwargs: called.append("unmatched") or [],
    )
    monkeypatch.setattr(
        probe_sources,
        "drop_partial_title_matches",
        lambda *args, **kwargs: called.append("partial") or [],
    )

    probe_sources.run_case(
        {"id": "search-case", "query": "alias query", "api": "search", "mode": "bm25"},
        glossary_mode="off",
        settings=_Settings(),
        vector_store=vector_store,
        embedder=_Embedder(),
    )

    assert called == []


def test_run_case_search_uses_search_snippet_hydration_bound(monkeypatch):
    plan = _Plan()
    vector_store = _VectorStore()
    captured = {}

    monkeypatch.setattr(probe_sources, "prepare_query", lambda *args, **kwargs: plan)
    monkeypatch.setattr(probe_sources, "resolve_branches", lambda *args, **kwargs: {"bm25"})
    monkeypatch.setattr(probe_sources, "build_query_sparse", lambda *args, **kwargs: "sparse")

    def load_visible(hits, **kwargs):
        captured.update(kwargs)
        return hits, {}

    monkeypatch.setattr(probe_sources, "load_visible_retrieval_hits", load_visible)
    monkeypatch.setattr(probe_sources, "merge_and_format", lambda *args, **kwargs: [])

    probe_sources.run_case(
        {"id": "search-bound", "query": "alias query", "api": "search", "mode": "bm25"},
        glossary_mode="off",
        settings=_Settings(),
        vector_store=vector_store,
        embedder=_Embedder(),
    )

    assert captured["max_concept_chars"] == 300
    assert captured["max_chunk_chars"] == 300


def test_load_cases_rejects_missing_explicit_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        probe_sources.load_cases(tmp_path / "missing.json")


def test_expand_cases_covers_all_modes_and_both_api_paths():
    cases = probe_sources.expand_cases(
        [{"id": "case-1", "query": "alias", "mode": "hybrid", "api": "chat"}],
        modes=("dense", "bm25", "hybrid"),
        apis=("search", "chat"),
    )

    assert len(cases) == 6
    assert {(case["mode"], case["api"]) for case in cases} == {
        (mode, api) for mode in ("dense", "bm25", "hybrid") for api in ("search", "chat")
    }
    assert len({case["id"] for case in cases}) == 6


def test_off_summary_does_not_enforce_expansion_expectations():
    case = {
        "id": "positive",
        "acceptance": {"missing_canonicals": ["IT0003"], "forbidden_applied": []},
        "timings_ms": {},
    }

    assert probe_sources._summary([case], glossary_mode="off")["acceptance_failures"] == []
    assert probe_sources._summary([case], glossary_mode="on")["acceptance_failures"]


def test_summary_rejects_unexpected_canonical_even_without_forbidden_list():
    case = {
        "id": "negative",
        "acceptance": {
            "missing_canonicals": [],
            "forbidden_applied": [],
            "unexpected_canonicals": ["PA20"],
        },
        "timings_ms": {},
    }

    assert probe_sources._summary([case], glossary_mode="on")["acceptance_failures"] == [
        {
            "id": "negative",
            "missing_canonicals": [],
            "forbidden_applied": [],
            "unexpected_canonicals": ["PA20"],
        }
    ]


def test_default_case_matrix_has_required_categories_and_legacy_probes():
    cases = probe_sources.load_cases()
    categories = {case.get("category") for case in cases}

    assert len(cases) >= 40
    assert {"positive", "negative", "compound", "multilingual", "filter-isolation", "legacy"} <= categories
    assert {case["id"] for case in cases if case.get("category") == "legacy"} >= {
        "legacy-tabular-tag",
        "legacy-en-table",
    }


def test_seed_cli_is_runnable_from_backend_without_pythonpath():
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, "scripts/seed_glossary.py", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--apply" in completed.stdout
