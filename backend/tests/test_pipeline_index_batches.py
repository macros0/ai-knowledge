"""Bound indexing memory while preserving the complete publication and resume."""
from openpyxl import Workbook

from app.db.models import DocumentChunk, DocumentGenerationState, OkfConcept
from app.db.session import session_scope
from app.services.errors import VectorStoreError
from app.services.staging import StagingStore
from tests.test_generation_pipeline import pipeline_env as _pipeline_env

pipeline_env = _pipeline_env


def workbook(path, prefix, rows=150):
    wb = Workbook()
    ws = wb.active
    ws.append(['Code', 'Quantity'])
    for index in range(rows):
        ws.append([f'{prefix}{index:03d}', index * 10])
    wb.save(path)


def test_indexing_batches_preserve_every_input_and_global_chunk_index(pipeline_env, tmp_path, monkeypatch):
    pipeline, _, _ = pipeline_env
    pipeline.settings.okf_table_llm_classify = False
    pipeline.settings.embedding_batch_size = 64
    source = tmp_path / 'batch.xlsx'
    workbook(source, 'K')
    doc_id = 'batch-memory-001'
    pipeline.registry.create(doc_id, source.name, 'application/octet-stream', source.stat().st_size)
    requests = []
    original_embed = pipeline.embedder.embed_texts

    def embed(texts):
        requests.append(list(texts))
        return original_embed(texts)

    monkeypatch.setattr(pipeline.embedder, 'embed_texts', embed)
    pipeline._process(doc_id, source, source.name, [], resume=False)
    assert max(map(len, requests)) <= 64, 'All document vectors were accumulated in one request'
    assert pipeline.registry.get(doc_id)['status'] == 'done'
    with session_scope() as session:
        active = session.get(DocumentGenerationState, doc_id).active_generation_id
        concepts = session.query(OkfConcept).filter_by(doc_id=doc_id).order_by(OkfConcept.chunk_index).all()
        chunks = session.query(DocumentChunk).filter_by(doc_id=doc_id).order_by(DocumentChunk.chunk_index).all()
        assert len(concepts) == len(chunks) == 151
        expected = [f'{item.title}\n{item.content[:pipeline.settings.okf_max_concept_chars]}' for item in concepts]
        for item in chunks:
            title = item.section_title or ''
            text = item.content[:pipeline.settings.okf_max_chunk_index_chars]
            expected.append(f'{title}\n{text}' if title else text)
        assert [text for batch in requests for text in batch] == expected
    points, _ = pipeline.vector_store.client.scroll(pipeline.settings.qdrant_collection, limit=1000, with_vectors=False)
    current = [p.payload for p in points if p.payload['generation_id'] == active]
    assert len(current) == 302
    assert sorted(p['chunk_index'] for p in current if p['point_type'] == 'chunk') == list(range(151))
    assert {p['source_id'] for p in current} == {'root'}


def test_later_chunk_batch_failure_keeps_previous_generation_and_resume_is_complete(pipeline_env, tmp_path, monkeypatch):
    pipeline, _, _ = pipeline_env
    pipeline.settings.okf_table_llm_classify = False
    pipeline.settings.embedding_batch_size = 64
    source = tmp_path / 'resume-batch.xlsx'
    workbook(source, 'OLD')
    doc_id = 'batch-resume-001'
    pipeline.registry.create(doc_id, source.name, 'application/octet-stream', source.stat().st_size)
    pipeline._process(doc_id, source, source.name, [], resume=False)
    with session_scope() as session:
        previous = session.get(DocumentGenerationState, doc_id).active_generation_id
    old_points, _ = pipeline.vector_store.client.scroll(pipeline.settings.qdrant_collection, limit=1000, with_vectors=False)
    previous_ids = {p.id for p in old_points if p.payload['generation_id'] == previous}
    assert len(previous_ids) == 302
    workbook(source, 'NEW')
    original_index = pipeline.vector_store.index_chunks
    calls = []

    def index(*args, **kwargs):
        calls.append(kwargs.get('chunk_indices'))
        if len(calls) == 2:
            raise VectorStoreError('injected late batch failure')
        return original_index(*args, **kwargs)

    monkeypatch.setattr(pipeline.vector_store, 'index_chunks', index)
    pipeline._process(doc_id, source, source.name, [], resume=False)
    assert len(calls) == 2, 'A later batch was never attempted'
    assert calls[0] == list(range(64)) and calls[1] == list(range(64, 128))
    assert pipeline.registry.get(doc_id)['status'] == 'paused'
    with session_scope() as session:
        assert session.get(DocumentGenerationState, doc_id).active_generation_id == previous
    assert len(pipeline.vector_store.client.retrieve(pipeline.settings.qdrant_collection, list(previous_ids))) == 302
    candidate = StagingStore(doc_id).load()['generation_id']
    monkeypatch.setattr(pipeline.vector_store, 'index_chunks', original_index)
    pipeline._process(doc_id, source, source.name, [], resume=True)
    assert pipeline.registry.get(doc_id)['status'] == 'done'
    with session_scope() as session:
        assert session.get(DocumentGenerationState, doc_id).active_generation_id == candidate
        assert session.query(DocumentChunk).filter_by(doc_id=doc_id).count() == 151
    points, _ = pipeline.vector_store.client.scroll(pipeline.settings.qdrant_collection, limit=1000, with_vectors=False)
    current = [p.payload for p in points if p.payload['generation_id'] == candidate]
    assert len(current) == 302
    assert sorted(p['chunk_index'] for p in current if p['point_type'] == 'chunk') == list(range(151))


def test_staging_generation_id_does_not_load_all_checkpoint_metadata(tmp_path, monkeypatch):
    stage = StagingStore('batch-pointer-1', staging_root=tmp_path / 'staging')
    stage.create(1, generation_id='g' * 32)

    def forbidden():
        raise AssertionError('Generation lookup hydrated the complete checkpoint map')

    monkeypatch.setattr(stage, '_load_raw', forbidden)
    assert stage.generation_id == 'g' * 32
