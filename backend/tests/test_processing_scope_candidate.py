from copy import deepcopy
from tests.test_processing_scope_change import example, KEYS
from app.services.processing_scope_change import analyze_scope_change
from app.services.processing_scope_candidate import build_scope_candidate


def test_exclusion_preserves_content_ids_and_provenance():
    base, desired = example()
    desired["decisions"][1]["effective_included"] = False
    for c in base.rows["concepts"]:
        c.update(content="unchanged", generated_at="2026-10-01", model_id="old-model", prompt_version="old-prompt")
    before = deepcopy(base.rows)
    result = build_scope_candidate(base, analyze_scope_change(base, desired), "f" * 32, {})
    assert [c["chunk_index"] for c in result["chunks"]] == [0, 20]
    assert [c["slug"] for c in result["concepts"]] == ["concept-0", "concept-2"]
    assert result["concepts"][0] == before["concepts"][0]
    assert result["archived_sources"]["root/1"]["chunks"] == [before["chunks"][1]]
    assert base.rows == before


def test_archive_is_not_active_manifest_and_original_sources_remain():
    base, desired = example()
    desired["decisions"][0]["effective_included"] = False
    result = build_scope_candidate(base, analyze_scope_change(base, desired), "f" * 32, {})
    assert len(result["sources"]) == 4
    assert result["sources"][1]["metadata"]["processing_excluded"] is True
    assert all(row["source_id"] != "root/0" for kind in ("chunks", "concepts") for row in result[kind])


def test_filtered_tables_and_fragment_refs():
    base, desired = example()
    desired["decisions"][0]["effective_included"] = False
    base.rows["tables"] = [{"source_id": "root/0", "table_id": "0" * 32},
                            {"source_id": "root/1", "table_id": "1" * 32}]
    for row in (base.rows["chunks"][1], base.rows["concepts"][1]):
        row["table_refs"] = [{"source_id": "root/1", "generation_id": base.generation_id, "table_id": "1" * 32}]
    result = build_scope_candidate(base, analyze_scope_change(base, desired), "f" * 32, {})
    assert [t["table_id"] for t in result["tables"]] == ["1" * 32]
    assert result["chunks"][0]["table_refs"][0]["generation_id"] == "f" * 32
    assert base.rows["chunks"][1]["table_refs"][0]["generation_id"] == base.generation_id


def test_added_results_merge_without_changing_retained_concepts():
    base, desired = example()
    cached = {"chunks": [base.rows["chunks"].pop(0)], "concepts": [base.rows["concepts"].pop(0)]}
    base.scope["decisions"][0]["effective_included"] = False
    plan = analyze_scope_change(base, desired)
    result = build_scope_candidate(base, plan, "f" * 32, {KEYS[0]: cached})
    assert sorted(c["chunk_index"] for c in result["chunks"]) == [0, 10, 20]
    assert len({c["slug"] for c in result["concepts"]}) == 3


def test_uncompleted_or_foreign_added_results_never_publish():
    import pytest
    base, desired = example()
    base.scope["decisions"][0]["effective_included"] = False
    base.rows["chunks"] = base.rows["chunks"][1:]
    base.rows["concepts"] = base.rows["concepts"][1:]
    plan = analyze_scope_change(base, desired)
    with pytest.raises(ValueError):
        build_scope_candidate(base, plan, "f" * 32, {})
    with pytest.raises(ValueError):
        build_scope_candidate(base, plan, "f" * 32, {KEYS[0]: {"chunks": [{"source_id": "root/other"}], "concepts": []}})
