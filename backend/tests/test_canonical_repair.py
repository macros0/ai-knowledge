"""Canonical repair prepares a private generation before changing live rows."""
from datetime import datetime, timezone

import pytest

from app.db.models import DocumentChunk, DocumentGeneration, DocumentGenerationState, OkfAttachment, OkfConcept
from app.db.session import session_scope
from tests.test_generation_pipeline import DOC_ID, _published_snapshot
from tests.test_generation_pipeline import pipeline_env as pipeline_env


def _edit(snapshot):
    for concept in snapshot["concepts"]:
        if not concept["content"].endswith("\nRepaired."):
            concept["content"] += "\nRepaired."


def _repair(pipeline, **kwargs):
    from app.services.canonical_repair import repair_published_document

    return repair_published_document(
        DOC_ID, pipeline.settings, pipeline.okf_generator, pipeline.embedder, pipeline.vector_store, _edit, **kwargs,
    )


def test_repair_publishes_new_generation_and_copies_registered_artifacts(pipeline_env, monkeypatch):
    pipeline, _source, _write = pipeline_env
    with session_scope() as session:
        concept = session.query(OkfConcept).filter_by(doc_id=DOC_ID).first()
        concept.model_id = "original-model"
        concept.prompt_version = "original-prompt"
        concept.generated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        chunk = session.query(DocumentChunk).filter_by(doc_id=DOC_ID).first()
        from app.services.source_evidence import span_from_offsets

        spans = [span_from_offsets(chunk.content, 0, len(chunk.content)).model_dump()]
        concept.source_spans = spans
        attachment = session.query(OkfAttachment).filter_by(doc_id=DOC_ID).first()
        attachment.processed_at = datetime(2026, 1, 3, tzinfo=timezone.utc)
        attachment.error = "historical diagnostic"
        attachment.extracted_chars = 17
    before = _published_snapshot(pipeline)
    original = pipeline.vector_store.index_concepts
    during_index = []

    def inspect_then_index(*args, **kwargs):
        during_index.append(_published_snapshot(pipeline))
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline.vector_store, "index_concepts", inspect_then_index)
    result = _repair(pipeline)
    after = _published_snapshot(pipeline)
    assert result["changed"] and result["generation_id"] == after[0] != before[0]
    assert during_index == [before]
    assert sorted(after[2].values()) == sorted(before[2].values())
    assert all(path.startswith(f"generations/{after[0]}/") for path in after[2])
    with session_scope() as session:
        concepts = session.query(OkfConcept).filter_by(doc_id=DOC_ID).all()
        assert concepts[0].model_id == "original-model"
        assert concepts[0].prompt_version == "original-prompt"
        assert concepts[0].generated_at.date().isoformat() == "2026-01-02"
        assert concepts[0].source_spans == spans
        attachment = session.query(OkfAttachment).filter_by(doc_id=DOC_ID).first()
        assert attachment.processed_at.date().isoformat() == "2026-01-03"
        assert attachment.error == "historical diagnostic" and attachment.extracted_chars == 17
        assert all(concept.content.endswith("\nRepaired.") for concept in concepts)
    assert not _repair(pipeline)["changed"]
    assert _published_snapshot(pipeline)[0] == after[0]


def test_repair_index_failure_preserves_old_publication_and_records_abandoned_candidate(pipeline_env, monkeypatch):
    pipeline, _source, _write = pipeline_env
    before = _published_snapshot(pipeline)

    def fail(*_args, **_kwargs):
        raise RuntimeError("index failed")

    monkeypatch.setattr(pipeline.vector_store, "index_concepts", fail)
    with pytest.raises(RuntimeError, match="index failed"):
        _repair(pipeline)
    assert _published_snapshot(pipeline) == before
    with session_scope() as session:
        assert session.get(DocumentGenerationState, DOC_ID).candidate_generation_id is None
        assert session.query(DocumentGeneration).filter_by(doc_id=DOC_ID, phase="abandoned").count() == 1


def test_repair_dry_run_has_no_candidate_files_or_index_writes(pipeline_env, monkeypatch):
    pipeline, _source, _write = pipeline_env
    before = _published_snapshot(pipeline)
    files = sorted(path.relative_to(pipeline.settings.data_dir) for path in pipeline.settings.data_dir.rglob("*") if path.is_file())

    def forbidden(*_args, **_kwargs):
        raise AssertionError("dry-run attempted to index")

    monkeypatch.setattr(pipeline.vector_store, "index_concepts", forbidden)
    assert _repair(pipeline, dry_run=True)["changed"]
    assert _published_snapshot(pipeline) == before
    assert files == sorted(path.relative_to(pipeline.settings.data_dir) for path in pipeline.settings.data_dir.rglob("*") if path.is_file())
    with session_scope() as session:
        assert session.query(DocumentGeneration).filter_by(doc_id=DOC_ID).count() == 1


def test_repair_rejects_canonical_change_between_prepare_and_publish(pipeline_env, monkeypatch):
    from app.services import canonical_repair as repair
    from app.services.generation_store import GenerationConflict

    pipeline, _source, _write = pipeline_env
    active = _published_snapshot(pipeline)[0]
    original = repair.publish_prepared_document

    def edit_then_publish(*args):
        with session_scope() as session:
            session.query(OkfConcept).filter_by(doc_id=DOC_ID).first().content = "Newer canonical edit"
        return original(*args)

    monkeypatch.setattr(repair, "publish_prepared_document", edit_then_publish)
    with pytest.raises(GenerationConflict, match="changed before repair"):
        _repair(pipeline)
    assert _published_snapshot(pipeline)[0] == active
    with session_scope() as session:
        assert session.query(OkfConcept).filter_by(doc_id=DOC_ID).first().content == "Newer canonical edit"
        assert session.get(DocumentGenerationState, DOC_ID).candidate_generation_id is None


def test_repair_rejects_existing_candidate_without_modifying_it(pipeline_env):
    from app.services.generation_store import GenerationConflict, begin_generation

    pipeline, _source, _write = pipeline_env
    before = _published_snapshot(pipeline)
    with session_scope() as session:
        candidate = begin_generation(session, DOC_ID).id
    with pytest.raises(GenerationConflict, match="pending generation"):
        _repair(pipeline)
    assert _published_snapshot(pipeline) == before
    with session_scope() as session:
        assert session.get(DocumentGenerationState, DOC_ID).candidate_generation_id == candidate
        assert session.query(DocumentGeneration).filter_by(doc_id=DOC_ID).count() == 2


def test_repair_preserves_noncontiguous_chunk_indices_in_sql_files_and_index(pipeline_env):
    from app.services.generation_files import generation_paths

    pipeline, _source, _write = pipeline_env
    with session_scope() as session:
        old_indices = [row.chunk_index for row in session.query(DocumentChunk).filter_by(doc_id=DOC_ID)]
        offset = max(old_indices) + 4
        expected_indices = {index * 3 + offset for index in old_indices}
        session.query(DocumentChunk).filter_by(doc_id=DOC_ID).update({"chunk_index": DocumentChunk.chunk_index * 3 + offset})
        session.query(OkfConcept).filter_by(doc_id=DOC_ID).update({"chunk_index": OkfConcept.chunk_index * 3 + offset})
    result = _repair(pipeline)
    paths = generation_paths(pipeline.settings, DOC_ID, result["generation_id"])
    assert all((paths.bundle / "chunks" / f"chunk_{index:02d}.md").is_file() for index in expected_indices)
    assert not (paths.bundle / "chunks" / "chunk_00.md").exists()
    points, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
    assert points and {point.payload["chunk_index"] for point in points} == expected_indices


def test_repair_does_not_accept_an_artifact_changed_since_its_recorded_hash(pipeline_env):
    from app.services.generation_files import GenerationStorageError

    pipeline, _source, _write = pipeline_env
    before = _published_snapshot(pipeline)
    old_path = next(iter(before[2]))
    (pipeline.settings.uploads_dir / DOC_ID / old_path).write_bytes(b"unexpected bytes")
    with pytest.raises(GenerationStorageError, match="hash"):
        _repair(pipeline)
    assert _published_snapshot(pipeline)[0] == before[0]
