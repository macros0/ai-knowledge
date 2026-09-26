"""A prepared generation must remain intact until atomic publication."""
import json

import pytest

from app.db.models import DocumentGeneration, OkfConcept
from app.db.session import session_scope
from app.services.generation_files import generation_paths
from app.services.staging import StagingStore
from tests.test_generation_cleanup import _prepare_second
from tests.test_generation_pipeline import DOC_ID, _published_snapshot
from tests.test_generation_pipeline import pipeline_env as pipeline_env


@pytest.mark.parametrize("damage", [
    "missing_attachment", "changed_attachment", "missing_bundle", "changed_manifest",
    "missing_point", "foreign_point", "missing_digest",
])
def test_damaged_ready_generation_cannot_replace_active_publication(pipeline_env, monkeypatch, damage):
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    previous = _published_snapshot(pipeline)
    paths = generation_paths(pipeline.settings, DOC_ID, candidate)
    if damage == "missing_attachment":
        next(paths.attachments.iterdir()).unlink()
    elif damage == "changed_attachment":
        attachment = next(paths.attachments.iterdir())
        attachment.write_bytes(b"x" * attachment.stat().st_size)
    elif damage == "missing_bundle":
        next(paths.bundle.glob("*.md")).unlink()
    elif damage == "changed_manifest":
        path = paths.uploads_root / "publication.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["concepts"][0]["content"] = "Changed after preparation"
        path.write_text(json.dumps(manifest), encoding="utf-8")
    elif damage == "missing_digest":
        with session_scope() as session:
            session.get(DocumentGeneration, candidate).publication_hash = None
    else:
        points, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
        point = next(point for point in points if point.payload.get("generation_id") == candidate)
        if damage == "missing_point":
            pipeline.vector_store.client.delete(pipeline.vector_store.collection, [point.id])
        else:
            pipeline.vector_store.client.set_payload(pipeline.vector_store.collection, {"doc_id": "other"}, [point.id])

    with pytest.raises(ValueError):
        pipeline._publish_prepared_generation(DOC_ID, candidate, StagingStore(DOC_ID))

    assert _published_snapshot(pipeline) == previous
    with session_scope() as session:
        assert session.get(DocumentGeneration, candidate).phase == "ready"


def test_replaying_published_generation_does_not_reapply_manifest(pipeline_env):
    pipeline, _source, _write = pipeline_env
    active = _published_snapshot(pipeline)[0]
    with session_scope() as session:
        concept = session.query(OkfConcept).filter_by(doc_id=DOC_ID).first()
        concept.content = "A later canonical correction"
    expected = _published_snapshot(pipeline)
    pipeline._publish_prepared_generation(DOC_ID, active, StagingStore(DOC_ID))
    assert _published_snapshot(pipeline) == expected


def test_replaying_publication_does_not_require_old_preparation_manifest(pipeline_env):
    pipeline, _source, _write = pipeline_env
    expected = _published_snapshot(pipeline)
    paths = generation_paths(pipeline.settings, DOC_ID, expected[0])
    (paths.uploads_root / "publication.json").unlink()
    pipeline._publish_prepared_generation(DOC_ID, expected[0], StagingStore(DOC_ID))
    assert _published_snapshot(pipeline) == expected
