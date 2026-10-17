"""Published generic chunks must keep their contract during offline maintenance."""
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from app.db.models import DocumentGeneration, DocumentChunkFragment
from app.db.session import session_scope
from app.services.generation_publication import publish_prepared_document
from app.services.vector_store import chunk_point_id, SPARSE_VECTOR_NAME
from app.services.sparse import to_sparse_vector
from app.services.generation_artifacts import GenerationIntegrityError
from tests.test_generation_pipeline import DOC_ID
from tests.test_generation_pipeline import pipeline_env as pipeline_env
from tests.test_chunk_fragment_publication import prepare


@pytest.fixture
def published_parts(pipeline_env):
    pipeline, _, _ = pipeline_env
    gen, prepared, _ = prepare(pipeline, long=True)
    # This synthetic publication has no source artifacts. Do not inherit the
    # previous fixture's attachment paths after clearing its attachment rows.
    from app.services.generation_files import generation_paths
    from app.services.generation_artifacts import file_digest
    import json
    prepared['document_fields'].update(okf_concept_count=0,total_chunks=1)
    prepared['sources'] = [{'source_id':'root','parent_source_id':None,'kind':'document','display_name':'Synthetic','ordinal':0}]
    path = generation_paths(pipeline.settings, DOC_ID, gen).uploads_root / 'publication.json'
    path.write_text(json.dumps(prepared), encoding='utf-8')
    with session_scope() as s: s.get(DocumentGeneration, gen).publication_hash = file_digest(path)
    assert publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, gen)
    return pipeline, gen, prepared


def own_points(store, gen):
    from qdrant_client import models as qm
    points, offset = store.client.scroll(store.collection,
        scroll_filter=qm.Filter(must=[qm.FieldCondition(key='generation_id', match=qm.MatchValue(value=gen))]),
        limit=256, with_vectors=True)
    assert offset is None
    return {str(p.id): p for p in points}


def test_restart_backfill_never_creates_legacy_root_or_embeds_complete_parts(published_parts):
    pipeline, gen, prepared = published_parts
    def forbidden(_): raise AssertionError('Complete publication needs no embedding')
    assert pipeline.vector_store.backfill_chunks(SimpleNamespace(embed_texts=forbidden), batch_size=2) == 0
    assert set(own_points(pipeline.vector_store, gen)) == set(prepared['point_ids'])
    assert chunk_point_id(DOC_ID, 0, generation_id=gen) not in own_points(pipeline.vector_store, gen)


def test_missing_part_is_restored_in_same_generation_with_bounded_embedding(published_parts):
    pipeline, gen, prepared = published_parts
    store = pipeline.vector_store
    missing = prepared['point_ids'][-1]
    before = own_points(store, gen)
    store.client.delete(store.collection, [missing])
    calls = []
    def embed(texts):
        calls.append(texts)
        assert len(texts) <= 2
        return [[0.1]*8 for _ in texts]
    assert store.backfill_chunks(SimpleNamespace(embed_texts=embed), batch_size=2) == 1
    after = own_points(store, gen)
    assert set(after) == set(before)
    assert all(after[missing].payload.get(k) == v for k, v in before[missing].payload.items())
    assert len(calls) == 1 and len(calls[0]) == 1
    with session_scope() as s:
        assert s.get(DocumentGeneration, gen).chunk_projection_version == 1


def test_sparse_rebuild_uses_complete_materialized_representation(published_parts):
    pipeline, gen, prepared = published_parts
    assert pipeline.vector_store.backfill_sparse(force=True, include_chunks=True, batch_size=2) == len(prepared['point_ids'])
    points = own_points(pipeline.vector_store, gen)
    with session_scope() as s:
        for part in s.scalars(select(DocumentChunkFragment).where(DocumentChunkFragment.generation_id == gen)):
            assert points[part.point_id].vector[SPARSE_VECTOR_NAME] == to_sparse_vector(part.content)


def test_backfill_restores_grouping_metadata_without_reembedding(published_parts):
    pipeline, gen, prepared = published_parts
    store = pipeline.vector_store
    point = prepared['point_ids'][0]
    before = own_points(store, gen)[point].payload
    store.client.delete_payload(store.collection, ['chunk_fragment_group'], [point])
    def forbidden(_): raise AssertionError('Metadata repair needs no embedding')
    assert store.backfill_chunks(SimpleNamespace(embed_texts=forbidden), batch_size=2) == 0
    assert own_points(store, gen)[point].payload == before


def test_unknown_active_format_is_rejected_instead_of_creating_root(published_parts):
    pipeline, gen, _ = published_parts
    with session_scope() as s: s.get(DocumentGeneration, gen).chunk_projection_version = 999
    with pytest.raises(GenerationIntegrityError):
        pipeline.vector_store.backfill_chunks(SimpleNamespace(embed_texts=lambda texts: [[.1]*8 for _ in texts]))


def test_canonical_repair_preserves_format_in_new_candidate(published_parts):
    from app.services.canonical_repair import repair_published_document
    pipeline, gen, _ = published_parts
    def edit(snapshot): snapshot['chunks'][0]['content'] += '\nRepaired.'
    result = repair_published_document(DOC_ID, pipeline.settings, pipeline.okf_generator,
        pipeline.embedder, pipeline.vector_store, edit)
    assert result['generation_id'] != gen
    with session_scope() as s:
        new = s.get(DocumentGeneration, result['generation_id'])
        assert new.chunk_projection_version == 1
        assert s.scalar(select(DocumentChunkFragment.point_id).where(DocumentChunkFragment.generation_id == new.id))
    assert chunk_point_id(DOC_ID, 0, generation_id=new.id) not in own_points(pipeline.vector_store, new.id)


@pytest.mark.parametrize('where', ['sql', 'qdrant'])
def test_maintenance_rejects_boolean_descriptor_version(published_parts, where):
    pipeline, gen, prepared = published_parts
    identity = prepared['point_ids'][0]
    if where == 'sql':
        with session_scope() as s:
            part = s.scalar(select(DocumentChunkFragment).where(DocumentChunkFragment.point_id == identity))
            from sqlalchemy import update
            s.execute(update(DocumentChunkFragment).where(DocumentChunkFragment.point_id==identity).values(descriptor={**part.descriptor,'version':True}))
        with session_scope() as s:
            assert s.scalar(select(DocumentChunkFragment.descriptor).where(DocumentChunkFragment.point_id==identity))['version'] is True
    else:
        payload = own_points(pipeline.vector_store, gen)[identity].payload
        pipeline.vector_store.client.set_payload(pipeline.vector_store.collection,
            {'chunk_fragment':{**payload['chunk_fragment'],'version':True}}, [identity])
    with pytest.raises(GenerationIntegrityError):
        pipeline.vector_store.backfill_chunks(SimpleNamespace(embed_texts=lambda texts: [[.1]*8 for _ in texts]), batch_size=2)


def test_group_metadata_repair_writes_bounded_batches(published_parts, monkeypatch):
    pipeline, gen, prepared = published_parts
    store = pipeline.vector_store
    store.client.delete_payload(store.collection, ['chunk_fragment_group'], prepared['point_ids'])
    calls = []
    original = store.client.set_payload
    def observed(*args, **kwargs):
        calls.append(kwargs['points'])
        return original(*args, **kwargs)
    monkeypatch.setattr(store.client, 'set_payload', observed)
    def forbidden(_): raise AssertionError('No dense rewrite for metadata repair')
    assert store.backfill_chunks(SimpleNamespace(embed_texts=forbidden), batch_size=2) == 0
    assert len(calls) == 2 and all(len(ids) == 2 for ids in calls)


def test_full_reindex_restores_exact_fragment_points(published_parts):
    from app.services.canonical_reindex import reindex_published_document
    pipeline, gen, prepared = published_parts
    store = pipeline.vector_store
    store.delete_generation_points(DOC_ID, gen)
    calls = []
    def embed(texts):
        calls.append(texts)
        assert len(texts) <= 64
        return [[.1]*8 for _ in texts]
    counts = reindex_published_document(DOC_ID, pipeline.settings, store, SimpleNamespace(embed_texts=embed))
    assert counts['chunks'] == 1
    assert set(own_points(store, gen)) == set(prepared['point_ids'])
    assert sum(map(len,calls)) == len(prepared['point_ids'])


def test_mail_backfill_accepts_parts_and_reverse_audit_counts_all_parts(published_parts):
    from scripts.backfill_mail_scope import run_backfill
    pipeline, gen, _ = published_parts
    report = run_backfill(pipeline.vector_store, pipeline.settings, apply=False, batch_size=2, doc_id=DOC_ID)
    assert report['invalid_identity'] == 0
    assert report['missing_active_points'] == 0


def test_export_preserves_version_full_title_and_reconstructable_representations(published_parts, tmp_path):
    import json
    from app.services.export_okf import export_okf_bundle
    from app.services.chunk_index_projection import build_chunk_index_projection
    pipeline, gen, _ = published_parts
    dest = tmp_path / 'portable'
    files = export_okf_bundle(DOC_ID, dest)
    assert 'chunks/projection.json' in files
    marker = json.loads((dest / 'chunks/projection.json').read_text(encoding='utf-8'))
    assert marker['chunk_projection_version'] == 1
    rows = json.loads((dest / 'chunks/manifest.json').read_text(encoding='utf-8'))
    assert rows[0]['section_title'] == 'title'
    raw = (dest / 'chunks/chunk_00.md').read_text(encoding='utf-8')
    projected = build_chunk_index_projection(rows[0]['section_title'], raw, version=marker['chunk_projection_version'])
    with session_scope() as s:
        texts = list(s.scalars(select(DocumentChunkFragment.content).where(DocumentChunkFragment.generation_id==gen).order_by(DocumentChunkFragment.ordinal)))
    assert [part.text for part in projected.fragments()] == texts
    assert marker['chunks'][0]['projection_hash'] == projected.projection_hash


def test_export_rejects_unknown_format_before_writing_files(published_parts, tmp_path):
    from app.services.export_okf import export_okf_bundle
    pipeline, gen, _ = published_parts
    with session_scope() as s: s.get(DocumentGeneration, gen).chunk_projection_version=999
    dest=tmp_path/'unsupported'
    with pytest.raises(GenerationIntegrityError): export_okf_bundle(DOC_ID,dest)
    assert not dest.exists()


def test_integrity_counts_parts_separately_from_canonical_chunks(published_parts):
    from scripts.check_integrity import check_doc
    pipeline, gen, prepared = published_parts
    result=check_doc(DOC_ID,1,0,pipeline.settings,pipeline.vector_store)
    assert result['ok'], result['issues']
    assert result['db_chunks']==1 and result['qdrant_points']==len(prepared['point_ids'])
    pipeline.vector_store.client.delete(pipeline.vector_store.collection,[prepared['point_ids'][0]])
    result=check_doc(DOC_ID,1,0,pipeline.settings,pipeline.vector_store)
    assert not result['ok'] and not result['qdrant_unavailable']


def test_sqlite_backup_restore_rebuilds_same_generation_contract(published_parts,tmp_path):
    import sqlite3
    from app.db.session import get_engine,configure_for_tests
    pipeline,gen,prepared=published_parts
    original=get_engine()
    from pathlib import Path
    assert original.url.drivername=='sqlite' and Path(original.url.database).resolve().parent==tmp_path.resolve()
    restored=tmp_path/'restored.db'
    with original.raw_connection() as connection:
        with sqlite3.connect(restored) as destination:
            connection.driver_connection.backup(destination)
    configure_for_tests('sqlite:///'+restored.as_posix())
    pipeline.vector_store.delete_generation_points(DOC_ID,gen)
    count=pipeline.vector_store.backfill_chunks(SimpleNamespace(embed_texts=lambda texts:[[.1]*8 for _ in texts]),batch_size=2)
    assert count==len(prepared['point_ids'])
    assert set(own_points(pipeline.vector_store,gen))==set(prepared['point_ids'])
    with session_scope() as s: assert s.get(DocumentGeneration,gen).chunk_projection_version==1


def test_mail_audit_rejects_inconsistent_parent_instead_of_legacy_fallback(published_parts):
    from app.db.models import DocumentChunkProjection
    from scripts.backfill_mail_scope import run_backfill
    pipeline,gen,_=published_parts
    with session_scope() as s: s.get(DocumentChunkProjection,(DOC_ID,gen,0)).fragment_count=0
    report=run_backfill(pipeline.vector_store,pipeline.settings,apply=True,batch_size=2,doc_id=DOC_ID)
    assert report['failed_batches']>0 and report['updated']==0


def test_full_reindex_rewrites_existing_structural_root(pipeline_env,tmp_path,monkeypatch):
    from tests.test_chunk_fragment_pipeline import test_explicit_generic_format_preserves_structural_table_route
    from app.services.canonical_reindex import reindex_published_document
    test_explicit_generic_format_preserves_structural_table_route(pipeline_env,tmp_path,monkeypatch)
    pipeline,_,_=pipeline_env
    calls=[];original=pipeline.vector_store.index_chunks
    def observed(*args,**kwargs):
        calls.append(kwargs['chunk_indices'])
        return original(*args,**kwargs)
    monkeypatch.setattr(pipeline.vector_store,'index_chunks',observed)
    reindex_published_document('struct-format1',pipeline.settings,pipeline.vector_store,
        SimpleNamespace(embed_texts=lambda texts:[[.9]+[0.]*7 for _ in texts]))
    assert calls, 'Full reindex must replace an existing structural root dense vector'


def test_full_reindex_bounds_concept_embedding_batches(published_parts):
    from app.db.models import OkfConcept
    from app.services.canonical_reindex import reindex_published_document
    pipeline,gen,_=published_parts
    with session_scope() as s:
        s.add_all([OkfConcept(doc_id=DOC_ID,slug=f'many-{i}',title=f'Concept {i}',
            content='Physical rule 0 False.',source_id='root',chunk_index=0) for i in range(130)])
    calls=[]
    def embed(texts):
        calls.append(len(texts))
        assert len(texts)<=64, 'Do not accumulate all concept vectors during offline reindex'
        return [[.1]*8 for _ in texts]
    counts=reindex_published_document(DOC_ID,pipeline.settings,pipeline.vector_store,SimpleNamespace(embed_texts=embed))
    assert counts['concepts']==130 and sum(calls)>=130


@pytest.mark.parametrize('field,value', [
    ('source_id', 'foreign'), ('point_type', 'concept'), ('chunk_index', False),
    ('chunk_index', 99), ('chunk_fragment', {'version': 1}),
])
def test_structural_backfill_rejects_existing_foreign_identity(pipeline_env, tmp_path, monkeypatch, field, value):
    from tests.test_chunk_fragment_pipeline import test_explicit_generic_format_preserves_structural_table_route
    from app.db.models import DocumentGenerationState
    test_explicit_generic_format_preserves_structural_table_route(pipeline_env, tmp_path, monkeypatch)
    pipeline, _, _ = pipeline_env
    store = pipeline.vector_store
    with session_scope() as session:
        generation = session.get(DocumentGenerationState, 'struct-format1').active_generation_id
    records, _ = store.client.scroll(store.collection, limit=100)
    point = next(row for row in records if row.payload.get('generation_id') == generation
                 and row.payload.get('point_type') == 'chunk')
    store.client.set_payload(store.collection, {field: value}, [point.id])
    calls = []
    def embed(texts):
        calls.append(texts)
        return [[.1] * 8 for _ in texts]
    with pytest.raises(GenerationIntegrityError):
        store.backfill_chunks(SimpleNamespace(embed_texts=embed), batch_size=2)
    assert not calls
