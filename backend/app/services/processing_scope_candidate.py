"""Compose canonical publication data without changing generated content."""
from copy import deepcopy

from app.services.processing_scope_change import _valid_provenance


def build_scope_candidate(base, plan, generation_id, completed_sources):
    if plan.mode not in {"fast_exclude", "reuse_only", "incremental_generate"}:
        raise ValueError("Change plan is not applicable")
    removed = set(plan.excluded_source_ids)
    result = {kind: [] for kind in ("chunks", "concepts", "tables")}
    result.update(sources=deepcopy(base.rows.get("sources", [])),
                  attachments=deepcopy(base.rows.get("attachments", [])), archived_sources={})
    for sid in removed:
        result["archived_sources"][sid] = {
            kind: deepcopy([r for r in base.rows.get(kind, []) if r.get("source_id") == sid])
            for kind in ("chunks", "concepts", "tables")
        }
    for kind in ("chunks", "concepts", "tables"):
        result[kind] = deepcopy([r for r in base.rows.get(kind, []) if r.get("source_id") not in removed])
    for source in result["sources"]:
        metadata = source.setdefault("metadata", {}) or {}
        source["metadata"] = metadata
        for key, sid in plan.source_mapping.items():
            if sid == source["source_id"] and key != "root":
                metadata["source_key"] = key
        if source["source_id"] in removed:
            metadata["processing_excluded"] = True
            metadata["processing_reason"] = "user_excluded"
    expected = {s["source_key"]: s for s in plan.workset["sources"]}
    if set(completed_sources) != set(expected):
        raise ValueError("Missing or unexpected completed sources")
    by_source = {s["source_id"]: s for s in result["sources"]}
    for key, data in completed_sources.items():
        sid = expected[key]["source_id"]
        for kind in ("chunks", "concepts", "tables"):
            for record in data.get(kind, []):
                if record.get("source_id") != sid:
                    raise ValueError("Completed result belongs to another source")
                result[kind].append(deepcopy(record))
        if sid not in by_source:
            source = deepcopy(data.get("source"))
            if not source or source.get("source_id") != sid:
                raise ValueError("New source provenance missing")
            result["sources"].append(source)
            by_source[sid] = source
        metadata = by_source[sid].get("metadata") or {}
        metadata.pop("processing_excluded", None)
        metadata.pop("processing_reason", None)
        by_source[sid]["metadata"] = metadata
    if len({c["chunk_index"] for c in result["chunks"]}) != len(result["chunks"]) or (
        len({c["slug"] for c in result["concepts"]}) != len(result["concepts"])
    ):
        raise ValueError("Duplicate canonical identity")
    from dataclasses import replace
    reason = _valid_provenance(replace(base, rows=result), set(by_source))
    if reason:
        raise ValueError(reason)
    for row in [*result["chunks"], *result["concepts"]]:
        for ref in row.get("table_refs") or []:
            ref["generation_id"] = generation_id
    result["document_fields"] = {"okf_concept_count": len(result["concepts"]),
        "total_chunks": len(result["chunks"]), "processed_chunks": len(result["chunks"])}
    return result
