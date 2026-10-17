"""Special format1 routes retain their selected marker across real Pipeline resume."""
import hashlib
import json
import pytest
from openpyxl import Workbook
from sqlalchemy import select
from app.db.models import DocumentChunk, DocumentChunkProjection, DocumentGeneration, DocumentGenerationState
from app.db.session import session_scope
from app.services.errors import VectorStoreError
from app.services.generation_artifacts import GenerationIntegrityError, file_digest
from app.services.generation_publication import publish_prepared_document
from app.services.generation_store import prepare_generation_attempt
from app.services.staging import StagingStore
from tests.test_chunk_fragment_publication import prepare
from tests.test_generation_pipeline import DOC_ID, _published_snapshot
from tests.test_generation_pipeline import pipeline_env as pipeline_env


@pytest.mark.parametrize("route", ["all_short", "table_ref_only"])
def test_special_route_resume_retains_marker_without_reselection(pipeline_env, tmp_path, monkeypatch, route):
    from app.services import pipeline as module
    pipeline, source, write = pipeline_env
    original_prepare = module.prepare_generation_attempt
    resume_observed = []
    def select_once(session, doc_id, *, resume):
        generation = original_prepare(session, doc_id, resume=resume)
        if resume:
            resume_observed.append((generation.id, generation.chunk_projection_version))
            assert generation.chunk_projection_version == 1
        else:
            assert generation.chunk_projection_version is None
            generation.chunk_projection_version = 1
        return generation
    monkeypatch.setattr(module, "prepare_generation_attempt", select_once)
    doc = DOC_ID
    if route == "all_short":
        write("short physical 0 False")
        previous = _published_snapshot(pipeline)
    else:
        source = tmp_path / "only-table.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["Code", "Value"])
        ws.append(["A", 0])
        ws.append(["B", False])
        wb.save(source)
        doc = "marker-table"
        pipeline.registry.create(doc, source.name, "application/octet-stream", source.stat().st_size)
        pipeline.settings.table_structure_enabled = True
        pipeline.settings.okf_table_llm_classify = False
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    method = "index_chunk_projection_fragments" if route == "all_short" else "index_chunks"
    original_index = getattr(pipeline.vector_store, method)
    def fail(*args, **kwargs):
        raise VectorStoreError("synthetic special route index interruption")
    monkeypatch.setattr(pipeline.vector_store, method, fail)
    pipeline._process(doc, source, source.name, [], resume=False)
    assert pipeline.registry.get(doc)["status"] == "paused"
    candidate = StagingStore(doc).load()["generation_id"]
    if route == "all_short":
        assert _published_snapshot(pipeline) == previous
    with session_scope() as s:
        assert s.get(DocumentGeneration, candidate).chunk_projection_version == 1
    monkeypatch.setattr(pipeline.vector_store, method, original_index)
    pipeline._process(doc, source, source.name, [], resume=True)
    assert pipeline.registry.get(doc)["status"] == "done"
    assert resume_observed and all(item == (candidate, 1) for item in resume_observed)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    with session_scope() as s:
        assert s.get(DocumentGenerationState, doc).active_generation_id == candidate
        assert s.get(DocumentGeneration, candidate).chunk_projection_version == 1
        rows = list(s.scalars(select(DocumentChunk).where(DocumentChunk.doc_id == doc)))
        parents = list(s.scalars(select(DocumentChunkProjection).where(DocumentChunkProjection.generation_id == candidate)))
        assert rows
        if route == "all_short":
            assert len(parents) == len(rows) and all(p.complete and p.fragment_count == 0 for p in parents)
        else:
            assert all(row.table_refs for row in rows) and not parents


def test_zero_chunks_ready_resume_preserves_marker_and_publishes_empty_set(pipeline_env):
    pipeline, _, _ = pipeline_env
    gen, prepared, path = prepare(pipeline)
    pipeline.vector_store.client.delete(pipeline.vector_store.collection, prepared["point_ids"])
    prepared["point_ids"] = []
    prepared["chunks"] = []
    path.write_text(json.dumps(prepared), encoding="utf-8")
    with session_scope() as s:
        s.delete(s.get(DocumentChunkProjection, (DOC_ID, gen, 0)))
        s.get(DocumentGeneration, gen).publication_hash = file_digest(path)
    with session_scope() as s:
        resumed = prepare_generation_attempt(s, DOC_ID, resume=True)
        assert resumed.id == gen and resumed.chunk_projection_version == 1 and resumed.phase == "ready"
    assert publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, gen)
    with session_scope() as s:
        assert s.get(DocumentGeneration, gen).chunk_projection_version == 1
        assert not list(s.scalars(select(DocumentChunk).where(DocumentChunk.doc_id == DOC_ID)))


def test_missing_short_parent_never_enables_legacy_fallback(pipeline_env):
    pipeline, _, _ = pipeline_env
    previous = _published_snapshot(pipeline)
    gen, _, _ = prepare(pipeline)
    with session_scope() as s:
        s.delete(s.get(DocumentChunkProjection, (DOC_ID, gen, 0)))
    with pytest.raises(GenerationIntegrityError):
        publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, gen)
    assert _published_snapshot(pipeline) == previous
