"""Recovery changes attempt identity while retaining accepted immutable outputs."""
import pytest

from app.db.models import CheckpointBinding, DocumentGeneration
from app.db.session import session_scope
from app.services.staging import StagingStore
from app.services.work.models import WorkSpec
from app.services.work.store import enqueue, claim
from app.services.work.document_handler import recover_document_attempt
from tests.test_portable_staging import DOC, prepare, portable as portable


def take_work():
    with session_scope() as session:
        enqueue(session, WorkSpec("document", 1, {"action": "resume", "doc_id": DOC}, "recovery", DOC))
        return claim(session, owner_id="new-worker", supported={"document": {1}}, lease_seconds=120)


def test_checkpoint_reuse_keeps_accepted_ids_on_cold_new_attempt(portable, tmp_path):
    stage, settings = portable
    prepare(stage)
    old = stage.load()
    work = take_work()
    new_id = recover_document_attempt(work, settings, parser_version="parser-v1", source_file_hash="a" * 64, processing_scope_hash=None)
    assert new_id != old["generation_id"]
    settings.data_dir = tmp_path / "cold-replacement"
    cold = StagingStore(DOC)
    assert cold.load()["generation_id"] == new_id
    assert [concept.id for concept in cold.concepts()] == ["entry"]
    assert [concept.content for concept in cold.concepts()] == ["accepted result"]
    assert cold.load()["chunks_data"]["0"]["table_refs"] == [{"row_number": 7}]
    assert cold.load()["processed_chunks"] == old["processed_chunks"]
    with session_scope() as session:
        assert session.get(DocumentGeneration, old["generation_id"]).phase == "abandoned"
        assert {row.generation_id for row in session.query(CheckpointBinding)} == {new_id}


@pytest.mark.parametrize("changed", ["parser_version", "source_file_hash", "processing_scope_hash"])
def test_checkpoint_reuse_requires_matching_signature(portable, changed):
    stage, settings = portable
    prepare(stage)
    old = stage.load()
    work = take_work()
    signature = dict(parser_version="parser-v1", source_file_hash="a" * 64, processing_scope_hash=None)
    signature[changed] = "changed"
    with pytest.raises(ValueError):
        recover_document_attempt(work, settings, **signature)
    assert stage.load() == old


def test_recovery_retains_scoped_source_results_on_cold_worker(portable, tmp_path, monkeypatch):
    from app.services.generation_files import generation_paths
    from app.services.processing_scope_cache import SourceResultCache
    from tests.test_portable_source_cache import SOURCE, prepare as prepare_cache
    stage, settings = portable
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    prepare(stage)
    old_id = stage.load()["generation_id"]
    cache = SourceResultCache(generation_paths(settings, DOC, old_id).bundle / "source-cache", DOC)
    prepare_cache(cache, tmp_path)
    new_id = recover_document_attempt(take_work(), settings, parser_version="parser-v1", source_file_hash="a" * 64, processing_scope_hash=None)
    settings.data_dir = tmp_path / "cold-source-worker"
    cold = SourceResultCache(generation_paths(settings, DOC, new_id).bundle / "source-cache", DOC)
    assert cold.read_stage(SOURCE, "embeddings")["vectors"] == [{"id": "same-id", "vector": [0.125, -0.75]}]
    assert cold.read_stage(SOURCE, "concepts")["concepts"][0]["slug"] == "original-slug"


def test_recovery_resumes_after_checkpoint_commit_failure(portable):
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from app.services.generation_cleanup import cleanup_document_generations
    stage, settings = portable
    prepare(stage)
    work = take_work()
    def fail(session):
        if session.info.get("portable_staging_write"):
            raise OSError("interrupted recovery commit")
    event.listen(Session, "before_commit", fail)
    try:
        with pytest.raises(OSError):
            recover_document_attempt(work, settings, parser_version="parser-v1", source_file_hash="a" * 64, processing_scope_hash=None)
    finally:
        event.remove(Session, "before_commit", fail)
    class Vector:
        def delete_generation_points(self, *_args):
            pytest.fail("Accepted source checkpoint must remain protected during recovery")
    cleanup_document_generations(settings, Vector(), DOC)
    new_id = recover_document_attempt(work, settings, parser_version="parser-v1", source_file_hash="a" * 64, processing_scope_hash=None)
    assert stage.load()["generation_id"] == new_id
    assert [concept.id for concept in stage.concepts()] == ["entry"]


def test_recovery_preserves_table_rows_and_rebinds_reference_generation(portable, tmp_path, monkeypatch):
    from app.db.models import DocumentTableRow
    from tests import test_table_store
    stage, settings = portable
    prepare(stage)
    old_id = stage.load()["generation_id"]
    monkeypatch.setattr(test_table_store, "DOC", DOC)
    table_id = test_table_store.write(tmp_path, old_id)
    stage.set_chunks_metadata([{"index": 0, "source_id": "root", "table_refs": [{
        "kind": "table", "generation_id": old_id, "source_id": "root", "table_id": table_id, "row_number": 3, "columns": [1, 2]}]}])
    with session_scope() as session:
        before = [(row.row_number, row.cells, row.hidden) for row in session.query(DocumentTableRow).filter_by(generation_id=old_id).order_by(DocumentTableRow.row_number)]
    new_id = recover_document_attempt(take_work(), settings, parser_version="parser-v1", source_file_hash="a" * 64, processing_scope_hash=None)
    with session_scope() as session:
        after = [(row.row_number, row.cells, row.hidden) for row in session.query(DocumentTableRow).filter_by(generation_id=new_id).order_by(DocumentTableRow.row_number)]
    assert before and before == after
    assert stage.load()["chunks_data"]["0"]["table_refs"][0]["generation_id"] == new_id
