"""Explicit candidate format exercises fragments without enabling public rollout."""
import hashlib
import pytest
from sqlalchemy import select
from app.db.models import DocumentGeneration,DocumentChunk,DocumentGenerationState
from app.db.session import session_scope
from app.services.chunk_index_projection import build_chunk_index_projection,chunk_fragment_point_id
from app.services.errors import VectorStoreError
from app.services.sparse import to_sparse_vector
from app.services.staging import StagingStore
from app.services.generation_files import generation_paths
from tests.test_generation_pipeline import DOC_ID,_published_snapshot
from tests.test_generation_pipeline import pipeline_env as pipeline_env


def explicit_format(monkeypatch):
    from app.services import pipeline as module
    original=module.prepare_generation_attempt
    def prepare(session,doc_id,*,resume):
        generation=original(session,doc_id,resume=resume)
        if generation.phase=='preparing':
            generation.chunk_projection_version=1
        return generation
    monkeypatch.setattr(module,'prepare_generation_attempt',prepare)


@pytest.mark.parametrize('structural',[False,True],ids=['legacy','structural'])
def test_pipeline_fragment_inputs_ids_and_sparse_are_complete(pipeline_env,monkeypatch,structural):
    pipeline,source,write=pipeline_env
    explicit_format(monkeypatch)
    pipeline.settings.table_structure_enabled=structural
    pipeline.settings.embedding_batch_size=2
    write('physical row 0 False\n'*600+'unique final source value')
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    batches=[]
    original=pipeline.embedder.embed_texts
    def embed(texts):
        batches.append(list(texts))
        return original(texts)
    monkeypatch.setattr(pipeline.embedder,'embed_texts',embed)
    pipeline._process(DOC_ID,source,'decision.eml',[],resume=False)
    assert pipeline.registry.get(DOC_ID)['status']=='done'
    assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
    with session_scope() as s:
        gen=s.get(DocumentGenerationState,DOC_ID).active_generation_id
        assert s.get(DocumentGeneration,gen).chunk_projection_version==1
        rows=list(s.scalars(select(DocumentChunk).where(DocumentChunk.doc_id==DOC_ID).order_by(DocumentChunk.chunk_index)))
        expected={}
        for row in rows:
            for part in build_chunk_index_projection(row.section_title or '',row.content,version=1).fragments():
                expected[chunk_fragment_point_id(DOC_ID,row.chunk_index,gen,row.source_id,part)]=part
    points,_=pipeline.vector_store.client.scroll(pipeline.vector_store.collection,limit=1000,with_vectors=True)
    actual=[point for point in points if point.payload.get('generation_id')==gen and point.payload.get('point_type')=='chunk']
    assert {str(point.id) for point in actual}==set(expected)
    assert len(actual)>len(rows)
    provider_inputs=[text for batch in batches for text in batch]
    assert max(map(len,batches))<=2
    for point in actual:
        part=expected[str(point.id)]
        assert part.text in provider_inputs
        assert point.payload.get('chunk_fragment')==part.descriptor
        sparse=to_sparse_vector(part.text)
        assert point.vector['sparse'].indices==sparse.indices
        assert point.vector['sparse'].values==pytest.approx(sparse.values)


def test_partial_fragment_upsert_resume_preserves_source_and_previous_active(pipeline_env,monkeypatch):
    pipeline,source,write=pipeline_env
    previous=_published_snapshot(pipeline)
    explicit_format(monkeypatch)
    pipeline.settings.embedding_batch_size=2
    write('physical row 0 False\n'*600)
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    original=getattr(pipeline.vector_store,'index_chunk_projection_fragments',None)
    assert original is not None,'Fragment indexing entry point is missing'
    calls=[]
    def index(*args,**kwargs):
        calls.append(1)
        if len(calls)==2:raise VectorStoreError('synthetic second fragment batch failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(pipeline.vector_store,'index_chunk_projection_fragments',index)
    pipeline._process(DOC_ID,source,'decision.eml',[],resume=False)
    assert len(calls)==2
    assert pipeline.registry.get(DOC_ID)['status']=='paused'
    assert _published_snapshot(pipeline)==previous
    stage=StagingStore(DOC_ID)
    candidate=stage.load()['generation_id']
    checkpoint_hashes={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in stage.dir.glob('chunk_*.md')}
    monkeypatch.setattr(pipeline.vector_store,'index_chunk_projection_fragments',original)
    pipeline._process(DOC_ID,source,'decision.eml',[],resume=True)
    assert pipeline.registry.get(DOC_ID)['status']=='done'
    assert _published_snapshot(pipeline)[0]==candidate
    assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
    bundle_chunks=generation_paths(pipeline.settings,DOC_ID,candidate).bundle/'chunks'
    assert {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in bundle_chunks.glob('chunk_*.md')}==checkpoint_hashes


def test_explicit_generic_format_preserves_structural_table_route(pipeline_env,tmp_path,monkeypatch):
    from openpyxl import Workbook
    from app.db.models import DocumentChunkProjection,DocumentTable
    pipeline,_,_=pipeline_env
    explicit_format(monkeypatch)
    pipeline.settings.table_structure_enabled=True
    pipeline.settings.okf_table_llm_classify=False
    source=tmp_path/'structural-format.xlsx'
    wb=Workbook()
    ws=wb.active
    ws.append(['Code','Value'])
    ws.append(['A',0])
    ws.append(['B',False])
    wb.save(source)
    doc='struct-format1'
    pipeline.registry.create(doc,source.name,'application/octet-stream',source.stat().st_size)
    pipeline._process(doc,source,source.name,[],resume=False)
    assert pipeline.registry.get(doc)['status']=='done'
    with session_scope() as s:
        gen=s.get(DocumentGenerationState,doc).active_generation_id
        assert s.get(DocumentGeneration,gen).chunk_projection_version==1
        rows=list(s.scalars(select(DocumentChunk).where(DocumentChunk.doc_id==doc)))
        structural={row.chunk_index for row in rows if row.table_refs}
        assert structural and s.scalar(select(DocumentTable).where(DocumentTable.generation_id==gen)) is not None
        generic=set(s.scalars(select(DocumentChunkProjection.chunk_index).where(DocumentChunkProjection.generation_id==gen)))
        assert generic.isdisjoint(structural)
    points,_=pipeline.vector_store.client.scroll(pipeline.vector_store.collection,limit=1000)
    chunks=[p for p in points if p.payload.get('generation_id')==gen and p.payload.get('point_type')=='chunk' and p.payload.get('chunk_index') in structural]
    assert len(chunks)==len(structural)
    assert all('chunk_fragment' not in point.payload for point in chunks)
