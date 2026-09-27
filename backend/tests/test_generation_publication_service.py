"""Prepared publication is shared by pipeline and canonical maintenance."""
import pytest

from app.db.models import DocumentGeneration, DocumentGenerationState
from app.db.session import session_scope
from app.services.staging import StagingStore
from tests.test_generation_cleanup import _prepare_second
from tests.test_generation_pipeline import DOC_ID, _published_snapshot
from tests.test_generation_pipeline import pipeline_env as pipeline_env


def test_publication_service_is_idempotent_and_does_not_remove_pipeline_checkpoint(pipeline_env, monkeypatch):
    from app.services.generation_publication import publish_prepared_document

    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    before = _published_snapshot(pipeline)
    staging_before = StagingStore(DOC_ID).load()
    assert publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)
    assert _published_snapshot(pipeline)[0] == candidate != before[0]
    assert StagingStore(DOC_ID).load() == staging_before
    assert not publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)


def test_publication_service_rolls_back_all_rows_on_canonical_write_failure(pipeline_env, monkeypatch):
    from app.services import generation_publication as publication

    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    before = _published_snapshot(pipeline)

    def fail(*_args, **_kwargs):
        raise RuntimeError("canonical write failed")

    monkeypatch.setattr(publication, "replace_chunks", fail)
    with pytest.raises(RuntimeError, match="canonical write failed"):
        publication.publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)
    assert _published_snapshot(pipeline) == before
    with session_scope() as session:
        assert session.get(DocumentGenerationState, DOC_ID).candidate_generation_id == candidate
        assert session.get(DocumentGeneration, candidate).phase == "ready"


def _rewrite_manifest(pipeline, candidate, change):
    import json
    from app.services.generation_files import generation_paths
    from app.services.generation_artifacts import file_digest
    path = generation_paths(pipeline.settings, DOC_ID, candidate).uploads_root / 'publication.json'
    prepared = json.loads(path.read_text(encoding='utf-8'))
    change(prepared)
    path.write_text(json.dumps(prepared), encoding='utf-8')
    with session_scope() as session:
        session.get(DocumentGeneration, candidate).publication_hash = file_digest(path)
    return prepared


@pytest.mark.parametrize('sources_mode', ['candidate_document', 'preserved_mail'])
def test_old_ready_candidate_scope_is_patched_from_manifest_before_visibility(pipeline_env, monkeypatch, sources_mode):
    from app.services.generation_publication import publish_prepared_document
    from app.services.mail_scope import build_record_mail_scopes
    from app.services.source_store import fetch_source_trees
    from app.services.vector_store import concept_point_id, chunk_point_id
    from pathlib import Path
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    def change(prepared):
        if sources_mode == 'preserved_mail':
            prepared['sources'] = None
        else:
            for row in prepared['sources']:
                row['kind'] = 'document'
                row['metadata'] = {}
    prepared = _rewrite_manifest(pipeline, candidate, change)
    pipeline.vector_store.client.delete_payload(pipeline.vector_store.collection,
        keys=['mail_scope', 'mail_scope_version'], points=prepared['point_ids'])
    with session_scope() as session:
        sources = prepared['sources'] if prepared['sources'] is not None else fetch_source_trees(session, {DOC_ID})[DOC_ID]
    cscopes, hscopes = build_record_mail_scopes(sources,
        [item['metadata'] for item in prepared['concepts']], prepared['chunks'])
    expected = {concept_point_id(DOC_ID, Path(item['filepath']).stem, generation_id=candidate): scope
                for item, scope in zip(prepared['concepts'], cscopes)}
    expected.update({chunk_point_id(DOC_ID, row['chunk_index'], generation_id=candidate): scope
                     for row, scope in zip(prepared['chunks'], hscopes)})
    assert publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)
    rows = pipeline.vector_store.client.retrieve(pipeline.vector_store.collection, prepared['point_ids'])
    assert rows
    for row in rows:
        assert row.payload.get('mail_scope') == expected[str(row.id)]
        assert row.payload.get('mail_scope_version') == 1


def test_publication_scope_patch_failure_keeps_ready_candidate(pipeline_env, monkeypatch):
    from app.services.generation_publication import publish_prepared_document
    from app.services.errors import VectorStoreError
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    before = _published_snapshot(pipeline)
    original = getattr(pipeline.vector_store, 'patch_mail_scopes', None)
    def fail(*args, **kwargs):
        raise VectorStoreError('Injected readback failure')
    monkeypatch.setattr(pipeline.vector_store, 'patch_mail_scopes', fail, raising=False)
    with pytest.raises(VectorStoreError):
        publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)
    assert _published_snapshot(pipeline) == before
    with session_scope() as session:
        assert session.get(DocumentGenerationState, DOC_ID).candidate_generation_id == candidate
        assert session.get(DocumentGeneration, candidate).phase == 'ready'
    monkeypatch.setattr(pipeline.vector_store, 'patch_mail_scopes', original)
    assert publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)


def test_manifest_foreign_point_identity_is_rejected(pipeline_env, monkeypatch):
    from app.services.generation_publication import publish_prepared_document
    from app.services.generation_artifacts import GenerationIntegrityError
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    before = _published_snapshot(pipeline)
    _rewrite_manifest(pipeline, candidate, lambda p: p['concepts'][0].update(filepath='foreign.md'))
    with pytest.raises(GenerationIntegrityError):
        publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)
    assert _published_snapshot(pipeline) == before
