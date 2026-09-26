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
