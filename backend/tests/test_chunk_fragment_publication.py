"""Generic chunk publication binds source, SQL representation and exact points."""
import json
from uuid import uuid4
import pytest
from qdrant_client import models as qm
from sqlalchemy import select, update
from app.db.models import DocumentGeneration, DocumentChunkProjection, DocumentChunkFragment
from app.db.session import session_scope
from app.services.chunk_index_projection import build_chunk_index_projection, chunk_fragment_point_id, chunk_fragment_group_id, digest
from app.services.chunk_projection_store import materialize_chunk_projection
from app.services.generation_store import begin_generation, mark_generation_ready
from app.services.generation_artifacts import artifact_manifest, file_digest, GenerationIntegrityError
from app.services.generation_files import generation_paths
from app.services.generation_publication import publish_prepared_document
from tests.test_generation_pipeline import DOC_ID, _published_snapshot
from tests.test_generation_pipeline import pipeline_env as pipeline_env


def prepare(pipeline, *, long=False):
    body = "physical row 0 False\n" * 230 if long else "short body 0 False"
    with session_scope() as s:
        gen = begin_generation(s, DOC_ID).id
    materialize_chunk_projection(DOC_ID, gen, 0, "title", body)
    projection = build_chunk_index_projection("title", body, version=1)
    parts = list(projection.fragments())
    points = []
    for part in parts:
        payload = {"doc_id": DOC_ID, "generation_id": gen, "point_type": "chunk", "chunk_index": 0, "source_id": None}
        if part.descriptor is not None:
            payload["chunk_fragment"] = part.descriptor
            payload["chunk_fragment_group"] = chunk_fragment_group_id(DOC_ID, gen, None, 0)
        points.append(qm.PointStruct(id=chunk_fragment_point_id(DOC_ID,0,gen,None,part),vector={"": [0.1]*8},payload=payload))
    pipeline.vector_store.client.upsert(pipeline.vector_store.collection, points)
    paths = generation_paths(pipeline.settings, DOC_ID, gen)
    paths.uploads_root.mkdir(parents=True, exist_ok=True)
    paths.bundle.mkdir(parents=True, exist_ok=True)
    prepared = {"doc_id": DOC_ID, "generation_id": gen, "chunk_projection_version": 1,
                "artifacts": artifact_manifest(pipeline.settings,DOC_ID,gen), "concepts": [], "sources": None,
                "chunks": [{"chunk_index":0,"section_title":"title","content":body,"content_hash":digest(body),"char_count":len(body),"source_id":None}],
                "attachments": [],"point_ids":[str(point.id) for point in points],
                "index_metadata":{"global_tags":[],"dev_tags":[],"source_locale":None},"document_fields":{"status":"done"}}
    path=paths.uploads_root/'publication.json'
    path.write_text(json.dumps(prepared),encoding='utf-8')
    with session_scope() as s:
        mark_generation_ready(s,DOC_ID,gen,publication_hash=file_digest(path))
    return gen, prepared, path


@pytest.mark.parametrize("long",[False,True],ids=["short","multipart"])
def test_complete_projection_publishes_atomically(pipeline_env,long):
    pipeline, _, _ = pipeline_env
    gen, prepared, _ = prepare(pipeline,long=long)
    assert publish_prepared_document(pipeline.settings,pipeline.vector_store,DOC_ID,gen)
    assert _published_snapshot(pipeline)[0] == gen
    with session_scope() as s:
        assert s.get(DocumentGeneration,gen).chunk_projection_version == 1
    assert not publish_prepared_document(pipeline.settings,pipeline.vector_store,DOC_ID,gen)


@pytest.mark.parametrize("damage",["missing-marker","unknown-marker","sql-marker","extra-point","point-source","point-index-bool","unexpected-descriptor","canonical-hash","canonical-title","parent-source"],ids=["missing-marker","unknown-marker","sql-marker","extra-point","point-source","point-index-bool","unexpected-descriptor","canonical-hash","canonical-title","parent-source"])
def test_invalid_short_contract_preserves_previous_publication(pipeline_env,damage):
    pipeline, _, _ = pipeline_env
    previous = _published_snapshot(pipeline)
    gen, prepared, path = prepare(pipeline)
    if damage == "missing-marker": prepared.pop("chunk_projection_version")
    elif damage == "unknown-marker": prepared["chunk_projection_version"] = 999
    elif damage == "sql-marker":
        with session_scope() as s: s.get(DocumentGeneration,gen).chunk_projection_version = None
    elif damage == "canonical-hash": prepared['chunks'][0]['content_hash'] = '0'*64
    elif damage == "canonical-title": prepared['chunks'][0]['section_title'] = 'changed'
    elif damage == "parent-source":
        with session_scope() as s: s.get(DocumentChunkProjection,(DOC_ID,gen,0)).source_id = 'other'
    elif damage == "extra-point":
        pipeline.vector_store.client.upsert(pipeline.vector_store.collection,[qm.PointStruct(id=str(uuid4()),vector={"": [0.1]*8},payload={"doc_id":DOC_ID,"generation_id":gen,"point_type":"chunk","chunk_index":99,"source_id":None})])
    else:
        patch = {"point-source":{"source_id":"other"}, "point-index-bool":{"chunk_index":False},"unexpected-descriptor":{"chunk_fragment":{"version":1}}}[damage]
        pipeline.vector_store.client.set_payload(pipeline.vector_store.collection,patch,prepared['point_ids'])
    path.write_text(json.dumps(prepared),encoding='utf-8')
    with session_scope() as s: s.get(DocumentGeneration,gen).publication_hash=file_digest(path)
    with pytest.raises(GenerationIntegrityError):
        publish_prepared_document(pipeline.settings,pipeline.vector_store,DOC_ID,gen)
    assert _published_snapshot(pipeline) == previous
    with session_scope() as s: assert s.get(DocumentGeneration,gen).phase == 'ready'


@pytest.mark.parametrize("damage",["missing-part","extra-part","text","descriptor","descriptor-bool","parent-count"],ids=["missing","extra","text","descriptor","descriptor-bool","count"])
def test_multipart_sql_corruption_preserves_previous_publication(pipeline_env,damage):
    pipeline, _, _ = pipeline_env
    previous = _published_snapshot(pipeline)
    gen, _, _ = prepare(pipeline,long=True)
    with session_scope() as s:
        part=s.scalar(select(DocumentChunkFragment).where(DocumentChunkFragment.generation_id==gen).order_by(DocumentChunkFragment.ordinal))
        if damage == 'missing-part': s.delete(part)
        elif damage == 'extra-part': s.add(DocumentChunkFragment(doc_id=DOC_ID,generation_id=gen,chunk_index=0,ordinal=999,point_id=str(uuid4()),descriptor=part.descriptor,content=part.content,text_hash=part.text_hash))
        elif damage == 'text': part.content += 'changed'
        elif damage == 'descriptor': part.descriptor={**part.descriptor,'end':999}
        elif damage == 'descriptor-bool':
            # ORM dict equality treats True == 1 and can suppress this UPDATE.
            s.execute(update(DocumentChunkFragment).where(DocumentChunkFragment.doc_id==DOC_ID,
                DocumentChunkFragment.generation_id==gen,DocumentChunkFragment.chunk_index==0,
                DocumentChunkFragment.ordinal==part.ordinal).values(descriptor={**part.descriptor,'version':True}))
        else: s.get(DocumentChunkProjection,(DOC_ID,gen,0)).fragment_count += 1
    if damage == 'descriptor-bool':
        with session_scope() as s:
            changed=s.scalar(select(DocumentChunkFragment).where(DocumentChunkFragment.generation_id==gen).order_by(DocumentChunkFragment.ordinal))
            assert changed.descriptor['version'] is True
    with pytest.raises(GenerationIntegrityError):
        publish_prepared_document(pipeline.settings,pipeline.vector_store,DOC_ID,gen)
    assert _published_snapshot(pipeline)==previous


def test_incomplete_projection_cannot_be_marked_ready(pipeline_env):
    pipeline, _, _ = pipeline_env
    previous = _published_snapshot(pipeline)
    with session_scope() as s: gen=begin_generation(s,DOC_ID).id
    materialize_chunk_projection(DOC_ID,gen,0,'title','body'*3000)
    with session_scope() as s: s.get(DocumentChunkProjection,(DOC_ID,gen,0)).complete=False
    with pytest.raises(GenerationIntegrityError),session_scope() as s:
        mark_generation_ready(s,DOC_ID,gen)
    assert _published_snapshot(pipeline)==previous


def test_boolean_descriptor_version_in_qdrant_is_rejected(pipeline_env):
    pipeline, _, _ = pipeline_env
    previous=_published_snapshot(pipeline)
    gen,prepared,_=prepare(pipeline,long=True)
    point=pipeline.vector_store.client.retrieve(pipeline.vector_store.collection,[prepared['point_ids'][0]])[0]
    descriptor={**point.payload['chunk_fragment'],'version':True}
    pipeline.vector_store.client.set_payload(pipeline.vector_store.collection,{'chunk_fragment':descriptor},[point.id])
    with pytest.raises(GenerationIntegrityError):
        publish_prepared_document(pipeline.settings,pipeline.vector_store,DOC_ID,gen)
    assert _published_snapshot(pipeline)==previous


def test_exact_point_verification_reads_every_page(pipeline_env,monkeypatch):
    from app.services import vector_store
    pipeline, _, _ = pipeline_env
    gen, _, _ = prepare(pipeline,long=True)
    monkeypatch.setattr(vector_store,'UPSERT_BATCH_SIZE',2)
    original=pipeline.vector_store.client.scroll
    calls=[]
    def observed(*args,**kwargs):
        calls.append(kwargs.get('offset'))
        return original(*args,**kwargs)
    monkeypatch.setattr(pipeline.vector_store.client,'scroll',observed)
    assert publish_prepared_document(pipeline.settings,pipeline.vector_store,DOC_ID,gen)
    assert len(calls)>1 and calls[0] is None and all(offset is not None for offset in calls[1:])


@pytest.mark.parametrize('group', [None, 'foreign-chunk', ['multiple', 'groups']])
def test_invalid_fragment_group_preserves_previous_publication(pipeline_env, group):
    pipeline, _, _ = pipeline_env
    previous = _published_snapshot(pipeline)
    gen, prepared, _ = prepare(pipeline, long=True)
    pipeline.vector_store.client.set_payload(pipeline.vector_store.collection,
        {'chunk_fragment_group': group}, [prepared['point_ids'][0]])
    with pytest.raises(GenerationIntegrityError):
        publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, gen)
    assert _published_snapshot(pipeline) == previous
