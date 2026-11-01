"""Durable staging checkpoints survive loss of a worker's entire local cache."""
import shutil

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import ArtifactObject, CheckpointBinding, Document, DocumentStaging
from app.db.session import session_scope
from app.models.schemas import Concept
from app.services.generation_artifacts import GenerationIntegrityError
from app.services.generation_store import begin_generation
from app.services.staging import StagingStore
from tests.test_generation_pipeline import pipeline_env as pipeline_env

DOC = "f" * 16


@pytest.fixture
def portable(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path / "worker-a", artifact_uploads_enabled=True,
                        artifact_root=tmp_path / "durable", artifact_store_id="local")
    monkeypatch.setattr("app.services.staging.get_settings", lambda: settings)
    with session_scope() as session:
        session.add(Document(id=DOC, filename="input.docx"))
        generation = begin_generation(session, DOC).id
    stage = StagingStore(DOC)
    stage.create(2, global_tags=["tag"], parser_version="parser-v1", source_file_hash="a" * 64,
                 generation_id=generation)
    return stage, settings


def prepare(stage):
    stage.save_chunk_text(0, "Исходный текст таблицы\n")
    stage.save_chunk_text(1, "Ещё не обработанный чанк\n")
    stage.set_chunks_metadata([
        {"index": 0, "source_id": "root/1", "table_refs": [{"row_number": 7}], "table_title": "Table"},
        {"index": 1, "source_id": "root"},
    ])
    stage.append_chunk(0, [Concept(id="entry", title="Entry", content="accepted result")],
                       degradation=[{"event": "llm_salvage"}], provenance={"model_id": "original"})


def test_checkpoint_retirement_cannot_delete_a_newer_generation(portable):
    stage, _ = portable
    prepare(stage)
    old_id = stage.generation_id
    from app.services.generation_store import abandon_generation
    with session_scope() as session:
        abandon_generation(session, DOC, old_id)
        new_id = begin_generation(session, DOC).id
    stage.remove()
    stage.create(1, global_tags=[], parser_version="parser-v2", source_file_hash="b" * 64, generation_id=new_id)
    stage.save_chunk_text(0, "new attempt")
    stage.remove(expected_generation_id=old_id)
    assert stage.generation_id == new_id
    assert stage.load()["total_chunks"] == 1
    stage.remove(expected_generation_id=new_id)
    with session_scope() as session:
        assert session.get(DocumentStaging, DOC) is None
        assert session.query(CheckpointBinding).filter_by(doc_id=DOC, generation_id=new_id).count() == 0


def test_staging_checkpoint_survives_worker_disk_loss(portable, tmp_path):
    stage, settings = portable
    prepare(stage)
    before = stage.load()
    shutil.rmtree(settings.data_dir)
    cold = StagingStore(DOC, staging_root=tmp_path / "worker-b" / DOC)
    assert cold.load() == before
    assert [c.content for c in cold.concepts()] == ["accepted result"]
    assert cold.slugs() == ["entry"]
    assert cold.partial_chunks == [0]
    assert (cold.dir / "chunk_00.md").read_text(encoding="utf8") == "Исходный текст таблицы\n"
    assert (cold.dir / "chunk_01.md").read_text(encoding="utf8") == "Ещё не обработанный чанк\n"
    assert cold.load()["chunks_data"]["0"]["table_refs"] == [{"row_number": 7}]


def test_table_ref_checkpoint_hash_is_stable_after_canonical_json_roundtrip(portable):
    stage, _ = portable
    refs = [{"kind": "table", "row_number": 7, "columns": [1, 2], "generation_id": stage.generation_id,
             "source_id": "root", "table_id": "a" * 32}]
    stage.save_chunk_text(0, "table input")
    stage.set_chunks_metadata([{"index": 0, "source_id": "root", "table_refs": refs}])
    assert stage.load()["chunks_data"]["0"]["table_refs"] == refs


def test_staging_checkpoint_restores_through_s3_adapter(portable, monkeypatch, tmp_path):
    from app.services.artifacts.s3 import S3BlobStore
    from tests.support.blob_s3_fake import MemoryS3
    client = MemoryS3()
    monkeypatch.setattr("app.services.artifacts.get_blob_store", lambda settings: S3BlobStore(
        client=client, endpoint="https://s3.invalid", bucket="test-bucket", prefix="test/", store_id="local",
        scratch_root=settings.data_dir / "s3-spool"))
    test_staging_checkpoint_survives_worker_disk_loss(portable, tmp_path)


def test_staging_failed_commit_preserves_previous_checkpoint(portable):
    stage, _ = portable
    prepare(stage)
    before = stage.load()

    def fail_commit(session):
        if session.info.get("portable_staging_write"):
            raise OSError("interrupted checkpoint commit")

    event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(OSError, match="interrupted checkpoint commit"):
            stage.append_chunk(0, [Concept(id="replacement", title="Replacement", content="new result")])
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert stage.load() == before
    assert [c.content for c in stage.concepts()] == ["accepted result"]


@pytest.mark.parametrize("damage", ["binding", "provider"])
def test_staging_missing_durable_checkpoint_never_uses_local_cache(portable, damage):
    stage, settings = portable
    prepare(stage)
    assert (stage.dir / "chunk_00.json").is_file()
    with session_scope() as session:
        binding = session.scalar(select(CheckpointBinding).where(CheckpointBinding.doc_id == DOC,
                                                                  CheckpointBinding.chunk_index == 0))
        assert binding is not None
        obj = session.get(ArtifactObject, binding.object_id)
        key = obj.key
        if damage == "binding":
            session.delete(binding)
    if damage == "provider":
        (settings.artifact_root / key).unlink()
    with pytest.raises(GenerationIntegrityError):
        stage.concepts()


def test_staging_remove_detaches_checkpoints_without_deleting_reader_bytes(portable):
    from app.services.artifacts.lifecycle import collect_blob
    from app.services.artifacts import get_blob_store
    stage, settings = portable
    prepare(stage)
    with session_scope() as session:
        ids = list(session.scalars(select(CheckpointBinding.object_id).where(CheckpointBinding.doc_id == DOC)))
    assert ids
    store = get_blob_store(settings)
    assert all(not collect_blob(store, oid, grace_seconds=0) for oid in ids)
    stage.remove()
    with session_scope() as session:
        assert session.get(DocumentStaging, DOC) is None
        assert not list(session.scalars(select(CheckpointBinding).where(CheckpointBinding.doc_id == DOC)))
    assert all(collect_blob(store, oid, grace_seconds=0) for oid in ids)


def test_staging_removal_does_not_detach_another_generations_checkpoint(portable):
    from app.db.models import DocumentGeneration
    stage, _ = portable
    prepare(stage)
    old_generation = "e" * 32
    with session_scope() as session:
        ref = session.scalar(select(CheckpointBinding).where(CheckpointBinding.doc_id == DOC,
                                                            CheckpointBinding.chunk_index == 0))
        session.add(DocumentGeneration(id=old_generation, doc_id=DOC, phase="active"))
        session.flush()
        session.add(CheckpointBinding(doc_id=DOC, generation_id=old_generation, chunk_index=0,
                                      source_signature="d" * 64, object_id=ref.object_id))
    stage.remove()
    with session_scope() as session:
        assert session.get(CheckpointBinding, (DOC, old_generation, 0, "d" * 64)) is not None


def test_raw_chunk_is_visible_in_local_cache_after_durable_save(portable):
    stage, _ = portable
    stage.save_chunk_text(0, "new input\n")
    assert (stage.dir / "chunk_00.md").read_text(encoding="utf8") == "new input\n"


def test_checkpoint_source_identity_cannot_be_relabelled(portable):
    stage, _ = portable
    prepare(stage)
    with session_scope() as session:
        session.get(DocumentStaging, DOC).source_file_hash = "b" * 64
    with pytest.raises(GenerationIntegrityError):
        stage.load()


@pytest.mark.parametrize("damage", ["all_entries", "pointer", "unknown_version"])
def test_durable_staging_cannot_silently_downgrade_or_lose_completed_chunks(portable, damage):
    stage, settings = portable
    prepare(stage)
    with session_scope() as session:
        row = session.get(DocumentStaging, DOC)
        if damage == "all_entries":
            row.chunks_data = {}
        elif damage == "pointer":
            chunks = dict(row.chunks_data)
            chunks["0"] = {key: value for key, value in chunks["0"].items() if key != "checkpoint"}
            row.chunks_data = chunks
        else:
            row.checkpoint_version = 3
    settings.artifact_uploads_enabled = False
    with pytest.raises(GenerationIntegrityError):
        stage.concepts()


def test_no_checkpoint_provider_io_after_document_deleted(portable, monkeypatch):
    from datetime import datetime, timezone
    stage, _ = portable
    prepare(stage)
    with session_scope() as session:
        session.get(Document, DOC).deleted_at = datetime.now(timezone.utc)
    monkeypatch.setattr("app.services.artifacts.get_blob_store", lambda *_a: pytest.fail("deleted owner must be checked before provider I/O"))
    with pytest.raises(GenerationIntegrityError):
        stage.load()


def test_partial_checkpoint_can_move_to_a_new_candidate_without_worker_cache(portable, tmp_path):
    from app.db.models import DocumentGeneration, DocumentGenerationState
    stage, _ = portable
    prepare(stage)
    old_generation = stage.generation_id
    with session_scope() as session:
        session.get(DocumentGeneration, old_generation).phase = "active"
        state = session.get(DocumentGenerationState, DOC)
        state.active_generation_id, state.candidate_generation_id = old_generation, None
        new_generation = begin_generation(session, DOC).id
    shutil.rmtree(stage.dir)
    cold = StagingStore(DOC, staging_root=tmp_path / "worker-b" / DOC)
    cold.bind_generation(new_generation)
    assert cold.generation_id == new_generation
    cold.append_chunk(0, [Concept(id="complete", title="Entry", content="complete result")])
    assert cold.partial_chunks == []
    assert [c.content for c in cold.concepts()] == ["complete result"]
    with session_scope() as session:
        assert not list(session.scalars(select(CheckpointBinding).where(CheckpointBinding.generation_id == old_generation)))


def test_append_only_reads_its_own_chunk_payload(portable, monkeypatch):
    stage, _ = portable
    prepare(stage)
    original = stage._load_raw
    monkeypatch.setattr(stage, "_load_raw", lambda: pytest.fail("hot append must not hydrate all table coverage"))
    stage.append_chunk(0, [Concept(id="new", title="Entry", content="new result")])
    monkeypatch.setattr(stage, "_load_raw", original)
    assert [c.content for c in stage.concepts()] == ["new result"]


@pytest.mark.parametrize("phase", ["provider", "scratch"])
def test_checkpoint_disk_full_preserves_domain_error_and_old_pointer(portable, monkeypatch, phase):
    import errno
    from types import SimpleNamespace
    from app.services.storage import StorageFullError
    stage, _ = portable
    prepare(stage)

    def full(*_a, **_k):
        raise OSError(errno.ENOSPC, "no space left")

    with monkeypatch.context() as patch:
        if phase == "provider":
            from app.services.artifacts import get_blob_store
            store = get_blob_store(portable[1])
            patch.setattr("app.services.artifacts.get_blob_store", lambda *_a: SimpleNamespace(
                store_id=store.store_id, open=store.open, put=full))
        else:
            patch.setattr("app.services.artifacts.staging._write_canonical", full)
        with pytest.raises(StorageFullError):
            stage.append_chunk(0, [Concept(id="new", title="New", content="new result")])
    assert [c.content for c in stage.concepts()] == ["accepted result"]


def test_stale_checkpoint_commit_cannot_replace_new_result(portable):
    from app.services.artifacts.staging import PortableStaging
    stage, settings = portable
    prepare(stage)
    adapter = PortableStaging(stage, settings)
    stale = adapter._snapshot(0)
    body = adapter._read(stale, 0)
    stage.append_chunk(0, [Concept(id="new", title="Entry", content="new accepted result")])
    with pytest.raises(GenerationIntegrityError):
        adapter._commit(stale, {0: body})
    assert [c.content for c in stage.concepts()] == ["new accepted result"]


def test_staging_migration_refuses_loss_of_portable_protocol_marker(portable, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from alembic import command
    from alembic.config import Config
    from app.db.session import get_engine
    stage, _ = portable
    prepare(stage)
    url = "sqlite:///" + Path(get_engine().url.database).as_posix()
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(db_url=url))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl01d_portable_staging")
    with pytest.raises(RuntimeError, match="portable staging"):
        command.downgrade(config, "rpl01c_portable_publication")
    assert [c.content for c in stage.concepts()] == ["accepted result"]


def test_hydration_cannot_recreate_cache_after_staging_removal(portable, monkeypatch):
    from app.services.artifacts.staging import PortableStaging
    stage, _ = portable
    prepare(stage)
    shutil.rmtree(stage.dir)
    read = PortableStaging._read
    removed = False

    def remove_after_io(adapter, *args):
        nonlocal removed
        body = read(adapter, *args)
        if not removed:
            removed = True
            stage.remove()
        return body

    monkeypatch.setattr(PortableStaging, "_read", remove_after_io)
    with pytest.raises(GenerationIntegrityError):
        stage.load()
    assert not stage.dir.exists()


def test_pipeline_resume_reuses_durable_checkpoint_after_scratch_loss(pipeline_env, monkeypatch, tmp_path):
    from app.services.errors import VectorStoreError
    from app.services.generation_files import generation_paths
    from tests.test_generation_pipeline import DOC_ID
    pipeline, source, write = pipeline_env
    settings = pipeline.settings
    settings.artifact_uploads_enabled = True
    settings.artifact_root = tmp_path / "durable"
    write("Accepted chunk must not be regenerated after losing scratch.")
    original_index = pipeline.vector_store.index_chunks
    monkeypatch.setattr(pipeline.vector_store, "index_chunks", lambda *_a, **_k: (_ for _ in ()).throw(VectorStoreError("index unavailable")))
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    assert pipeline.registry.get(DOC_ID)["status"] == "paused"
    stage = StagingStore(DOC_ID)
    assert stage.processed_chunks == [0]
    generation = stage.generation_id
    paths = generation_paths(settings, DOC_ID, generation)
    for path in (stage.dir, paths.bundle, paths.uploads_root):
        shutil.rmtree(path)
    settings.data_dir = tmp_path / "worker-b"
    cache = settings.staging_dir / DOC_ID
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "chunk_99.md").write_text("unregistered stale scratch must not be indexed", encoding="utf8")
    monkeypatch.setattr(pipeline.vector_store, "index_chunks", original_index)
    monkeypatch.setattr(pipeline.okf_generator, "generate_chunk", lambda *_a, **_k: pytest.fail("accepted LLM checkpoint must be reused"))
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=True)
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
    assert pipeline.registry.get(DOC_ID)["problem"] is None
    with session_scope() as session:
        from app.db.models import DocumentChunk, DocumentGenerationState, OkfConcept
        assert session.get(DocumentGenerationState, DOC_ID).active_generation_id == generation
        assert "Accepted chunk" in session.scalar(select(OkfConcept.content).where(OkfConcept.doc_id == DOC_ID))
        assert [row.chunk_index for row in session.scalars(select(DocumentChunk).where(DocumentChunk.doc_id == DOC_ID))] == [0]
