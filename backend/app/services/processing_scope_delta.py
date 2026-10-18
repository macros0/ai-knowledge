"""Run only missing stages; completed source/chunk results remain reusable."""
from copy import copy, deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from types import SimpleNamespace

from app.db.models import DocumentScopeChange, DocumentGeneration, Document
from app.services.processing_scope_cache import SourceResultCache, embedding_fingerprint, inspect_source_cache
from app.services.processing_scope_order import reserve_chunk_indices
from app.services.update_workers import scope_write_guard
from app.models.schemas import Concept, OkfDocument


def _parse_source(pipeline, doc_id, plan_id, owner_token, work, source, snapshot, generation_id):
    from dataclasses import asdict
    from app.db.models import DocumentSource, DocumentTable, DocumentTableRow
    from app.services.processing_scope_worker import _rows
    from app.services.parser_supervisor import parse_document_supervised
    from app.services.source_chunking import chunk_blocks_by_source
    from app.services.generation_files import generation_paths, processing_review_paths, _reject_links
    settings = pipeline.settings
    cache = SourceResultCache(settings.uploads_dir / doc_id / "scope-stage-cache", doc_id)
    nodes = {n["source_key"]: n for n in snapshot["inventory"]["nodes"]}
    from docparser.attachment_policy import ParserProcessingPolicy
    max_depth = ParserProcessingPolicy(snapshot).traversal.max_depth
    with scope_write_guard(doc_id, plan_id, owner_token) as session:
        operation = session.get(DocumentScopeChange, plan_id)
        row = session.get(DocumentSource, (doc_id, source["source_id"]))
        candidates = []
        if row and row.saved_path:
            candidates.append(settings.uploads_dir / doc_id / row.saved_path)
        saved = source.get("saved_path")
        if saved and Path(saved).name == saved:
            candidates.append(processing_review_paths(settings, doc_id, operation.plan_json["review_id"]) / "attachments" / saved)
        original = next((p for p in candidates if p.is_file()), None)
        if original is None:
            try:
                original = cache.original_path(source)
            except (OSError, ValueError) as exc:
                raise ValueError("Selected source original is unavailable") from exc
        _reject_links(original)
        original = cache.save_original(source, original, Path(source["display_name"]).suffix.lower())
    chain, current = [], source["source_key"]
    while current != "root":
        if current not in nodes or len(chain) >= max_depth:
            raise ValueError("Invalid incremental source ancestry")
        chain.insert(0, nodes[current]["locator"])
        current = nodes[current]["parent_key"]
    parent = source["source_id"].rsplit("/", 1)[0]
    ordinal = [n["source_key"] for n in snapshot["inventory"]["nodes"] if n["parent_key"] == source["parent_key"]].index(source["source_key"])
    policy = deepcopy(snapshot)
    policy["decisions"] = [{**d, "effective_included": d["source_key"] == source["source_key"]} for d in snapshot["decisions"]]
    policy["delta_branch"] = {"source_id": source["source_id"], "source_key": source["source_key"],
        "parent_source_id": parent, "ordinal": ordinal, "kind": source["kind"],
        "locator_chain": chain, "content_fingerprint": source["content_fingerprint"]}
    parsed_folder = generation_paths(settings, doc_id, generation_id).uploads_root / ".delta-parse" / source["source_key"]
    _reject_links(parsed_folder)
    result = parse_document_supervised(str(original), source["display_name"], attachments_dir=parsed_folder,
        timeout_seconds=settings.parser_timeout_seconds, max_memory_mb=settings.parser_max_memory_mb,
        max_concurrent=settings.parser_max_concurrent, mail_enabled=settings.mail_import_enabled,
        table_mode=settings.table_structure_enabled, processing_policy=policy,
        archive_helper_path=settings.archive_7zip_path or None)
    with scope_write_guard(doc_id, plan_id, owner_token):
        pass
    if settings.table_structure_enabled:
        from app.services.table_pipeline import import_parser_spool
        import_parser_spool(doc_id, generation_id, parsed_folder / ".tables-spool")
    chunks = chunk_blocks_by_source(result.blocks, pipeline.okf_generator)
    if any(c["source_id"] != source["source_id"] for c in chunks):
        raise ValueError("Incremental parsing crossed a source boundary")
    with scope_write_guard(doc_id, plan_id, owner_token) as session:
        indices = reserve_chunk_indices(session, doc_id, len(chunks))
        for ordinal, (chunk, index) in enumerate(zip(chunks, indices)):
            chunk.update(chunk_index=index, local_order=ordinal,
                content_hash=hashlib.sha256(chunk["content"].encode()).hexdigest(), char_count=len(chunk["content"]))
        tables = _rows(session, DocumentTable, doc_id, generation_id, source_id=source["source_id"])
        table_ids = {t["table_id"] for t in tables}
        table_rows = [r for r in _rows(session, DocumentTableRow, doc_id, generation_id) if r["table_id"] in table_ids]
    parsed_source = asdict(next(n for n in result.sources if n.source_id == source["source_id"]))
    parsed_source.update(content_fingerprint=source["content_fingerprint"], container_locator=source["locator"],
                         artifact_kind="original", extraction_status="parsed", parser_version=result.parser_version)
    return {"chunks": chunks, "tables": tables, "table_rows": table_rows, "source": parsed_source,
            "tables_projected": not tables, "warnings": result.warnings}


def run_scope_delta(doc_id, plan_id, workset, owner_token):
    from app.services import gen_quality
    from app.services.table_quality import bind_table_reports
    from app.services.pipeline import get_pipeline
    from app.services.processing_scope_worker import _iter_points, _identities, _rows
    from app.services.canonical_repair import _index_candidate
    from app.services.okf_generator import _slugify
    from app.db.models import DocumentChunkProjection, DocumentChunkFragment
    pipeline = get_pipeline()
    settings, store = pipeline.settings, pipeline.vector_store
    guard = lambda: scope_write_guard(doc_id, plan_id, owner_token)
    with guard() as session:
        operation = session.get(DocumentScopeChange, plan_id)
        snapshot = deepcopy(operation.plan_json["snapshot"])
        generation_id = operation.candidate_generation_id
        version = session.get(DocumentGeneration, generation_id).chunk_projection_version
        document = session.get(Document, doc_id)
        filename = document.filename
        metadata = {"global_tags": [t.tag_rel.canonical_text for t in document.tags_rel],
                    "dev_tags": [], "source_locale": document.source_locale}
        operation.state = "processing"
        document.status = "processing"
    nodes = {n["source_key"]: n for n in snapshot["inventory"]["nodes"]}
    cache = SourceResultCache(settings.uploads_dir / doc_id / "scope-stage-cache", doc_id)
    completed = {}
    for work in workset["sources"]:
        gen_quality.drain()
        key = work["source_key"]
        source = {**nodes[key], "source_id": work["source_id"], "embedding_fingerprint": embedding_fingerprint(settings)}
        descriptor = inspect_source_cache(doc_id, source)
        if descriptor.get("verified"):
            original_cache = SourceResultCache(descriptor["location"], doc_id)
            if original_cache.root != cache.root:
                with guard():
                    original_cache.copy_to(source, cache)
            parse = cache.read_stage(source, "parse")
        else:
            parse = _parse_source(pipeline, doc_id, plan_id, owner_token, work, source, snapshot, generation_id)
            with guard():
                cache.save_stage(source, "parse", parse)
        for chunk in parse["chunks"]:
            chunk.setdefault("section_title", "")
            actual_hash = hashlib.sha256(chunk["content"].encode()).hexdigest()
            if chunk.get("content_hash") and chunk["content_hash"] != actual_hash:
                raise ValueError("Cached canonical chunk hash changed")
            chunk.setdefault("content_hash", actual_hash)
            chunk.setdefault("char_count", len(chunk["content"]))
        if parse.get("tables"):
            from app.db.models import DocumentTable, DocumentTableRow
            from app.services.table_pipeline import build_table_source_chunks
            with guard() as session:
                for model, records in ((DocumentTable, parse["tables"]), (DocumentTableRow, parse.get("table_rows", []))):
                    for row in records:
                        values = {**row, "generation_id": generation_id}
                        primary = tuple(values[c.key] for c in model.__table__.primary_key.columns)
                        if session.get(model, primary) is None:
                            session.add(model(**values))
                session.flush()
        if parse.get("tables") and not parse.get("tables_projected", any(c.get("table_refs") for c in parse["chunks"])):
            structural = build_table_source_chunks(doc_id, generation_id, settings, pipeline.okf_generator, source_ids={source["source_id"]})
            with guard() as session:
                indices = reserve_chunk_indices(session, doc_id, len(structural))
                for index, chunk in zip(indices, structural):
                    chunk.update(chunk_index=index, local_order=len(parse["chunks"]),
                        content_hash=hashlib.sha256(chunk["content"].encode()).hexdigest(), char_count=len(chunk["content"]),
                        section_title=chunk.get("table_title") or "")
                    parse["chunks"].append(chunk)
                parse["tables"] = _rows(session, DocumentTable, doc_id, generation_id, source_id=source["source_id"])
                parse["tables_projected"] = True
                cache.save_stage(source, "parse", parse)
        hit = cache.inspect(source)
        if not hit["stages"]["concepts"]:
            try:
                partial = cache.read_stage(source, "concepts", allow_partial=True)
            except (OSError, ValueError):
                partial = {"concepts": [], "completed_chunks": []}
            concepts = deepcopy(partial.get("concepts", []))
            done = set(partial.get("completed_chunks", []))
            diagnostics = deepcopy(partial.get("chunks_data", {}))
            total = max((c["chunk_index"] for c in parse["chunks"]), default=0) + 1
            for chunk in parse["chunks"]:
                index = chunk["chunk_index"]
                if index in done:
                    continue
                # A salvaged chunk is retryable, never a completed cache hit.
                # Replace its partial output rather than appending duplicates.
                concepts = [c for c in concepts if c.get("chunk_index") != index]
                gen_quality.drain()
                with guard() as session:
                    session.get(Document, doc_id).current_chunk = len(done) + 1
                if chunk.get("table_refs"):
                    results = [Concept(id=chunk.get("fragment_id", "table"), title=chunk.get("table_title", "Table row"),
                                       type=chunk.get("concept_type", "reference"), content=chunk["content"], table_refs=chunk["table_refs"])]
                else:
                    results = pipeline.okf_generator.generate_chunk(chunk["content"], source["display_name"], index + 1, total,
                        doc_id=doc_id, source_is_mail=source.get("kind") in {"eml", "msg", "mail"})
                degradation = bind_table_reports(gen_quality.drain(), source["source_id"], index)
                diagnostics[str(index)] = {"degradation": degradation}
                provenance = {"generated_at": datetime.now(timezone.utc).isoformat(), "model_id": settings.llm_model,
                              "prompt_version": pipeline.okf_generator.prompt_version()}
                for ordinal, concept in enumerate(results):
                    row = concept.model_dump(mode="json")
                    slug = f"{_slugify(concept.title)[:48]}-s{key[:10]}-{index}-{ordinal}"
                    row.pop("id", None)
                    concepts.append({**row, **provenance, "slug": slug, "source_id": source["source_id"],
                                     "chunk_index": index, "local_order": len(concepts)})
                if not gen_quality.has_salvage(degradation):
                    done.add(index)
                with guard():
                    cache.save_stage(source, "concepts", {"concepts": concepts, "completed_chunks": sorted(done),
                                                         "chunks_data": diagnostics,
                                                         "stage_complete": len(done) == len(parse["chunks"])})
            if not parse["chunks"]:
                with guard():
                    cache.save_stage(source, "concepts", {"concepts": [], "completed_chunks": [], "stage_complete": True})
        concept_stage = cache.read_stage(source, "concepts")
        concepts = concept_stage["concepts"]
        if not cache.inspect(source)["stages"]["embeddings"]:
            if version is not None:
                from app.services.chunk_projection_store import materialize_chunk_projection
                for chunk in parse["chunks"]:
                    if not chunk.get("table_refs"):
                        with guard():
                            pass
                        materialize_chunk_projection(doc_id, generation_id, chunk["chunk_index"], chunk.get("section_title") or "",
                            chunk["content"], source_id=source["source_id"])
            docs = [OkfDocument(filepath=c["slug"] + ".md", metadata=c, content=c["content"], markdown=c["content"]) for c in concepts]
            checked_store = copy(store)
            def upsert(*args, **kwargs):
                with guard():
                    return store._upsert_batches(*args, **kwargs)
            checked_store._upsert_batches = upsert
            def embed(texts):
                with guard():
                    pass
                vectors = pipeline.embedder.embed_texts(texts)
                with guard():
                    pass
                return vectors
            ids = _index_candidate(settings, checked_store, SimpleNamespace(embed_texts=embed), doc_id, filename,
                generation_id, docs, parse["chunks"], metadata, sources=[parse.get("source") or {"source_id": source["source_id"],
                    "parent_source_id": "root", "kind": "document", "metadata": {}}], chunk_projection_version=version)
            with guard() as session:
                identities = _identities(session, doc_id, generation_id, {"chunks": parse["chunks"], "concepts": concepts}, version)
                if ids != set(identities):
                    raise ValueError("Incremental index is incomplete")
                parse["projections"] = _rows(session, DocumentChunkProjection, doc_id, generation_id, source_id=source["source_id"])
                indices = {c["chunk_index"] for c in parse["chunks"]}
                parse["fragments"] = [r for r in _rows(session, DocumentChunkFragment, doc_id, generation_id) if r["chunk_index"] in indices]
                # Projection SQL and its vectors complete together. Keeping them
                # in this stage preserves already completed concepts if indexing
                # fails or a later attempt needs a different embedding space.
                cache.save_stage(source,"embeddings",{"projections":parse["projections"],"fragments":parse["fragments"],
                    "embedding_fingerprint":source["embedding_fingerprint"]},record_streams={
                    "vectors":_iter_points(store,doc_id,generation_id,identities)})
        completed[key] = {**parse, **concept_stage}
    return completed


def resume_scope_delta(doc_id, plan_id, owner_token):
    with scope_write_guard(doc_id, plan_id, owner_token) as session:
        workset = deepcopy(session.get(DocumentScopeChange, plan_id).plan_json["preview"]["workset"])
    return run_scope_delta(doc_id, plan_id, workset, owner_token)
