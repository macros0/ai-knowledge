"""Pure planning boundary for changes to an already published document."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from copy import deepcopy
import hashlib
import json

SCOPE_CHANGE_ANALYSIS_VERSION = 2


@dataclass
class ScopeBaseSnapshot:
    doc_id: str
    generation_id: str | None
    file_hash: str | None
    rows: dict
    scope: dict | None = None
    caches: dict = field(default_factory=dict)
    derived_digest: str = ""
    current_file_hash: str | None = None
    candidate_id: str | None = None
    status: str = "done"
    archived_chunks: int = 0
    document_fields: dict = field(default_factory=dict)
    canonical_digest: str = ""
    vector_space: dict | None = None
    current_embedding_fingerprint: str | None = None


@dataclass
class ScopeChangePlan:
    mode: str = "blocked"
    base_generation_id: str | None = None
    selection_hash: str | None = None
    mapping_status: str = "unverified"
    reasons: list[str] = field(default_factory=list)
    source_mapping: dict = field(default_factory=dict)
    excluded_source_ids: list[str] = field(default_factory=list)
    retained_source_ids: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    chunk_counts_after: dict = field(default_factory=dict)
    workset: dict = field(default_factory=lambda: {"sources": []})

    def to_dict(self):
        return asdict(self)


def scope_base_digest(base: ScopeBaseSnapshot) -> str:
    value = asdict(base)
    # The observed disk hash is a separate guard, not a mutable canonical field.
    value.pop("current_file_hash", None)
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def _effective(scope):
    nodes = {n["source_key"]: n for n in scope.get("inventory", {}).get("nodes", [])}
    selected = {d["source_key"] for d in scope.get("decisions", [])
                if d.get("effective_included") is True}
    result = set()
    for key in selected:
        current, seen = key, set()
        while current != "root":
            if current not in selected or current not in nodes or current in seen:
                break
            seen.add(current)
            current = nodes[current].get("parent_key")
        else:
            result.add(key)
    return result


def _source_tree_valid(sources):
    by_id = {s.get("source_id"): s for s in sources}
    if len(by_id) != len(sources) or "root" not in by_id:
        return False
    for sid in by_id:
        seen, current = set(), sid
        while current != "root":
            if current not in by_id or current in seen:
                return False
            seen.add(current)
            current = by_id[current].get("parent_source_id")
        if by_id["root"].get("parent_source_id") is not None:
            return False
    return True


def recover_legacy_source_locators(sources, inventory, *, root_verified, original_hashes):
    """Recover only a unique original occurrence in a verified unchanged root.

    `original_hashes` must come from the registered published original files,
    not their names, ordinals or an unverified SQL fingerprint. Duplicate bytes
    under the same parent deliberately remain unresolved.
    """
    recovered=deepcopy(sources)
    if not root_verified:
        return recovered
    pending={n["source_key"]:n for n in inventory.get("nodes",[])}
    nodes=list(pending.values())
    parents={"root":"root"}
    while pending:
        progressed=False
        for key,node in list(pending.items()):
            parent=node.get("parent_key")
            if parent not in parents:
                continue
            del pending[key];progressed=True
            known=[s for s in recovered if (s.get("metadata") or {}).get("source_key")==key
                   and s.get("parent_source_id")==parents[parent]]
            if len(known)==1:
                parents[key]=known[0]["source_id"]
                continue
            fp=node.get("content_fingerprint")
            if not fp or not node.get("locator") or sum(n.get("parent_key")==parent and
                    n.get("content_fingerprint")==fp for n in nodes)!=1:
                continue
            matches=[s for s in recovered if s.get("parent_source_id")==parents[parent]
                     and s.get("content_fingerprint")==fp]
            if len(matches)!=1:
                continue
            source=matches[0]
            metadata=source.get("metadata") or {}
            if (not source.get("saved_path") or original_hashes.get(source["source_id"])!=fp
                    or metadata.get("source_key") or source.get("container_locator")
                    or metadata.get("source_locator")):
                continue
            source["container_locator"]=node["locator"]
            source["metadata"]={**metadata,"source_key":key,"legacy_locator_verified":True}
            parents[key]=source["source_id"]
        if not progressed:
            break
    return recovered


def _map_sources(base, nodes, result):
    sources = base.rows.get("sources", [])
    by_id = {s["source_id"]: s for s in sources}
    by_key = {}
    for row in sources:
        key = (row.get("metadata") or {}).get("source_key")
        if key:
            by_key.setdefault(key, []).append(row)
    pending = dict(nodes)
    mapping = {"root": "root"}
    while pending:
        progressed = False
        for key, node in list(pending.items()):
            parent = node.get("parent_key")
            if parent not in mapping:
                continue
            progressed = True
            matches = by_key.get(key, [])
            legacy = not matches
            if legacy:
                row = by_id.get(node.get("source_id"))
                matches = [row] if row else [s for s in sources if node.get("content_fingerprint")
                    and s.get("content_fingerprint") == node["content_fingerprint"]
                    and s.get("parent_source_id") == mapping[parent]
                    and (s.get("container_locator") or (s.get("metadata") or {}).get("source_locator")) == node.get("locator")]
            if len(matches) > 1:
                result.reasons.append("ambiguous_source_mapping")
                return {}
            if not matches:
                # Unprocessed nodes may be absent from the published source tree.
                mapping[key] = node.get("source_id") or mapping[parent] + "/s" + key[:12]
            else:
                row = matches[0]
                expected_fp, actual_fp = node.get("content_fingerprint"), row.get("content_fingerprint")
                if expected_fp and actual_fp and expected_fp != actual_fp:
                    result.reasons.append("source_changed")
                    return {}
                locator = row.get("container_locator") or (row.get("metadata") or {}).get("source_locator") or (row.get("metadata") or {}).get("container_locator")
                if row.get("parent_source_id") != mapping[parent] or (legacy and (
                    not expected_fp or expected_fp != actual_fp or not locator or locator != node.get("locator")
                )):
                    result.reasons.append("ambiguous_source_mapping")
                    return {}
                mapping[key] = row["source_id"]
            del pending[key]
        if not progressed:
            result.reasons.append("incomplete_provenance")
            return {}
    if len(set(mapping.values())) != len(mapping):
        result.reasons.append("ambiguous_source_mapping")
        return {}
    return mapping


def _valid_provenance(base, source_ids):
    for kind in ("chunks", "concepts"):
        for row in base.rows.get(kind, []):
            sid = row.get("source_id")
            if not sid or sid not in source_ids:
                return "incomplete_provenance"
            for ref in [*(row.get("source_spans") or []), *(row.get("table_refs") or [])]:
                if ref.get("source_id", sid) != sid:
                    return "mixed_provenance"
    chunk_sources = {r["chunk_index"]: r.get("source_id") for r in base.rows.get("chunks", [])}
    if len(chunk_sources) != len(base.rows.get("chunks", [])):
        return "incomplete_provenance"
    for row in base.rows.get("concepts", []):
        index = row.get("chunk_index")
        if index is not None and chunk_sources.get(index) != row.get("source_id"):
            return "mixed_provenance"
    return None


def _unmapped_depth_placeholders(base, mapping):
    """Allow only empty depth-boundary records under a verified source tree.

    Their retained originals are never opened, mapped to an inventory key or
    promoted to completed parse/concept/cache stages by this exception.
    """
    by_id = {s['source_id']: s for s in base.rows.get('sources', [])}
    mapped = set(mapping.values())
    unmapped = set(by_id) - mapped
    content_sources = {r.get('source_id') for kind in ('chunks', 'concepts', 'tables')
                       for r in base.rows.get(kind, [])}
    for sid in unmapped:
        current, seen = sid, set()
        while current not in mapped:
            row = by_id.get(current)
            if (not row or current in seen or current in content_sources
                    or row.get('extraction_status') != 'skipped_depth'):
                return None
            seen.add(current)
            current = row.get('parent_source_id')
    return unmapped


def analyze_scope_change(base: ScopeBaseSnapshot, selection: dict) -> ScopeChangePlan:
    result = ScopeChangePlan(base_generation_id=base.generation_id,
                             selection_hash=selection.get("selection_hash"))
    if not base.generation_id:
        result.reasons.append("no_published_base")
    if base.candidate_id or base.status != "done":
        result.reasons.append("active_update")
    if not base.file_hash or selection.get("source_file_hash") != base.file_hash or (
        base.current_file_hash is not None and base.current_file_hash != base.file_hash
    ):
        result.reasons.append("source_changed")
    if selection.get("schema_version") != 1:
        result.reasons.append("unsupported_schema")
    sources = base.rows.get("sources", [])
    if not _source_tree_valid(sources):
        result.reasons.append("incomplete_provenance")
    if result.reasons:
        return result
    nodes_list = selection.get("inventory", {}).get("nodes", [])
    nodes = {n["source_key"]: n for n in nodes_list}
    if len(nodes) != len(nodes_list) or len(nodes) > 1000:
        result.reasons.append("incomplete_provenance")
        return result
    mapping = _map_sources(base, nodes, result)
    if result.reasons:
        return result
    result.source_mapping = mapping
    result.mapping_status = "verified"
    reason = _valid_provenance(base, {s["source_id"] for s in sources})
    if reason:
        result.reasons.append(reason)
        return result
    desired_keys = _effective(selection)
    active_keys = _effective(base.scope) if base.scope else {
        key for key, sid in mapping.items() if key != "root" and any(s["source_id"] == sid for s in sources)
    }
    removed_keys, added_keys = active_keys - desired_keys, desired_keys - active_keys
    # A skipped-depth original has no generated content or reliable occurrence
    # locator in legacy parsers. Preserve it structurally and inherit exclusion
    # from its verified parent; never guess its inventory identity from a name.
    placeholders = _unmapped_depth_placeholders(base, mapping)
    if placeholders is None:
        result.reasons.append("incomplete_provenance")
        return result
    excluded = {mapping[k] for k in removed_keys}
    parents = {s['source_id']: s.get('parent_source_id') for s in sources}
    for sid in placeholders:
        parent = parents[sid]
        while parent is not None:
            if parent in excluded:
                excluded.add(sid)
                break
            parent = parents.get(parent)
    result.excluded_source_ids = sorted(excluded)
    result.retained_source_ids = sorted({s["source_id"] for s in sources} - excluded)
    for key in sorted(added_keys):
        node, cache = nodes[key], base.caches.get(key, {})
        reusable = cache.get("verified") is True and cache.get("content_fingerprint") == node.get("content_fingerprint")
        stages = cache.get("stages", {}) if reusable else {}
        parse = stages.get("parse") is True
        concepts = parse and stages.get("concepts") is True
        embeddings = concepts and stages.get("embeddings") is True
        result.workset["sources"].append({
            "source_key": key, "source_id": mapping[key], "fingerprint": node.get("content_fingerprint"),
            "cache_id": cache.get("cache_id") if reusable else None,
            "parse": "reuse" if parse else "run", "concepts": "reuse" if concepts else "run",
            "embeddings": "reuse" if embeddings else "run", "reasons": [] if embeddings else ["missing_stages"],
        })
    result.mode = ("incremental_generate" if any(
        s[stage] == "run" for s in result.workset["sources"] for stage in ("parse", "concepts", "embeddings")
    ) else "reuse_only") if added_keys else ("fast_exclude" if removed_keys else "noop")
    if result.mode == "incremental_generate" and base.current_embedding_fingerprint is not None and (
        not base.vector_space or base.vector_space.get("kind") != "proven"
        or base.vector_space.get("fingerprint") != base.current_embedding_fingerprint
    ):
        result.mode="blocked"
        result.reasons.append("embedding_space_incompatible")
    counts = {"before": {}, "removed": {}, "retained": {}}
    for kind in ("sources", "chunks", "concepts", "tables"):
        records = sources if kind == "sources" else base.rows.get(kind, [])
        before = len(records)
        removed = sum(r.get("source_id") in excluded for r in records)
        counts["before"][kind], counts["removed"][kind], counts["retained"][kind] = before, removed, before - removed
    for kind in ("table_rows", "search_points"):
        for group in counts.values():
            group[kind] = None  # Exact values require verified table/index manifests.
    result.counts = counts
    added_counts = []
    for key in added_keys:
        cache = base.caches.get(key, {})
        added_counts.append((cache.get("counts") or {}).get("chunks") if cache.get("verified") else None)
    known = all(n is not None for n in added_counts)
    restored = sum(added_counts) if known else None
    active = counts["retained"]["chunks"] + restored if restored is not None else None
    archived = base.archived_chunks + counts["removed"]["chunks"] - (restored or 0) if known else None
    result.chunk_counts_after = {"active": active, "archived": max(0, archived) if archived is not None else None,
                                 "stored": active + max(0, archived) if active is not None and archived is not None else None}
    return result


def load_scope_base(doc_id: str, *, session=None) -> ScopeBaseSnapshot:
    """Read a consistent canonical/derived snapshot; no parsing or provider calls."""
    from sqlalchemy import select
    from app.config import get_settings
    from app.db.models import (Document, DocumentGeneration, DocumentGenerationState, DocumentProcessingReview,
                               DocumentTable)
    from app.db.session import session_scope
    from app.services.canonical_repair import _canonical_rows
    from app.services.generation_store import lock_generation_read
    from app.services.generation_artifacts import file_digest
    from app.services.generation_files import _reject_links
    from pathlib import Path
    from contextlib import nullcontext

    settings = get_settings()
    with (session_scope() if session is None else nullcontext(session)) as session:
        lock_generation_read(session, [doc_id])
        document = session.get(Document, doc_id)
        if document is None or document.deleted_at:
            raise ValueError("Document unavailable")
        state = session.get(DocumentGenerationState, doc_id)
        gid = state.active_generation_id if state else None
        generation = session.get(DocumentGeneration, gid) if gid else None
        rows = _canonical_rows(session, doc_id)
        rows["tables"] = [dict(row._mapping) for row in session.execute(
            select(DocumentTable.table_id, DocumentTable.source_id).where(
                DocumentTable.doc_id == doc_id, DocumentTable.generation_id == gid))]
        canonical_digest = scope_sql_digest(session, doc_id, canonical_rows=rows)
        # scope_sql_digest already covers the versioned tables/projections.
        # A second full table walk duplicated JSON decoding and hashing.
        path = settings.uploads_dir / (doc_id + Path(document.filename).suffix.lower())
        _reject_links(path)
        current_hash = file_digest(path) if path.is_file() else "missing"
        original_hashes={}
        for source in rows["sources"]:
            if source.get("saved_path"):
                saved = (settings.uploads_dir / doc_id / source["saved_path"]).resolve()
                root = (settings.uploads_dir / doc_id).resolve()
                if saved.is_relative_to(root):
                    _reject_links(saved)
                    if saved.is_file():
                        source["content_fingerprint"] = file_digest(saved)
                        original_hashes[source["source_id"]]=source["content_fingerprint"]
        review=session.get(DocumentProcessingReview,doc_id)
        if review:
            rows["sources"]=recover_legacy_source_locators(rows["sources"],review.inventory_json,
                root_verified=current_hash==document.file_hash==review.source_file_hash,original_hashes=original_hashes)
        from app.services.processing_scope_cache import (SourceResultCache, embedding_fingerprint,
            generation_vector_space, vector_space_fingerprint)
        from app.services.generation_files import generation_paths
        caches = {}
        space=generation_vector_space(generation.processing_scope_json,gid) if generation else None
        archived_chunks = 0
        if generation:
            active_scope=generation.processing_scope_json or {}
            active_keys = _effective(active_scope) if active_scope else {
                (s.get("metadata") or {}).get("source_key") for s in rows["sources"]}
            roots = [settings.uploads_dir / doc_id / "scope-stage-cache",
                     generation_paths(settings, doc_id, gid).bundle / "source-cache"]
            cache_nodes = {n["source_key"]: n for n in active_scope.get("inventory", {}).get("nodes", [])}
            if review and review.source_file_hash == document.file_hash:
                cache_nodes.update({n["source_key"]: n for n in review.inventory_json.get("nodes", [])})
            for node in cache_nodes.values():
                identity = {**node, "embedding_fingerprint": vector_space_fingerprint(space,doc_id)}
                for root in roots:
                    cache = SourceResultCache(root, doc_id)
                    hit = cache.inspect(identity)
                    previous = caches.get(node["source_key"], {})
                    if hit.get("verified") and sum(hit["stages"].values()) > sum(previous.get("stages", {}).values()):
                        caches[node["source_key"]] = {**hit, "location": str(root)}
                if node["source_key"] not in active_keys:
                    archived_chunks += (caches.get(node["source_key"], {}).get("counts") or {}).get("chunks", 0)
        return ScopeBaseSnapshot(doc_id, gid, document.file_hash, rows,
            scope=deepcopy(generation.processing_scope_json) if generation else None,
            caches=caches, archived_chunks=archived_chunks,
            derived_digest=canonical_digest, current_file_hash=current_hash,
            candidate_id=state.candidate_generation_id if state else None, status=document.status,
            canonical_digest=canonical_digest,
            vector_space=space, current_embedding_fingerprint=embedding_fingerprint(settings),
            document_fields={"filename": document.filename, "problem": document.problem,
                             "global_tags":[item.tag_rel.canonical_text for item in document.tags_rel],
                             "parse_warnings": deepcopy(document.parse_warnings),
                             "source_locale": document.source_locale})


def scope_sql_digest(session, doc_id, *, generation_id=None, canonical_rows=None):
    """Canonical publication guard, including versioned rows, before pointer swap."""
    from sqlalchemy import inspect, select, cast, Text, func
    from app.db.models import DocumentGenerationState, DocumentTable, DocumentTableRow, DocumentChunkProjection, DocumentChunkFragment
    from app.services.canonical_repair import _canonical_rows
    rows = deepcopy(canonical_rows) if canonical_rows is not None else _canonical_rows(session, doc_id)
    # Lightweight source ownership used by the analyzer is not canonical SQL.
    rows.pop("tables",None)
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode())
    state = session.get(DocumentGenerationState, doc_id)
    gid = generation_id if generation_id is not None else (state.active_generation_id if state else None)
    for model in (DocumentTable, DocumentTableRow, DocumentChunkProjection, DocumentChunkFragment):
        fields = [a.key for a in inspect(model).column_attrs if a.key != "cells_projection"]
        columns=[getattr(model,key) for key in fields]
        cells_index=fields.index("cells") if model is DocumentTableRow else None
        database_hash=session.get_bind().dialect.name=="postgresql"
        if cells_index is not None:
            serialized=cast(model.cells,Text)
            columns[cells_index]=(func.encode(func.sha256(func.convert_to(serialized,"UTF8")),"hex")
                                  if database_hash else serialized)
        stmt = select(*columns).where(model.doc_id == doc_id, model.generation_id == gid)
        stmt = stmt.order_by(*model.__table__.primary_key.columns).execution_options(yield_per=64)
        digest.update(model.__tablename__.encode())
        for row in session.execute(stmt):
            values=list(row)
            if cells_index is not None and not database_hash:
                values[cells_index]=hashlib.sha256(values[cells_index].encode()).hexdigest()
            digest.update(json.dumps(values, sort_keys=True, default=str).encode())
    return digest.hexdigest()
