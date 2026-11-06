"""Source results are durable data, including inactive source IDs and vectors."""
import hashlib
import shutil

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import ArtifactBinding, ArtifactObject, Document
from app.db.session import session_scope
from app.services.generation_store import begin_generation, lock_document_write
from app.services.processing_scope_cache import SourceResultCache
from tests.test_processing_scope_change_end_to_end import source_environment as source_environment

DOC = "0123456789abcdef"
ORIGINAL = b"retained source original"
SOURCE = {"source_key": "a" * 64, "content_fingerprint": hashlib.sha256(ORIGINAL).hexdigest(),
          "source_id": "root/1", "embedding_fingerprint": "space-v1"}


@pytest.fixture
def portable_cache(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path / "worker-a", artifact_root=tmp_path / "durable",
                        artifact_uploads_enabled=True)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    with session_scope() as session:
        session.add(Document(id=DOC, filename="source.docx"))
    cache = SourceResultCache(settings.uploads_dir / DOC / "scope-stage-cache", DOC)
    return cache, settings


def prepare(cache, tmp_path):
    original = tmp_path / "original.docx"
    original.write_bytes(ORIGINAL)
    cache.save_original(SOURCE, original, ".docx")
    cache.save_stage(SOURCE, "parse", {"chunks": [{"chunk_index": 17, "source_id": "root/1", "content": "accepted input"}]},
                     record_streams={"table_rows": ({"row_number": n} for n in range(10))})
    cache.save_stage(SOURCE, "concepts", {"concepts": [{"slug": "original-slug", "generated_at": "accepted-time"}]})
    cache.save_stage(SOURCE, "embeddings", {"embedding_fingerprint": "space-v1",
                                            "vectors": [{"id": "same-id", "vector": [0.125, -0.75]}]})


def test_source_cache_survives_worker_disk_loss(portable_cache, tmp_path):
    cache, settings = portable_cache
    prepare(cache, tmp_path)
    old_location = cache.root
    shutil.rmtree(settings.data_dir)
    settings.data_dir = tmp_path / "worker-b"
    # Durable descriptors can contain another worker's former location.
    cold = SourceResultCache(old_location, DOC)
    assert all(cold.inspect(SOURCE)["stages"].values())
    assert cold.read_stage(SOURCE, "concepts")["concepts"] == [{"slug": "original-slug", "generated_at": "accepted-time"}]
    assert list(cold.iter_stage_records(SOURCE, "parse", "table_rows")) == [{"row_number": n} for n in range(10)]
    assert cold.read_stage(SOURCE, "embeddings")["vectors"] == [{"id": "same-id", "vector": [0.125, -0.75]}]
    assert cold.original_path(SOURCE).read_bytes() == ORIGINAL
    assert cold.root.is_relative_to(settings.data_dir)


def test_cache_writes_join_existing_document_transaction(portable_cache):
    cache, _ = portable_cache
    with session_scope() as session:
        lock_document_write(session, DOC)
        cache.save_stage(SOURCE, "parse", {"chunks": []})
        cache.save_stage(SOURCE, "concepts", {"concepts": []})
        assert cache.read_stage(SOURCE, "concepts") == {"concepts": []}
    assert cache.inspect(SOURCE)["stages"]["concepts"]


def test_failed_cache_commit_keeps_previous_source_results(portable_cache, tmp_path):
    cache, _ = portable_cache
    prepare(cache, tmp_path)

    def fail(session):
        if session.info.get("portable_cache_write"):
            raise OSError("interrupted source cache commit")

    event.listen(Session, "before_commit", fail)
    try:
        with pytest.raises(OSError, match="interrupted source cache commit"):
            cache.save_stage(SOURCE, "concepts", {"concepts": [{"slug": "replacement"}]})
    finally:
        event.remove(Session, "before_commit", fail)
    assert cache.read_stage(SOURCE, "concepts")["concepts"][0]["slug"] == "original-slug"
    assert cache.inspect(SOURCE)["stages"]["embeddings"]


def test_missing_cache_blob_cannot_be_replaced_by_local_files(portable_cache, tmp_path):
    from app.services.generation_artifacts import GenerationIntegrityError
    cache, settings = portable_cache
    prepare(cache, tmp_path)
    with session_scope() as session:
        object_id = session.scalar(select(ArtifactBinding.object_id).where(ArtifactBinding.owner_type == "source",
                                                                          ArtifactBinding.logical_name.like("source-cache/%")))
        assert object_id is not None
        key = session.get(ArtifactObject, object_id).key
    (settings.artifact_root / key).unlink()
    with pytest.raises(GenerationIntegrityError):
        cache.inspect(SOURCE)
    with pytest.raises(GenerationIntegrityError):
        cache.read_stage(SOURCE, "concepts")


def test_source_cache_copy_to_candidate_survives_cold_worker(portable_cache, tmp_path):
    from app.services.generation_files import generation_paths
    cache, settings = portable_cache
    prepare(cache, tmp_path)
    with session_scope() as session:
        generation = begin_generation(session, DOC).id
        destination = SourceResultCache(generation_paths(settings, DOC, generation).bundle / "source-cache", DOC)
        cache.copy_to(SOURCE, destination)
    shutil.rmtree(settings.data_dir)
    settings.data_dir = tmp_path / "worker-b"
    cold = SourceResultCache(destination.root, DOC)
    assert all(cold.inspect(SOURCE)["stages"].values())
    assert cold.original_path(SOURCE).read_bytes() == ORIGINAL


def test_scope_exclusion_and_restore_reuse_cold_source_cache(source_environment, monkeypatch, tmp_path):
    from uuid import uuid4
    from app.services.artifacts import get_blob_store
    from app.services.artifacts.repository import attach_blob
    import app.services.processing_scope_jobs as jobs
    from tests.test_processing_scope_change_end_to_end import test_exclude_restore_multiple_times_without_provider_calls
    env = source_environment
    settings = env["settings"]
    settings.artifact_uploads_enabled = True
    settings.artifact_root = tmp_path / "durable"
    with env["root_file"].open("rb") as stream:
        ref = get_blob_store(settings).put(stream, object_id=uuid4().hex, media_type="application/octet-stream")
    with session_scope() as session:
        attach_blob(session, ref=ref, owner_type="original", owner_id=env["doc_id"], generation_id=None, logical_name="original")
    preview = jobs.create_change_preview
    calls = 0
    def cold_preview(*args, **kwargs):
        nonlocal calls
        if calls:
            for folder in (settings.uploads_dir, settings.okf_dir):
                if folder.exists():
                    shutil.rmtree(folder)
            settings.data_dir = tmp_path / f"worker-cold-{calls}"
        calls += 1
        return preview(*args, **kwargs)
    monkeypatch.setattr(jobs, "create_change_preview", cold_preview)
    test_exclude_restore_multiple_times_without_provider_calls(env, monkeypatch, legacy=False, write_bundles=True)


def test_copy_by_cache_identity_does_not_require_old_manifest_path(portable_cache, tmp_path):
    from app.services.processing_scope_cache import copy_source_cache
    from app.services.generation_files import generation_paths
    cache, settings = portable_cache
    prepare(cache, tmp_path)
    cache_id = cache.identity(SOURCE)
    shutil.rmtree(settings.data_dir)
    settings.data_dir = tmp_path / "worker-b"
    with session_scope() as session:
        generation = begin_generation(session, DOC).id
    assert copy_source_cache(DOC, cache_id, generation)["verified"]
    target = SourceResultCache(generation_paths(settings, DOC, generation).bundle / "source-cache", DOC)
    assert target.read_stage(SOURCE, "concepts")["concepts"][0]["slug"] == "original-slug"


def test_full_regeneration_guard_checks_cold_cache_inventory_in_sql(portable_cache, tmp_path):
    from app.db.models import DocumentGeneration, DocumentGenerationState
    from app.services.processing_scope_cache import assert_full_regeneration_cache_safe
    from app.services.errors import ConflictError
    cache, settings = portable_cache
    prepare(cache, tmp_path)
    with session_scope() as session:
        session.add(DocumentGeneration(id="c" * 32, doc_id=DOC, phase="active"))
        session.add(DocumentGenerationState(doc_id=DOC, active_generation_id="c" * 32))
    shutil.rmtree(settings.data_dir)
    settings.data_dir = tmp_path / "worker-b"
    with session_scope() as session:
        with pytest.raises(ConflictError):
            assert_full_regeneration_cache_safe(session, settings, DOC, {})


def test_first_cache_commit_failure_can_retry_without_promoting_leftover_files(portable_cache):
    cache, _ = portable_cache
    def fail(session):
        if session.info.get("portable_cache_write"):
            raise OSError("first commit interrupted")
    event.listen(Session, "before_commit", fail)
    try:
        with pytest.raises(OSError):
            cache.save_stage(SOURCE, "parse", {"chunks": [{"content": "uncommitted"}]})
    finally:
        event.remove(Session, "before_commit", fail)
    cache.save_stage(SOURCE, "parse", {"chunks": [{"content": "accepted retry"}]})
    assert cache.read_stage(SOURCE, "parse") == {"chunks": [{"content": "accepted retry"}]}
