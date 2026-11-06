"""Portable candidates retain publication integrity across worker scratch roots."""
import json
import shutil

import pytest
from sqlalchemy import select

from app.db.models import ArtifactBinding, DocumentGeneration, DocumentGenerationState
from app.db.session import session_scope
from app.services.generation_artifacts import GenerationIntegrityError
from app.services.generation_files import generation_paths
from app.services.generation_publication import publish_prepared_document
from tests.test_generation_cleanup import _prepare_second
from tests.test_generation_pipeline import DOC_ID, _published_snapshot
from tests.test_generation_pipeline import pipeline_env as pipeline_env
from tests.test_processing_scope_change_end_to_end import source_environment as source_environment


def portable_candidate(pipeline_env, monkeypatch):
    pipeline, _, _ = pipeline_env
    pipeline.settings.artifact_uploads_enabled = True
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    paths = generation_paths(pipeline.settings, DOC_ID, candidate)
    body = json.loads((paths.uploads_root / "publication.json").read_text(encoding="utf-8"))
    return pipeline, candidate, paths, body


def test_preview_pipeline_seals_canonical_blob_manifest(pipeline_env, monkeypatch):
    from app.services.artifacts.repository import resolve_blob
    from app.services.artifacts.publication import canonical_hash
    pipeline, candidate, paths, body = portable_candidate(pipeline_env, monkeypatch)
    assert body["version"] == 2
    assert body["concepts"][0]["filepath"].startswith("bundle/")
    assert body["artifacts"] and all(isinstance(value, dict) for value in body["artifacts"].values())
    with session_scope() as session:
        ref = resolve_blob(session, owner_type="generation", owner_id=DOC_ID,
                           generation_id=candidate, logical_name="publication.json")
        assert canonical_hash(body) == ref.sha256 == session.get(DocumentGeneration, candidate).publication_hash
        assert ref.size == (paths.uploads_root / "publication.json").stat().st_size


@pytest.mark.parametrize("provider", ["local", "s3-protocol"])
def test_candidate_publishes_after_worker_disk_loss(pipeline_env, monkeypatch, tmp_path, provider):
    if provider == "s3-protocol":
        from app.services.artifacts.s3 import S3BlobStore
        from tests.support.blob_s3_fake import MemoryS3
        client = MemoryS3()
        monkeypatch.setattr("app.services.artifacts.get_blob_store", lambda settings: S3BlobStore(
            client=client, endpoint="https://s3.invalid", bucket="test-bucket", prefix="test/",
            store_id="local", scratch_root=settings.data_dir / "s3-spool"))
    pipeline, candidate, paths, _ = portable_candidate(pipeline_env, monkeypatch)
    previous = _published_snapshot(pipeline)[0]
    for root in (paths.bundle, paths.uploads_root):
        assert root.is_relative_to(tmp_path)
        shutil.rmtree(root)
    cold = pipeline.settings.model_copy(update={"data_dir": tmp_path / "worker-b",
                                                "artifact_root": pipeline.settings.data_dir / "artifacts"})
    assert publish_prepared_document(cold, pipeline.vector_store, DOC_ID, candidate)
    with session_scope() as session:
        assert session.get(DocumentGenerationState, DOC_ID).active_generation_id == candidate != previous
    restored = generation_paths(cold, DOC_ID, candidate)
    assert list(restored.bundle.glob("*.md"))
    assert list(restored.attachments.iterdir())


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_artifact_keeps_previous_active(pipeline_env, monkeypatch, tmp_path, damage):
    pipeline, candidate, paths, body = portable_candidate(pipeline_env, monkeypatch)
    before = _published_snapshot(pipeline)
    ref = next(iter(body["artifacts"].values()))
    target = pipeline.settings.data_dir / "artifacts" / ref["key"]
    if damage == "missing":
        target.unlink()
    else:
        target.write_bytes(b"corrupt provider bytes")
    cold = pipeline.settings.model_copy(update={"data_dir": tmp_path / "worker-b",
                                                "artifact_root": pipeline.settings.data_dir / "artifacts"})
    with pytest.raises(GenerationIntegrityError):
        publish_prepared_document(cold, pipeline.vector_store, DOC_ID, candidate)
    assert _published_snapshot(pipeline) == before
    with session_scope() as session:
        assert session.get(DocumentGeneration, candidate).phase == "ready"


def test_incomplete_manifest_cannot_seal(pipeline_env, monkeypatch):
    from app.services.artifacts.publication import write_prepared_publication
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    path = generation_paths(pipeline.settings, DOC_ID, candidate).uploads_root / "publication.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["artifacts"].clear()
    pipeline.settings.artifact_uploads_enabled = True
    with session_scope() as session:
        session.get(DocumentGeneration, candidate).phase = "preparing"
    with pytest.raises(GenerationIntegrityError):
        with session_scope() as session:
            write_prepared_publication(session, pipeline.settings, body)
    with session_scope() as session:
        assert not session.scalars(select(ArtifactBinding).where(ArtifactBinding.generation_id == candidate)).all()


def test_preview_preserves_no_bundle_generation_mode(pipeline_env, monkeypatch, tmp_path):
    pipeline, _, _ = pipeline_env
    pipeline.settings.okf_write_bundles = False
    pipeline, candidate, _, body = portable_candidate(pipeline_env, monkeypatch)
    assert body["version"] == 2
    assert body["bundle_files_enabled"] is False
    cold = pipeline.settings.model_copy(update={"data_dir": tmp_path / "worker-b",
                                                "artifact_root": pipeline.settings.data_dir / "artifacts"})
    assert publish_prepared_document(cold, pipeline.vector_store, DOC_ID, candidate)


def test_portable_replay_does_not_require_provider_or_manifest(pipeline_env, monkeypatch):
    pipeline, candidate, paths, _ = portable_candidate(pipeline_env, monkeypatch)
    assert publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)
    (paths.uploads_root / "publication.json").unlink()
    def forbidden(*args):
        pytest.fail("Replay contacted the provider")
    monkeypatch.setattr("app.services.artifacts.get_blob_store", forbidden)
    assert not publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)


def test_corrupt_local_cache_is_replaced_only_from_verified_blob(pipeline_env, monkeypatch):
    pipeline, candidate, paths, body = portable_candidate(pipeline_env, monkeypatch)
    target = next(paths.bundle.glob("*.md"))
    target.write_bytes(b"broken cache")
    assert publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)
    from app.services.generation_artifacts import file_digest
    name = "bundle/" + target.relative_to(paths.bundle).as_posix()
    assert file_digest(target) == body["artifacts"][name]["sha256"]


def test_sealing_retry_reuses_the_same_blob_references(pipeline_env, monkeypatch):
    from app.services.artifacts.publication import write_prepared_publication
    from app.db.models import ArtifactObject
    pipeline, candidate, _, body = portable_candidate(pipeline_env, monkeypatch)
    original_hash = None
    with session_scope() as session:
        generation = session.get(DocumentGeneration, candidate)
        original_hash = generation.publication_hash
        generation.phase = "preparing"  # Crash after binding, before ready transition.
        count = len(session.scalars(select(ArtifactObject)).all())
    body["artifacts"] = {name: ref["sha256"] for name, ref in body["artifacts"].items()}
    with session_scope() as session:
        assert write_prepared_publication(session, pipeline.settings, body) == original_hash
        assert len(session.scalars(select(ArtifactObject)).all()) == count


@pytest.mark.parametrize("name", ["bundle/../escape", "bundle/a:stream", "bundle/NUL.txt", "bundle/a\\b", "bundle/a.", "bundle/a?b", "bundle/a|b"])
def test_manifest_rejects_nonportable_artifact_names(name):
    from app.services.artifacts.publication import validate_v2
    body = {"version": 2, "doc_id": DOC_ID, "generation_id": "a" * 32,
            "concepts": [], "chunks": [], "sources": [], "attachments": [], "point_ids": [],
            "document_fields": {}, "index_metadata": {}, "parse_effects": {},
            "artifacts": {name: {"object_id": "a" * 32, "key": "objects/" + "a" * 32,
                "sha256": "b" * 64, "size": 0, "media_type": "text/plain", "store_id": "local"}}}
    with pytest.raises(GenerationIntegrityError):
        validate_v2(body, DOC_ID, "a" * 32)


def test_cleanup_race_cannot_resurrect_retired_cache(pipeline_env, monkeypatch):
    from contextlib import contextmanager
    from app.services.artifacts import publication
    from app.services.generation_store import abandon_generation
    from app.services.generation_cleanup import cleanup_document_generations
    pipeline, candidate, paths, _ = portable_candidate(pipeline_env, monkeypatch)
    original = publication.materialize
    cleanup = True
    @contextmanager
    def race(ref, **kwargs):
        nonlocal cleanup
        with original(ref, **kwargs) as source:
            if cleanup:
                cleanup = False
                with session_scope() as session:
                    abandon_generation(session, DOC_ID, candidate)
                # R2 retains any generation still used by an accepted checkpoint.
                # Retire that source before exercising cleanup versus hydration.
                from app.services.staging import StagingStore
                StagingStore(DOC_ID).remove()
                cleanup_document_generations(pipeline.settings, pipeline.vector_store, DOC_ID)
                assert not paths.uploads_root.exists() and not paths.bundle.exists()
            yield source
    monkeypatch.setattr(publication, "materialize", race)
    with pytest.raises(ValueError):
        publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)
    assert not paths.uploads_root.exists() and not paths.bundle.exists()


@pytest.mark.parametrize("backend", ["rwx", "s3"])
def test_legacy_manifest_cannot_be_used_as_shared_publication(pipeline_env, backend):
    from app.services.generation_artifacts import load_verified_publication
    pipeline, _, _ = pipeline_env
    generation = _published_snapshot(pipeline)[0]
    with session_scope() as session:
        expected = session.get(DocumentGeneration, generation).publication_hash
    incompatible = pipeline.settings.model_copy(update={"artifact_backend": backend})
    with pytest.raises(GenerationIntegrityError):
        load_verified_publication(incompatible, DOC_ID, generation, expected)


def test_canonical_repair_uses_portable_publication(pipeline_env):
    from tests.test_canonical_repair import _repair
    pipeline, _, _ = pipeline_env
    pipeline.settings.artifact_uploads_enabled = True
    result = _repair(pipeline)
    path = generation_paths(pipeline.settings, DOC_ID, result["generation_id"]).uploads_root / "publication.json"
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2


@pytest.mark.parametrize("write_bundles", [False, True])
def test_scope_worker_uses_portable_publication(source_environment, monkeypatch, write_bundles):
    from tests.test_processing_scope_change_end_to_end import test_exclude_restore_multiple_times_without_provider_calls
    source_environment["settings"].artifact_uploads_enabled = True
    test_exclude_restore_multiple_times_without_provider_calls(source_environment, monkeypatch,
                                                               legacy=False, write_bundles=write_bundles)
    settings = source_environment["settings"]
    doc_id = source_environment["doc_id"]
    with session_scope() as session:
        active = session.get(DocumentGenerationState, doc_id).active_generation_id
    path = generation_paths(settings, doc_id, active).uploads_root / "publication.json"
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2


def test_lost_v2_bindings_cannot_be_treated_as_legacy(pipeline_env, monkeypatch):
    from app.services.artifacts.publication import hydrate_publication
    pipeline, candidate, _, _ = portable_candidate(pipeline_env, monkeypatch)
    with session_scope() as session:
        assert session.get(DocumentGeneration, candidate).publication_version == 2
        session.query(ArtifactBinding).filter(ArtifactBinding.generation_id == candidate).delete(synchronize_session=False)
    with pytest.raises(GenerationIntegrityError):
        hydrate_publication(pipeline.settings, DOC_ID, candidate)


@pytest.mark.parametrize("route", ["source", "attachment"])
def test_published_download_reads_binding_on_a_cold_api_worker(pipeline_env, monkeypatch, tmp_path, route):
    from tests.test_upload_development import make_client, login
    pipeline, candidate, paths, body = portable_candidate(pipeline_env, monkeypatch)
    assert publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)
    source = next(row for row in body["sources"] if row.get("saved_path"))
    name = source["saved_path"].split(f"generations/{candidate}/", 1)[1]
    expected = (pipeline.settings.uploads_dir / DOC_ID / source["saved_path"]).read_bytes()
    for root in (paths.bundle, paths.uploads_root):
        assert root.is_relative_to(tmp_path)
        shutil.rmtree(root)
    client = make_client(tmp_path / "api-worker", monkeypatch, artifact_root=pipeline.settings.data_dir / "artifacts")
    login(client)
    if route == "source":
        url = f"/api/documents/{DOC_ID}/sources/download?source_id={source['source_id']}"
    else:
        url = f"/api/documents/{DOC_ID}/okf/attachments/{name.split('/')[-1]}"
    response = client.get(url, headers={"Range": "bytes=1-5"})
    assert response.status_code == 206, response.text
    assert response.content == expected[1:6]
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("damage", ["provider", "binding"])
def test_bound_source_never_falls_back_to_remaining_local_cache(pipeline_env, monkeypatch, tmp_path, damage):
    from tests.test_upload_development import make_client, login
    pipeline, candidate, _, body = portable_candidate(pipeline_env, monkeypatch)
    assert publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)
    source = next(row for row in body["sources"] if row.get("saved_path"))
    logical = source["saved_path"].split(f"generations/{candidate}/", 1)[1]
    assert (pipeline.settings.uploads_dir / DOC_ID / source["saved_path"]).exists()
    if damage == "provider":
        (pipeline.settings.data_dir / "artifacts" / body["artifacts"][logical]["key"]).unlink()
    else:
        with session_scope() as session:
            session.query(ArtifactBinding).filter_by(owner_type="generation", owner_id=DOC_ID,
                generation_id=candidate, logical_name=logical).delete(synchronize_session=False)
    client = make_client(pipeline.settings.data_dir, monkeypatch)
    login(client)
    response = client.get(f"/api/documents/{DOC_ID}/sources/download", params={"source_id": source["source_id"]})
    assert response.status_code == (404 if damage == "provider" else 503)


def test_unauthorized_request_does_not_open_generation_blob(pipeline_env, monkeypatch, tmp_path):
    from tests.test_upload_development import make_client
    pipeline, candidate, _, body = portable_candidate(pipeline_env, monkeypatch)
    assert publish_prepared_document(pipeline.settings, pipeline.vector_store, DOC_ID, candidate)
    source = next(row for row in body["sources"] if row.get("saved_path"))
    client = make_client(tmp_path / "api-worker", monkeypatch, artifact_root=pipeline.settings.data_dir / "artifacts")
    def forbidden(*args):
        pytest.fail("Unauthenticated request accessed the blob provider")
    monkeypatch.setattr("app.services.artifacts.get_blob_store", forbidden)
    response = client.get(f"/api/documents/{DOC_ID}/sources/download", params={"source_id": source["source_id"]})
    assert response.status_code == 401


@pytest.mark.parametrize("phase", ["manifest", "provider"])
def test_storage_full_retains_its_domain_error(pipeline_env, monkeypatch, phase):
    import errno
    from types import SimpleNamespace
    from app.services.artifacts import publication
    from app.services.storage import StorageFullError
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    path = generation_paths(pipeline.settings, DOC_ID, candidate).uploads_root / "publication.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    with session_scope() as session:
        session.get(DocumentGeneration, candidate).phase = "preparing"
    pipeline.settings.artifact_uploads_enabled = True
    def full(*args, **kwargs):
        if phase == "manifest":
            raise OSError(errno.ENOSPC, "Private filesystem detail")
        raise StorageFullError()
    if phase == "manifest":
        monkeypatch.setattr(publication, "_write_canonical", full)
    else:
        monkeypatch.setattr("app.services.artifacts.get_blob_store", lambda settings: SimpleNamespace(put=full))
    with pytest.raises(StorageFullError):
        with session_scope() as session:
            publication.write_prepared_publication(session, pipeline.settings, body)


def test_canonical_hash_is_stable_after_json_key_conversion():
    from app.services.artifacts.publication import canonical_hash
    value = {1: "one", 2: "two", 10: "ten"}
    assert canonical_hash(value) == canonical_hash(json.loads(json.dumps(value)))


def test_canonical_hash_rejects_colliding_json_key_representations():
    from app.services.artifacts.publication import canonical_hash
    with pytest.raises(ValueError):
        canonical_hash({1: "first", "1": "second"})
