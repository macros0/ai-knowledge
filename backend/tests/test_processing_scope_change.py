"""Changing the selected sources must never mean regenerating the retained ones."""
from copy import deepcopy

import pytest

from app.services.processing_scope_change import (
    ScopeBaseSnapshot, analyze_scope_change, scope_base_digest,
)


DOC = "1234567890abcdef"
KEYS = [str(i) * 64 for i in (1, 2, 3)]


def example():
    sources = [{"source_id": "root", "parent_source_id": None}]
    nodes, decisions, chunks, concepts = [], [], [], []
    for i, key in enumerate(KEYS):
        sid = f"root/{i}"
        sources.append({"source_id": sid, "parent_source_id": "root", "ordinal": i,
                        "metadata": {"source_key": key}, "container_locator": f"part:{i}",
                        "content_fingerprint": str(i + 4) * 64})
        nodes.append({"source_key": key, "source_id": sid, "parent_key": "root",
                      "locator": f"part:{i}", "content_fingerprint": str(i + 4) * 64,
                      "supported": True, "children_complete": True})
        decisions.append({"source_key": key, "effective_included": True, "reason": "small"})
        chunks.append({"source_id": sid, "chunk_index": i * 10, "content": f"body {i}"})
        concepts.append({"source_id": sid, "chunk_index": i * 10, "slug": f"concept-{i}"})
    scope = {"schema_version": 1, "source_file_hash": "a" * 64,
             "inventory": {"nodes": nodes, "complete": True}, "decisions": decisions, "selection_hash": "b" * 64}
    return ScopeBaseSnapshot(doc_id=DOC, generation_id="c" * 32, file_hash="a" * 64,
                             rows={"sources": sources, "chunks": chunks, "concepts": concepts,
                                   "attachments": []}, scope=scope), deepcopy(scope)


def test_exclude_only_preserves_base_and_reports_exact_retained_counts():
    base, desired = example()
    before = deepcopy(base)
    desired["decisions"][0]["effective_included"] = False
    result = analyze_scope_change(base, desired)
    assert result.mode == "fast_exclude"
    assert result.excluded_source_ids == ["root/0"]
    assert result.counts["removed"]["chunks"] == 1
    assert result.counts["retained"]["concepts"] == 2
    assert result.chunk_counts_after == {"active": 2, "archived": 1, "stored": 3}
    assert base == before
    assert result.workset == {"sources": []}


def test_depth_limited_empty_original_follows_excluded_parent_without_parsing():
    base, desired = example()
    base.rows['sources'].append({'source_id': 'root/0/deeper', 'parent_source_id': 'root/0',
                                'extraction_status': 'skipped_depth', 'artifact_kind': 'original',
                                'saved_path': 'attachments/deeper.bin'})
    desired['decisions'][0]['effective_included'] = False
    before = deepcopy(base)
    plan = analyze_scope_change(base, desired)
    assert plan.mode == 'fast_exclude'
    assert plan.excluded_source_ids == ['root/0', 'root/0/deeper']
    assert plan.workset == {'sources': []}
    assert 'root/0/deeper' not in plan.source_mapping.values(), 'No guessed identity or reusable result for skipped content'
    assert base == before
    # The same placeholder can remain with a retained parent; its payload is
    # still outside generated results and must not block later composition edits.
    desired['decisions'][0]['effective_included'] = True
    assert analyze_scope_change(base, desired).mode == 'noop'


@pytest.mark.parametrize('artifact', ['chunks', 'concepts', 'tables', 'available'])
def test_unmapped_depth_marker_with_content_or_unknown_status_still_blocks(artifact):
    base, desired = example()
    base.rows['sources'].append({'source_id': 'root/0/deeper', 'parent_source_id': 'root/0',
                                'extraction_status': 'skipped_depth'})
    desired['decisions'][0]['effective_included'] = False
    if artifact == 'available':
        base.rows['sources'][-1]['extraction_status'] = 'available'
    else:
        base.rows.setdefault(artifact, []).append({'source_id': 'root/0/deeper', 'chunk_index': 999})
    plan = analyze_scope_change(base, desired)
    assert plan.mode == 'blocked'
    assert set(plan.reasons) & {'incomplete_provenance', 'mixed_provenance'}


@pytest.mark.parametrize("space", [{"kind":"legacy","origin":"c"*32}, {"kind":"proven","fingerprint":"d"*64}])
def test_new_embeddings_block_when_base_space_is_unknown_or_changed(space):
    base, desired = example()
    base.scope["decisions"][0]["effective_included"] = False
    base.rows["chunks"] = base.rows["chunks"][1:]
    base.rows["concepts"] = base.rows["concepts"][1:]
    base.vector_space, base.current_embedding_fingerprint = space, "e"*64
    result = analyze_scope_change(base, desired)
    assert result.mode == "blocked"
    assert "embedding_space_incompatible" in result.reasons


def test_occurrence_identity_not_filename_or_sha():
    base, desired = example()
    for source in base.rows["sources"][1:]:
        source["metadata"] = {}
        source["content_fingerprint"] = "f" * 64
    for node in desired["inventory"]["nodes"]:
        node["content_fingerprint"] = "f" * 64
    desired["decisions"][0]["effective_included"] = False
    assert analyze_scope_change(base, desired).excluded_source_ids == ["root/0"]
    # A matching digest without physical locator is insufficient.
    base.rows["sources"][1]["container_locator"] = None
    result = analyze_scope_change(base, desired)
    assert result.mode == "blocked"
    assert "ambiguous_source_mapping" in result.reasons


@pytest.mark.parametrize("corrupt", ["null", "foreign", "mixed", "cycle"])
def test_null_and_mixed_provenance_block(corrupt):
    base, desired = example()
    desired["decisions"][0]["effective_included"] = False
    if corrupt == "null":
        base.rows["chunks"][1]["source_id"] = None
    elif corrupt == "foreign":
        base.rows["concepts"][1]["source_id"] = "another-source"
    elif corrupt == "mixed":
        base.rows["concepts"][1]["table_refs"] = [{"source_id": "root/0"}]
    else:
        base.rows["sources"][1]["parent_source_id"] = "root/0"
    result = analyze_scope_change(base, desired)
    assert result.mode == "blocked"
    assert set(result.reasons) & {"mixed_provenance", "incomplete_provenance"}


def test_noop_and_mixed_change_modes():
    base, desired = example()
    assert analyze_scope_change(base, desired).mode == "noop"
    base.scope["decisions"][0]["effective_included"] = False
    # Previously excluded data is not in the active canonical rows.
    base.rows["chunks"] = base.rows["chunks"][1:]
    base.rows["concepts"] = base.rows["concepts"][1:]
    desired["decisions"][1]["effective_included"] = False
    result = analyze_scope_change(base, desired)
    assert result.mode == "incremental_generate"
    assert result.excluded_source_ids == ["root/1"]
    assert [s["source_key"] for s in result.workset["sources"]] == [KEYS[0]]


@pytest.mark.parametrize("stages,expected,actions", [
    ({"parse": True, "concepts": True, "embeddings": True}, "reuse_only", ("reuse", "reuse", "reuse")),
    ({"parse": True, "concepts": False, "embeddings": False}, "incremental_generate", ("reuse", "run", "run")),
    ({"parse": True, "concepts": True, "embeddings": False}, "incremental_generate", ("reuse", "reuse", "run")),
])
def test_stage_workset_complete_zero_partial_and_missing_vectors(stages, expected, actions):
    base, desired = example()
    base.scope["decisions"][0]["effective_included"] = False
    base.rows["chunks"] = base.rows["chunks"][1:]
    base.rows["concepts"] = base.rows["concepts"][1:]
    base.caches[KEYS[0]] = {"verified": True, "cache_id": "d" * 64,
        "content_fingerprint": "4" * 64, "stages": stages,
        "counts": {"chunks": 1, "concepts": 0}}
    plan = analyze_scope_change(base, desired)
    assert plan.mode == expected
    row = plan.workset["sources"][0]
    assert tuple(row[k] for k in ("parse", "concepts", "embeddings")) == actions


def test_excluded_parent_overrides_stale_selected_descendant():
    base, desired = example()
    for scope in (base.scope, desired):
        scope["inventory"]["nodes"][1]["parent_key"] = KEYS[0]
    base.rows["sources"][2]["parent_source_id"] = "root/0"
    desired["decisions"][0]["effective_included"] = False
    assert analyze_scope_change(base, desired).excluded_source_ids == ["root/0", "root/1"]


def test_digest_covers_tables_projection_and_cache_not_only_canonical_rows():
    base, _ = example()
    before = scope_base_digest(base)
    base.derived_digest = "changed table cell or fragment"
    assert scope_base_digest(base) != before
    before = scope_base_digest(base)
    base.caches[KEYS[0]] = {"verified": True, "manifest_hash": "changed"}
    assert scope_base_digest(base) != before


@pytest.mark.parametrize("guard,reason", [("candidate_id", "active_update"), ("current_file_hash", "source_changed")])
def test_changed_source_and_busy_base_fail_closed(guard, reason):
    base, desired = example()
    setattr(base, guard, "changed")
    result = analyze_scope_change(base, desired)
    assert result.mode == "blocked" and reason in result.reasons
