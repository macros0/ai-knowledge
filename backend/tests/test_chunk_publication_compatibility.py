"""Explicit manifest/SQL compatibility and actual multipart point faults."""
import json
from uuid import uuid4
import pytest
from qdrant_client import models as qm
from app.db.models import DocumentGeneration, DocumentChunkProjection
from app.db.session import session_scope
from app.services.generation_artifacts import GenerationIntegrityError, file_digest
from app.services.generation_publication import publish_prepared_document
from tests.test_chunk_fragment_publication import prepare
from tests.test_generation_pipeline import DOC_ID, _published_snapshot
from tests.test_generation_pipeline import pipeline_env as pipeline_env


@pytest.mark.parametrize("sql_version", [None, 1, 999])
@pytest.mark.parametrize("manifest_version", ["absent", None, 1, 999, "1", True, False])
def test_manifest_sql_compatibility(pipeline_env, sql_version, manifest_version):
    pipeline, _, _ = pipeline_env
    previous = _published_snapshot(pipeline)
    gen, prepared, path = prepare(pipeline)
    if manifest_version == "absent":
        prepared.pop("chunk_projection_version")
    else:
        prepared["chunk_projection_version"] = manifest_version
    path.write_text(json.dumps(prepared), encoding="utf-8")
    with session_scope() as s:
        generation = s.get(DocumentGeneration, gen)
        generation.chunk_projection_version = sql_version
        generation.publication_hash = file_digest(path)
        if sql_version is None:
            # A real legacy fixture has no versioned parent, even for short roots.
            s.delete(s.get(DocumentChunkProjection, (DOC_ID, gen, 0)))
    allowed = ((sql_version is None and manifest_version in ("absent", None)) or
               (sql_version == 1 and type(manifest_version) is int and manifest_version == 1))
    if allowed:
        assert publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, gen)
        assert _published_snapshot(pipeline)[0] == gen
    else:
        with pytest.raises(GenerationIntegrityError):
            publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, gen)
        assert _published_snapshot(pipeline) == previous


@pytest.mark.parametrize("damage", ["missing", "duplicate", "foreign_generation", "foreign_source",
                                     "missing_descriptor", "bad_ordinal", "old_ready_hash"])
def test_multipart_point_fault_preserves_previous_active(pipeline_env, damage):
    pipeline, _, _ = pipeline_env
    previous = _published_snapshot(pipeline)
    gen, prepared, path = prepare(pipeline, long=True)
    client = pipeline.vector_store.client
    collection = pipeline.vector_store.collection
    point = client.retrieve(collection, [prepared["point_ids"][0]])[0]
    if damage == "missing":
        client.delete(collection, [point.id])
    elif damage == "duplicate":
        client.upsert(collection, [qm.PointStruct(id=str(uuid4()), vector={"": [0.1]*8}, payload=point.payload)])
    elif damage == "old_ready_hash":
        prepared["chunks"][0]["section_title"] = "changed after ready"
        path.write_text(json.dumps(prepared), encoding="utf-8")
    else:
        patch = {"foreign_generation": {"generation_id": "foreign"},
                 "foreign_source": {"source_id": "foreign"},
                 "missing_descriptor": {"chunk_fragment": None},
                 "bad_ordinal": {"chunk_fragment": {**point.payload["chunk_fragment"], "ordinal": 999}}}[damage]
        client.set_payload(collection, patch, [point.id])
    with pytest.raises(GenerationIntegrityError):
        publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, gen)
    assert _published_snapshot(pipeline) == previous
