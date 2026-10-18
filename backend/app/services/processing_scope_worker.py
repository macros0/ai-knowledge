"""Prepare a new publication using existing source-level processing results."""
from copy import deepcopy
import json
import logging
import shutil
import hashlib
from itertools import chain, islice
from pathlib import Path

from sqlalchemy import inspect, select
from qdrant_client import models as qm

from app.db.models import (Document, DocumentGeneration, DocumentScopeChange, Development,
    DocumentTable, DocumentTableRow, DocumentChunkProjection, DocumentChunkFragment)
from app.services.generation_artifacts import artifact_manifest, file_digest, GenerationIntegrityError
from app.services.generation_files import generation_paths, prepare_generation_paths, _reject_links
from app.services.processing_scope_change import load_scope_base, ScopeChangePlan
from app.services.processing_scope_candidate import build_scope_candidate
from app.services.processing_scope_cache import SourceResultCache
from app.services.processing_scope_order import assign_scope_display_order
from app.services.staging import write_json_atomic
from app.services.update_workers import scope_write_guard, register_update_worker, finish_update_worker
from app.services.vector_store import concept_point_id, chunk_point_id

logger = logging.getLogger(__name__)


def _point_identity_key(identity):
    # Fragment group IDs include the publication generation. All source and
    # descriptor fields must still match exactly across generations.
    return json.dumps({k: v for k, v in identity.items() if k != "chunk_fragment_group"}, sort_keys=True)


def _index_ranges(indices):
    result=[]
    for index in sorted(set(indices)):
        if result and index==result[-1][1]+1:
            result[-1][1]=index
        else:
            result.append([index,index])
    return result


def _rows(session, model, doc_id, generation_id, **filters):
    return list(_iter_rows(session,model,doc_id,generation_id,**filters))


def _iter_rows(session, model, doc_id, generation_id, **filters):
    fields = [a.key for a in inspect(model).column_attrs if a.key != "cells_projection"]
    query = select(*(getattr(model, key) for key in fields)).where(model.doc_id == doc_id, model.generation_id == generation_id)
    for key, value in filters.items():
        query = query.where(getattr(model,key).in_(value) if isinstance(value,(set,list,tuple)) else getattr(model,key)==value)
    for row in session.execute(query.execution_options(yield_per=64)):
        yield dict(zip(fields,row))


def _quality_reports(settings, doc_id, generation_id):
    from app.services.table_quality import TableQualityArtifact
    path = generation_paths(settings, doc_id, generation_id).bundle / "table-quality.json"
    _reject_links(path)
    return (TableQualityArtifact.model_validate_json(path.read_text(encoding="utf-8")).reports
            if path.is_file() else [])


def _prepare_quality(session, settings, doc_id, base, plan, generation_id, bundle, completed, proposal):
    from app.services.table_pipeline import prepare_table_artifacts
    from app.services.table_quality import reports_from_chunks, write_table_quality_report
    from app.services.pipeline import _generation_problem
    from app.services.parse_diagnostics import summarize_problems
    excluded = set(plan.excluded_source_ids)
    reports = [r for r in _quality_reports(settings, doc_id, base.generation_id)
               if r.table_ref.kind == "legacy" and r.table_ref.source_id not in excluded]
    warnings = deepcopy(base.document_fields.get("parse_warnings") or [])
    chunks_data = {}
    for result in completed.values():
        from app.services.table_quality import TableQualityReport
        reports.extend(TableQualityReport.model_validate(r) for r in result.get("quality_reports", [])
                       if r["table_ref"]["kind"] == "legacy")
        chunks_data.update(result.get("chunks_data", {}))
        for warning in result.get("warnings", []):
            if warning not in warnings:
                warnings.append(warning)
    reports.extend(reports_from_chunks(chunks_data))
    reports.extend(prepare_table_artifacts(doc_id, generation_id, bundle, session=session))
    # A restored legacy report may also be present in a completed chunk event.
    reports = list({json.dumps(r.model_dump(mode="json"), sort_keys=True): r for r in reports}.values())
    write_table_quality_report(bundle, reports)
    proposal["document_fields"].update(parse_warnings=warnings,
        problem=summarize_problems(warnings, _generation_problem(chunks_data),
                                   base.document_fields.get("problem"), None))


def _identities(session, doc_id, generation_id, rows, projection_version):
    result = {concept_point_id(doc_id, c["slug"], generation_id=generation_id): {
        "point_type": "concept", "slug": c["slug"], "chunk_index": c.get("chunk_index"),
        "source_id": c.get("source_id"), "chunk_fragment": None} for c in rows["concepts"]}
    if projection_version is None:
        result.update({chunk_point_id(doc_id, c["chunk_index"], generation_id=generation_id): {
            "point_type": "chunk", "chunk_index": c["chunk_index"], "source_id": c.get("source_id"),
            "chunk_fragment": None} for c in rows["chunks"]})
    else:
        from app.services.chunk_projection_publication import verify_chunk_projection_publication
        result.update(verify_chunk_projection_publication(session, doc_id, generation_id, rows["chunks"]))
    return result


def _read_points(store, doc_id, generation_id, identities):
    return list(_iter_points(store,doc_id,generation_id,identities))


def _iter_points(store, doc_id, generation_id, identities):
    ids = list(identities)
    for start in range(0, len(ids), 256):
        expected = set(ids[start:start + 256])
        points = store.client.retrieve(collection_name=store.collection, ids=list(expected), with_payload=True, with_vectors=True)
        if {str(p.id) for p in points} != expected:
            raise GenerationIntegrityError()
        for point in points:
            payload = point.payload or {}
            identity = identities[str(point.id)]
            if payload.get("doc_id") != doc_id or payload.get("generation_id") != generation_id or any(
                payload.get(k) != v for k, v in identity.items()
            ) or not isinstance(point.vector, dict) or not point.vector.get("") or "sparse" not in point.vector:
                raise GenerationIntegrityError()
            vector = {k: (v.model_dump(mode="json") if hasattr(v, "model_dump") else v) for k, v in point.vector.items()}
            yield {"id": str(point.id), "payload": payload, "vector": vector}


def _copy_derived(session, doc_id, base_id, new_id, proposal, completed, bundle):
    from app.services.table_store import clone_selected_generation_tables
    from app.services.chunk_index_projection import chunk_fragment_point_id_from_descriptor
    active = {s["source_id"] for s in proposal["sources"] if not (s.get("metadata") or {}).get("processing_excluded")}
    clone_selected_generation_tables(session, doc_id, base_id, new_id, bundle, active,export_artifacts=False)
    indices = {c["chunk_index"] for c in proposal["chunks"]}
    parents = [r for r in _rows(session, DocumentChunkProjection, doc_id, base_id) if r["chunk_index"] in indices]
    fragments = [r for r in _rows(session, DocumentChunkFragment, doc_id, base_id) if r["chunk_index"] in indices]
    restored_tables = []
    restored_rows=chain.from_iterable(artifact.get("table_rows",[]) for artifact in completed.values())
    for artifact in completed.values():
        parents.extend(artifact.get("projections", []))
        fragments.extend(artifact.get("fragments", []))
        restored_tables.extend(artifact.get("tables", []))
    source_by_index={c["chunk_index"]:c.get("source_id") for c in proposal["chunks"]}
    for model, records in ((DocumentTable, restored_tables), (DocumentTableRow, restored_rows),
                            (DocumentChunkProjection, parents), (DocumentChunkFragment, fragments)):
        if model is DocumentChunkFragment:
            records=[{**row,"point_id":chunk_fragment_point_id_from_descriptor(doc_id,row["chunk_index"],new_id,
                source_by_index[row["chunk_index"]],row["descriptor"])} for row in records]
        _insert_scope_records(session,model,doc_id,new_id,records)
    session.flush()
    # _prepare_quality exports the complete candidate once, after all SQL rows
    # are present. Exporting here duplicated every restored cell's serialization.


def _insert_scope_records(session,model,doc_id,generation_id,records):
    """Bounded bulk restore; identical retries remain valid, conflicts fail."""
    from sqlalchemy import insert
    session.flush()
    keys=list(model.__table__.primary_key.columns)
    existing_keys=set(tuple(row) for row in session.execute(select(*keys).where(
        model.doc_id==doc_id,model.generation_id==generation_id)))
    seen,batch={},[]
    for record in records:
        if record.get("doc_id")!=doc_id:
            raise GenerationIntegrityError()
        values={**record,"generation_id":generation_id}
        primary=tuple(values[c.key] for c in keys)
        fingerprint=hashlib.sha256(json.dumps(values,sort_keys=True,default=str).encode()).digest()
        if primary in seen:
            if seen[primary]!=fingerprint:raise GenerationIntegrityError()
            continue
        seen[primary]=fingerprint
        if primary in existing_keys:
            existing=session.get(model,primary)
            if any(getattr(existing,key)!=value for key,value in values.items()):
                raise GenerationIntegrityError()
            continue
        batch.append(values)
        if len(batch)==256:
            session.execute(insert(model),batch)
            batch=[]
    if batch:session.execute(insert(model),batch)


def execute_scope_change(doc_id, plan_id, owner_token):
    from app.services.pipeline import get_pipeline
    pipeline = get_pipeline()
    settings, store = pipeline.settings, pipeline.vector_store
    base = load_scope_base(doc_id)
    if base.current_file_hash != base.file_hash:
        raise GenerationIntegrityError()
    with scope_write_guard(doc_id, plan_id, owner_token) as session:
        operation = session.get(DocumentScopeChange, plan_id)
        generation_id = operation.candidate_generation_id
        data = deepcopy(operation.plan_json)
        if data["canonical_digest"] and base.canonical_digest != data["canonical_digest"]:
            raise GenerationIntegrityError()
        plan = ScopeChangePlan(**data["preview"])
        paths = prepare_generation_paths(session, settings, doc_id, generation_id)
        # Seed before removal: a later addition must not reuse an archived ID.
        from app.services.processing_scope_order import reserve_chunk_indices
        reserve_chunk_indices(session, doc_id, 0)
        operation.state = "copying"
    from app.services.processing_scope_cache import generation_vector_space, vector_space_fingerprint
    fingerprint = vector_space_fingerprint(base.vector_space or generation_vector_space(base.scope,base.generation_id),doc_id)
    node_by_key = {n["source_key"]: n for n in data["snapshot"]["inventory"]["nodes"]}
    destination = SourceResultCache(paths.bundle / "source-cache", doc_id)
    completed, restored_streams, new_originals = {}, [], []
    guard = lambda: scope_write_guard(doc_id, plan_id, owner_token)
    with register_update_worker(doc_id, plan_id, owner_token):
        with guard() as session:
            old_identities = _identities(session, doc_id, base.generation_id, base.rows, base.rows.get("chunk_projection_version"))
        # Carry inactive source results independently of retired directories.
        desired = {d["source_key"] for d in data["snapshot"]["decisions"] if d["effective_included"]}
        for key, cache in base.caches.items():
            if key not in desired and cache.get("verified"):
                with guard():
                    SourceResultCache(cache["location"], doc_id).copy_to(
                        {**node_by_key[key], "embedding_fingerprint": fingerprint}, destination)
        for sid, archive_key in ((sid, key) for key, sid in plan.source_mapping.items() if sid in plan.excluded_source_ids):
            source = {**node_by_key[archive_key], "embedding_fingerprint": fingerprint}
            with guard() as session:
                table_records = _rows(session, DocumentTable, doc_id, base.generation_id, source_id=sid)
                table_ids = {t["table_id"] for t in table_records}
                parse_artifact = {"chunks": [r for r in base.rows["chunks"] if r.get("source_id") == sid],
                    "global_tags":base.document_fields.get("global_tags",[]),
                    "quality_reports": [r.model_dump(mode="json") for r in _quality_reports(settings, doc_id, base.generation_id)
                                        if r.table_ref.source_id == sid],
                    "tables": table_records,
                    "projections": _rows(session, DocumentChunkProjection, doc_id, base.generation_id, source_id=sid),
                    "source": next(r for r in base.rows["sources"] if r["source_id"] == sid)}
                archived_indices = {c["chunk_index"] for c in parse_artifact["chunks"]}
                parse_artifact["fragments"] = [r for r in _rows(session, DocumentChunkFragment, doc_id, base.generation_id) if r["chunk_index"] in archived_indices]
                destination.save_stage(source,"parse",parse_artifact,record_streams={"table_rows":
                    _iter_rows(session,DocumentTableRow,doc_id,base.generation_id,table_id=table_ids)})
                # Legacy document-level diagnostics do not identify the damaged
                # source. Preserve output, but never invent chunk completion.
                complete=base.document_fields.get("problem") not in {"llm_partial_result","table_content_omitted"}
                destination.save_stage(source, "concepts", {
                    "concepts": [r for r in base.rows["concepts"] if r.get("source_id") == sid],
                    "stage_complete":complete,
                    "completed_chunks":sorted(archived_indices) if complete else []})
                identities = {pid: ident for pid, ident in old_identities.items() if ident.get("source_id") == sid}
                if complete:
                    destination.save_stage(source,"embeddings",{"embedding_fingerprint":fingerprint},record_streams={
                        "vectors":_iter_points(store,doc_id,base.generation_id,identities)})
        generated = {}
        if any(w[s] == "run" for w in plan.workset["sources"] for s in ("parse", "concepts", "embeddings")):
            from app.services.processing_scope_delta import run_scope_delta
            generated = run_scope_delta(doc_id, plan_id, plan.workset, owner_token)
        for work in plan.workset["sources"]:
            key = work["source_key"]
            source = {**node_by_key[key], "embedding_fingerprint": fingerprint}
            from app.services.processing_scope_cache import inspect_source_cache
            descriptor = inspect_source_cache(doc_id, source)
            cache = SourceResultCache(descriptor["location"], doc_id)
            with guard():
                if not all(cache.inspect(source)["stages"].values()):
                    raise GenerationIntegrityError()
                completed[key] = {**cache.read_stage(source,"parse",omit_records={"table_rows"}), **cache.read_stage(source, "concepts")}
                embeddings = cache.read_stage(source,"embeddings",omit_records={"vectors"})
                for kind in ("projections", "fragments"):
                    if kind in embeddings:
                        completed[key][kind] = embeddings[kind]
                if key in generated:
                    completed[key].update(generated[key])
                cache.copy_to(source, destination)
                restored_streams.append((destination,source))
                completed[key]["table_rows"]=destination.iter_stage_records(source,"parse","table_rows")
                if work["source_id"] not in {s["source_id"] for s in base.rows["sources"]}:
                    new_originals.append((work["source_id"], key, source, cache.original_path(source)))
        for result in completed.values():
            prior_globals=set(result.get("global_tags",[]))
            for concept in result.get("concepts",[]):
                concept["tags"]=[tag for tag in concept.get("tags") or [] if tag not in prior_globals]
        proposal = build_scope_candidate(base, plan, generation_id, completed)
        from app.services.processing_scope_relations import reconcile_scope_relations
        suspended_path=generation_paths(settings,doc_id,base.generation_id).bundle/"suspended_relations.jsonl"
        _reject_links(suspended_path)
        suspended=[]
        if suspended_path.is_file():
            with suspended_path.open(encoding="utf-8") as stream:
                for line in stream:
                    if len(line)>65536 or len(suspended)>=100000:
                        raise GenerationIntegrityError()
                    suspended.append(json.loads(line))
        known_concepts=[]
        for key,descriptor in base.caches.items():
            if descriptor.get("verified"):
                cache=SourceResultCache(descriptor["location"],doc_id)
                source={**node_by_key[key],"embedding_fingerprint":fingerprint}
                try:
                    records=cache.read_stage(source,"concepts",allow_partial=True)
                except (OSError,ValueError):
                    continue
                known_concepts.extend(records.get("concepts",[]))
        suspended=reconcile_scope_relations([*known_concepts,*base.rows["concepts"]],proposal["concepts"],suspended)
        with guard():
            (paths.bundle/"suspended_relations.jsonl").write_text(
                "".join(json.dumps(edge,sort_keys=True)+"\n" for edge in suspended),encoding="utf-8")
        archived_count, archived_sources = 0, []
        for key,node in node_by_key.items():
            if key in desired:
                continue
            source={**node,"embedding_fingerprint":fingerprint}
            if destination.inspect(source).get("verified"):
                archived=destination.read_stage(source,"parse",record_kinds={"chunks"}).get("chunks",[])
                archived_count+=len(archived)
                if archived:
                    archived_sources.append({"source_id":plan.source_mapping[key],
                        "ranges":_index_ranges(c["chunk_index"] for c in archived)})
        data["snapshot"]["chunk_counts"]={"active":len(proposal["chunks"]),"archived":archived_count,
                                          "stored":len(proposal["chunks"])+archived_count}
        data["snapshot"]["archived_chunk_sources"]=archived_sources
        assign_scope_display_order(proposal["sources"], proposal["chunks"], proposal["concepts"])
        with guard() as session:
            _copy_derived(session, doc_id, base.generation_id, generation_id, proposal, completed, paths.bundle)
            generation = session.get(DocumentGeneration, generation_id)
            generation.processing_scope_json=deepcopy(data["snapshot"])
            generation.table_schema_version = session.get(DocumentGeneration, base.generation_id).table_schema_version
            if session.scalar(select(DocumentTable.table_id).where(DocumentTable.doc_id == doc_id,
                    DocumentTable.generation_id == generation_id).limit(1)):
                generation.table_schema_version = 1
            from app.services.canonical_repair import _copy_artifacts, _write_bundle
            document = session.get(Document, doc_id)
            proposal.update(filename=document.filename, global_tags=[t.tag_rel.canonical_text for t in document.tags_rel])
            _copy_artifacts(settings, doc_id, base.generation_id, generation_id, paths, proposal)
            for sid, key, source, original in new_originals:
                # New originals are registered in this candidate, independently
                # of review/staging lifetime, just like retained originals.
                name = f"source-{key}{original.suffix}"
                for target in (paths.attachments / name, paths.bundle / "attachments" / name):
                    _reject_links(target)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(original, target)
                    if file_digest(target) != source["content_fingerprint"]:
                        raise GenerationIntegrityError()
                record = next(s for s in proposal["sources"] if s["source_id"] == sid)
                record["saved_path"] = f"generations/{generation_id}/attachments/{name}"
            written_hashes={}
            docs = _write_bundle(pipeline.okf_generator, doc_id, document.filename, paths, proposal,
                written_hashes=written_hashes,write_bundles=settings.okf_write_bundles)
            for item, original in zip(docs, proposal["concepts"]):
                item.metadata["display_order"] = original["display_order"]
            _prepare_quality(session, settings, doc_id, base, plan, generation_id, paths.bundle, completed, proposal)
            identities = _identities(session, doc_id, generation_id, proposal, base.rows.get("chunk_projection_version"))
            dev = session.get(Development, document.development_id) if document.development_id else None
            metadata = {"global_tags": proposal["global_tags"], "source_locale": document.source_locale,
                        "dev_tags": [dev.number, dev.name, *([dev.module] if dev.module else [])] if dev else []}
        restored_ids = set()
        restored_vectors=chain.from_iterable(cache.iter_stage_records(source,"embeddings","vectors") for cache,source in restored_streams)
        from app.services.chunk_index_projection import chunk_fragment_group_id, chunk_fragment_point_id_from_descriptor
        while True:
            batch = []
            with guard():
                records=list(islice(restored_vectors,256))
                if not records:break
                for record in records:
                    payload = deepcopy(record["payload"])
                    if payload.get("doc_id") != doc_id:
                        raise GenerationIntegrityError()
                    payload["generation_id"] = generation_id
                    if payload["point_type"] == "concept":
                        pid = concept_point_id(doc_id, payload["slug"], generation_id=generation_id)
                    elif payload.get("chunk_fragment") is not None:
                        pid = chunk_fragment_point_id_from_descriptor(doc_id, payload["chunk_index"], generation_id, payload.get("source_id"), payload["chunk_fragment"])
                        payload["chunk_fragment_group"] = chunk_fragment_group_id(doc_id, generation_id, payload.get("source_id"), payload["chunk_index"])
                    else:
                        pid = chunk_point_id(doc_id, payload["chunk_index"], generation_id=generation_id)
                    if pid not in identities or pid in restored_ids or any(payload.get(k) != v for k, v in identities[pid].items()):
                        raise GenerationIntegrityError()
                    batch.append(qm.PointStruct(id=pid, vector=record["vector"], payload=payload))
                    restored_ids.add(pid)
                stats = store._upsert_batches(batch, doc_id=doc_id, mode="scope-restore")
                if stats["failed_batches"]:
                    raise GenerationIntegrityError()
        old_by_identity = {_point_identity_key(v): pid for pid, v in old_identities.items()}
        copy_map = {}
        for pid, identity in identities.items():
            if pid not in restored_ids:
                old_pid = old_by_identity.get(_point_identity_key(identity))
                if not old_pid:
                    raise GenerationIntegrityError()
                copy_map[old_pid] = {"id": pid, "identity": identity}
        store.copy_generation_points(doc_id, base.generation_id, generation_id, copy_map, batch_guard=guard)
        store.update_generation_relations(doc_id,generation_id,
            {row["slug"]:row.get("relations") or [] for row in proposal["concepts"]},batch_guard=guard)
        store.reconcile_generation_metadata(doc_id,generation_id,**metadata,
            concept_tags={Path(item.filepath).stem:list(dict.fromkeys([
                *(item.metadata.get("tags") or []),*metadata["global_tags"]])) for item in docs},batch_guard=guard)
        with guard() as session:
            store.verify_generation_projection_points(doc_id, generation_id, identities)
            from app.services.processing_scope_signatures import prepare_scope_signature
            signature,signature_cache=prepare_scope_signature("\n\n".join(c["content"] for c in sorted(
                proposal["chunks"],key=lambda c:c["display_order"])),
                (base.scope or {}).get("signature_cache"),settings)
            data["snapshot"]["signature_cache"]=signature_cache
            session.get(DocumentGeneration,generation_id).processing_scope_json=deepcopy(data["snapshot"])
            effects = {"signature": signature,
                       "signature_scope_hash": data["snapshot"]["selection_hash"]}
            prepared = {"doc_id": doc_id, "generation_id": generation_id, "scope_change_id": plan_id,
                "scope_owner_token": owner_token, "scope_base_hash": data["canonical_digest"],
                "concepts": [d.model_dump(mode="json") for d in docs], "chunks": proposal["chunks"],
                "sources": proposal["sources"], "attachments": proposal["attachments"],
                "processing_scope": data["snapshot"], "preserve_attachment_provenance": True,
                "document_fields": {**proposal["document_fields"], "status": "done", "current_chunk": None,
                                    "error": None, "error_code": None}, "index_metadata": metadata,
                "parse_effects": effects, "point_ids": sorted(identities),
                "chunk_projection_version": base.rows.get("chunk_projection_version"),
                "artifacts": artifact_manifest(settings, doc_id, generation_id,written_hashes=written_hashes)}
            publication = paths.uploads_root / "publication.json"
            write_json_atomic(publication, json.loads(json.dumps(prepared, default=lambda v: v.isoformat())))
            from app.services.generation_store import mark_generation_ready
            mark_generation_ready(session, doc_id, generation_id, publication_hash=file_digest(publication))
            session.get(DocumentScopeChange, plan_id).state = "ready"
        from app.services.generation_publication import publish_prepared_document
        publish_prepared_document(settings, store, doc_id, generation_id)
    finish_update_worker(doc_id, plan_id, owner_token)
    try:
        from app.services.generation_cleanup import cleanup_document_generations
        cleanup_document_generations(settings, store, doc_id)
    except Exception:
        logger.warning("Scope update published; retired cleanup deferred", exc_info=True)
