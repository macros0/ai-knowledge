"""Offline migration never changes the selected source before verified cutover."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from app.config import Settings
from app.db.models import Document, ArtifactBinding
from app.db.session import session_scope
from app.services.artifacts.local import LocalBlobStore
from tests.test_generation_pipeline import pipeline_env as pipeline_env
from tests.test_portable_source_cache import portable_cache as portable_cache


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path / "source", artifact_root=tmp_path / "source-blobs",
                        artifact_store_id="source", diagnostics_dir=tmp_path / "source-diagnostics")
    settings.uploads_dir.mkdir(parents=True)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    for number in (1, 2):
        doc_id = f"{number:016x}"
        payload = f"accepted original {number}".encode()
        (settings.uploads_dir / (doc_id + ".docx")).write_bytes(payload)
        with session_scope() as session:
            session.add(Document(id=doc_id, filename=f"{number}.docx", status="uploaded",
                                 file_hash=hashlib.sha256(payload).hexdigest()))
    return settings, tmp_path


def migration(corpus, **changes):
    from app.services.artifacts.migration import StorageMigration, installation_id
    settings, root = corpus
    source = {"version": 1, "installation_id": installation_id(),
              "store": {"backend": "local", "store_id": "source", "root": str(settings.artifact_root)}}
    target = {"version": 1, "installation_id": source["installation_id"],
              "store": {"backend": "local", "store_id": "target", "root": str(root / "target")}}
    target.update(changes)
    stores = {"source": LocalBlobStore(settings.artifact_root, store_id="source"),
              "target": LocalBlobStore(root / "target", store_id="target")}
    return StorageMigration(settings, source, target, root / "transfer" / "plan.json", stores=stores)


def backup(migration, root):
    plan = migration.plan()
    files = []
    for role in ("database", "artifacts"):
        path = root / (role + ".backup")
        path.write_bytes(b"isolated fixture backup " + role.encode())
        files.append({"role": role, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size})
    path = root / "backup.json"
    path.write_text(json.dumps({"version": 1, "installation_id": plan["installation_id"],
                                "source_revision": plan["source_revision"], "files": files}))
    return path


def test_resume_copy_without_duplicates(corpus, monkeypatch):
    task = migration(corpus)
    proof = backup(task, corpus[1])
    target = task.stores["target"]
    put = target.put
    calls = []
    def interrupt(source, **kwargs):
        calls.append(kwargs["object_id"])
        if len(calls) == 2:
            raise OSError("interrupted copy")
        return put(source, **kwargs)
    monkeypatch.setattr(target, "put", interrupt)
    with pytest.raises(OSError):
        task.copy(apply=True, writers_stopped=True, backup=proof)
    monkeypatch.setattr(target, "put", put)
    task.copy(apply=True, writers_stopped=True, backup=proof)
    task.verify(apply=True, writers_stopped=True, backup=proof)
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    with session_scope() as session:
        bindings = session.query(ArtifactBinding).filter_by(owner_type="original").all()
        assert len(bindings) == 2 and len({row.object_id for row in bindings}) == 2
    assert len(list((target.root / "objects").iterdir())) == 2


def test_bad_hash_refuses_cutover(corpus):
    from app.services.artifacts.contract import BlobIntegrityError
    task = migration(corpus)
    proof = backup(task, corpus[1])
    task.copy(apply=True, writers_stopped=True, backup=proof)
    item = task.plan()["objects"][0]
    (task.stores["target"].root / item["ref"]["key"]).write_bytes(b"corrupted")
    with pytest.raises((BlobIntegrityError, ValueError)):
        task.cutover(apply=True, writers_stopped=True, backup=proof)
    assert task.active_store_id() == "source"


def test_wrong_installation_refused(corpus):
    task = migration(corpus, installation_id="f" * 32)
    with pytest.raises(ValueError):
        task.plan()
    assert not list(task.stores["target"].root.glob("objects/*"))


def test_unverified_target_never_active(corpus):
    task = migration(corpus)
    proof = backup(task, corpus[1])
    with pytest.raises(ValueError):
        task.cutover(apply=True, writers_stopped=True, backup=proof)
    assert task.active_store_id() in {None, "source"}


def test_apply_requires_stopped_writers_and_independent_backup(corpus):
    task = migration(corpus)
    task.plan()
    with pytest.raises(ValueError):
        task.copy(apply=True, writers_stopped=False)
    with pytest.raises(ValueError):
        task.copy(apply=True, writers_stopped=True)
    assert task.active_store_id() is None


def relocate(settings, root):
    old = settings.data_dir
    new = root / "legacy-source"
    new.mkdir()
    for name in ("uploads", "okf_bundles", "staging", "exports"):
        path = old / name
        if path.exists():
            assert path.absolute().is_relative_to(root.absolute()) and (new / name).absolute().is_relative_to(root.absolute())
            shutil.move(str(path), str(new / name))
    settings.data_dir = new


def move_task(settings, root, monkeypatch, provider="local"):
    from app.services.artifacts.migration import StorageMigration, installation_id
    from app.services.artifacts.control import descriptor, enforce_runtime
    from app.services.artifacts.rwx import RwxBlobStore
    from app.services.artifacts.s3 import S3BlobStore
    from tests.support.blob_s3_fake import MemoryS3
    settings.artifact_root = settings.artifact_root or settings.data_dir / "artifacts"
    if not settings.diagnostics_dir.absolute().is_relative_to(root.absolute()):
        settings.diagnostics_dir = root / "legacy-diagnostics"
    source = {"version": 1, "installation_id": installation_id(), "store": descriptor(settings)}
    target_store = {"backend": provider, "store_id": "target", "root": str(root / "target-provider")}
    if provider == "s3":
        target_store = dict(backend="s3", store_id="target", endpoint="https://s3.invalid", bucket="test-bucket", prefix="install/",
                            region="us-east-1", addressing_style="path", allow_http=False)
        adapter = S3BlobStore(client=MemoryS3(), endpoint="https://s3.invalid", bucket="test-bucket", prefix="install/",
                             store_id="target", scratch_root=root / "target-scratch")
    else:
        (root / "target-provider").mkdir()
        adapter = (RwxBlobStore if provider == "rwx" else LocalBlobStore)(root / "target-provider", store_id="target")
    target = {"version": 1, "installation_id": source["installation_id"], "store": target_store}
    stores = {settings.artifact_store_id: LocalBlobStore(settings.artifact_root, store_id=settings.artifact_store_id), "target": adapter}
    def build(actual):
        enforce_runtime(actual)
        return stores[actual.artifact_store_id]
    monkeypatch.setattr("app.services.artifacts.get_blob_store", build)
    return StorageMigration(settings, source, target, root / "transfer" / "plan.json", stores=stores)


def switch_cache(task, root):
    from app.services.artifacts.control import STORE_FIELDS
    settings = task.runtime_settings
    for key, value in task.plan()["target"].items():
        setattr(settings, STORE_FIELDS[key], Path(value) if key in {"root", "ca_bundle"} else value)
    settings.data_dir = root / "cold-worker"
    settings.diagnostics_dir = root / "cold-diagnostics"
    return settings


@pytest.mark.parametrize("provider", ["local", "rwx", "s3"])
def test_paths_rebased_for_attachments_and_cold_publication(pipeline_env, tmp_path, monkeypatch, provider):
    from app.db.models import DocumentGenerationState, DocumentGeneration, OkfConcept
    from app.services.export_okf import export_okf_bundle
    from app.services.generation_artifacts import load_verified_publication
    from app.services.artifacts.publication import hydrate_publication
    from tests.test_generation_pipeline import DOC_ID
    pipeline, _, _ = pipeline_env
    settings = pipeline.settings
    from app.services.sparse import to_sparse_vector
    def search():
        return [(hit.point_id, hit.payload) for hit in pipeline.vector_store.search_composite(
            dense_vec=None, sparse_vec=to_sparse_vector("Original approved decision"),
            branches={"bm25"}, tags=None, top_k=20)]
    search_before = search()
    assert search_before
    with session_scope() as session:
        generation = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        concepts = [(c.id, c.slug, c.content) for c in session.query(OkfConcept)]
    relocate(settings, tmp_path)
    task = move_task(settings, tmp_path, monkeypatch, provider)
    proof = backup(task, tmp_path)
    task.copy(apply=True, writers_stopped=True, backup=proof)
    task.verify(apply=True, writers_stopped=True, backup=proof)
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    assert task.cutover(apply=True, writers_stopped=True, backup=proof)["cutover"]
    settings = switch_cache(task, tmp_path)
    assert search() == search_before
    assert hydrate_publication(settings, DOC_ID, generation)
    with session_scope() as session:
        active = session.get(DocumentGeneration, generation)
        assert active.publication_version == 2
        assert session.get(DocumentGenerationState, DOC_ID).active_generation_id == generation
        assert concepts == [(c.id, c.slug, c.content) for c in session.query(OkfConcept)]
        published = load_verified_publication(settings, DOC_ID, generation, active.publication_hash, session=session)
    assert all(Path(item["filepath"]).is_relative_to(settings.data_dir) for item in published["concepts"])
    files = export_okf_bundle(DOC_ID, tmp_path / "exported")
    assert any(name.startswith("attachments/") for name in files)
    assert task.cutover(apply=True, writers_stopped=True, backup=proof)["store_id"] == "target"


def test_unversioned_legacy_files_restore_on_cold_worker_without_new_generation(corpus, monkeypatch):
    from app.services.generation_files import active_bundle_path
    from app.db.models import DocumentGenerationState
    settings, root = corpus
    doc_id = "0000000000000001"
    bundle = settings.okf_dir / doc_id
    bundle.mkdir(parents=True)
    (bundle / "accepted.md").write_text("accepted legacy concept", encoding="utf8")
    task = migration(corpus)
    proof = backup(task, root)
    task.copy(apply=True, writers_stopped=True, backup=proof)
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    settings = switch_cache(task, root)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    path = active_bundle_path(settings, doc_id)
    assert (path / "accepted.md").read_text(encoding="utf8") == "accepted legacy concept"
    with session_scope() as session:
        assert session.get(DocumentGenerationState, doc_id) is None


def test_legacy_checkpoint_and_candidate_sidecars_survive_migration(corpus, monkeypatch):
    from app.models.schemas import Concept
    from app.services.generation_store import begin_generation
    from app.services.generation_files import generation_paths
    from app.services.staging import StagingStore
    from app.services.artifacts.publication import hydrate_publication
    settings, root = corpus
    monkeypatch.setattr("app.services.staging.get_settings", lambda: settings)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    doc_id = "0000000000000001"
    with session_scope() as session:
        generation = begin_generation(session, doc_id).id
    stage = StagingStore(doc_id)
    stage.create(1, generation_id=generation, parser_version="accepted-parser", source_file_hash="a" * 64)
    stage.save_chunk_text(0, "accepted input")
    stage.set_chunks_metadata([{"index": 0, "source_id": "root", "table_refs": [{"row_number": 7, "table_id": "b" * 32}]}])
    stage.append_chunk(0, [Concept(id="accepted-id", title="Accepted", content="accepted output")])
    sidecar = generation_paths(settings, doc_id, generation).bundle / "tables" / "rows.jsonl"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text('{"cells":[7,9]}\n', encoding="utf8")
    task = move_task(settings, root, monkeypatch)
    proof = backup(task, root)
    task.copy(apply=True, writers_stopped=True, backup=proof)
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    switch_cache(task, root)
    cold = StagingStore(doc_id)
    assert cold.load()["generation_id"] == generation
    assert [value.id for value in cold.concepts()] == ["accepted-id"]
    assert hydrate_publication(settings, doc_id, generation)
    assert (generation_paths(settings, doc_id, generation).bundle / "tables" / "rows.jsonl").read_text(encoding="utf8") == '{"cells":[7,9]}\n'


def test_existing_portable_cache_rewrites_nested_blob_refs(portable_cache, tmp_path, monkeypatch):
    from tests.test_portable_source_cache import prepare, SOURCE, ORIGINAL
    from app.services.processing_scope_cache import SourceResultCache
    cache, settings = portable_cache
    prepare(cache, tmp_path)
    (settings.uploads_dir / (cache.doc_id + ".docx")).write_bytes(ORIGINAL)
    task = move_task(settings, tmp_path, monkeypatch, "s3")
    proof = backup(task, tmp_path)
    task.copy(apply=True, writers_stopped=True, backup=proof)
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    switch_cache(task, tmp_path)
    cold = SourceResultCache(settings.uploads_dir / cache.doc_id / "scope-stage-cache", cache.doc_id)
    assert cold.original_path(SOURCE).read_bytes() == ORIGINAL
    assert cold.read_stage(SOURCE, "embeddings")["vectors"] == [{"id": "same-id", "vector": [0.125, -0.75]}]


def test_maintenance_blocks_ordinary_sessions_and_gc(corpus):
    task = migration(corpus)
    proof = backup(task, corpus[1])
    task.copy(apply=True, writers_stopped=True, backup=proof)
    with pytest.raises(ValueError, match="maintenance"):
        with session_scope():
            pass
    from app.services.artifacts.control import enforce_runtime
    with pytest.raises(ValueError, match="maintenance"):
        enforce_runtime(corpus[0])
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    with pytest.raises(ValueError, match="selection"):
        enforce_runtime(corpus[0])


def test_source_mutation_after_plan_blocks_switch_even_if_target_is_good(corpus):
    task = migration(corpus)
    proof = backup(task, corpus[1])
    task.copy(apply=True, writers_stopped=True, backup=proof)
    (corpus[0].uploads_dir / "0000000000000001.docx").write_bytes(b"source changed")
    with pytest.raises(ValueError, match="Source|source"):
        task.cutover(apply=True, writers_stopped=True, backup=proof)
    assert task.active_store_id() == "source"


def test_old_runtime_cannot_commit_metadata_after_cutover(corpus):
    task = migration(corpus)
    proof = backup(task, corpus[1])
    task.copy(apply=True, writers_stopped=True, backup=proof)
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    with pytest.raises(ValueError, match="selection"):
        with session_scope() as session:
            session.get(Document, "0000000000000001").status = "paused"


def test_lost_legacy_snapshot_pointer_cannot_fall_back_to_cached_files(corpus, monkeypatch):
    from app.services.artifacts.control import migration_access
    from app.services.generation_files import active_bundle_path
    from app.services.generation_artifacts import GenerationIntegrityError
    settings, root = corpus
    bundle = settings.okf_dir / "0000000000000001"
    bundle.mkdir(parents=True)
    (bundle / "accepted.md").write_text("accepted legacy concept", encoding="utf8")
    task = migration(corpus)
    proof = backup(task, root)
    task.copy(apply=True, writers_stopped=True, backup=proof)
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    settings = switch_cache(task, root)
    active_bundle_path(settings, "0000000000000001")
    with migration_access(), session_scope() as session:
        session.get(Document, "0000000000000001").legacy_artifacts_id = None
    with pytest.raises(GenerationIntegrityError):
        active_bundle_path(settings, "0000000000000001")


def test_legacy_review_and_source_cache_retain_originals_and_vectors(corpus, monkeypatch):
    from app.services.processing_scope_cache import SourceResultCache
    from app.services.processing_scope import create_review
    from app.services.generation_files import processing_review_paths
    from app.services.artifacts.review_files import hydrate_review_files
    from app.db.models import DocumentProcessingReview
    from docparser.attachment_policy import AttachmentInventory, AttachmentNode
    settings, root = corpus
    doc_id = "0000000000000001"
    original = root / "attachment.bin"
    original.write_bytes(b"accepted attachment")
    fingerprint = hashlib.sha256(original.read_bytes()).hexdigest()
    source = dict(source_key="a" * 64, content_fingerprint=fingerprint, embedding_fingerprint="accepted-space")
    cache = SourceResultCache(settings.uploads_dir / doc_id / "scope-stage-cache", doc_id, _local_only=True)
    cache.save_original(source, original, ".bin")
    cache.save_stage(source, "parse", {"chunks": [{"chunk_index": 7, "source_id": "root/one", "content": "accepted"}]})
    cache.save_stage(source, "concepts", {"concepts": [{"slug": "accepted-id"}]})
    cache.save_stage(source, "embeddings", {"embedding_fingerprint": "accepted-space", "vectors": [{"id": "same-id", "vector": [.1, .2]}]})
    with session_scope() as session:
        session.get(Document, doc_id).status = "paused"
        inventory = AttachmentInventory(source_file_hash=session.get(Document, doc_id).file_hash, nodes=[
            AttachmentNode("a" * 64, "root", "one", "one.bin", "bin", saved_path="one.bin", content_fingerprint=fingerprint)])
        review = create_review(session, doc_id, inventory, actor="operator", base_generation_id=None)
        files = processing_review_paths(settings, doc_id, review.id) / "attachments"
        files.mkdir(parents=True)
        (files / "one.bin").write_bytes(original.read_bytes())
    task = move_task(settings, root, monkeypatch, "s3")
    proof = backup(task, root)
    task.copy(apply=True, writers_stopped=True, backup=proof)
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    switch_cache(task, root)
    cold = SourceResultCache(settings.uploads_dir / doc_id / "scope-stage-cache", doc_id)
    assert cold.original_path(source).read_bytes() == b"accepted attachment"
    assert cold.read_stage(source, "embeddings")["vectors"] == [{"id": "same-id", "vector": [.1, .2]}]
    from app.api.document_processing_scope import download_review_attachment
    from app.auth.models import User
    monkeypatch.setattr("app.api.document_processing_scope.get_settings", lambda: settings)
    response = download_review_attachment(doc_id, "a" * 64, User(user_id="operator", username="operator", roles=["admin"]))
    try:
        assert response._file.read() == b"accepted attachment"
    finally:
        if hasattr(response, "_file"):
            response._file.close()
    with session_scope() as session:
        review = session.get(DocumentProcessingReview, doc_id)
        assert review.artifact_version == 2
        assert hydrate_review_files(session, settings, review)
        assert (processing_review_paths(settings, doc_id, review.id) / "attachments" / "one.bin").read_bytes() == b"accepted attachment"


def test_legacy_ready_archives_download_after_cold_migration(corpus, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4
    from zipfile import ZipFile
    from app.db.models import Job, DiagnosticBundle
    from app.services.export_queue import ExportQueue
    from app.services.diagnostics.bundle_queue import DiagnosticBundleQueue
    from app.services.diagnostics.store import DiagnosticStore
    from app.services.diagnostics.schema import DiagnosticLimits
    from app.auth.models import User
    settings, root = corpus
    monkeypatch.setattr("app.services.export_queue.get_settings", lambda: settings)
    actor = User(user_id="operator", username="operator", roles=["admin"])
    with session_scope() as session:
        job = Job(job_type="bulk_export", status="completed", params={})
        session.add(job)
        session.flush()
        name = f"documents-export-{job.id}-part-1.zip"
        path = settings.exports_dir / f"{job.id}.ready" / name
        path.parent.mkdir(parents=True)
        with ZipFile(path, "w") as archive:
            archive.writestr("accepted.txt", "accepted archive")
        payload = path.read_bytes()
        job.result = {"artifact_status": "available", "expires_at": "2099-01-01T00:00:00+00:00", "total_bytes": len(payload),
                      "parts": [{"number": 1, "filename": name, "size_bytes": len(payload)}]}
        job_id = job.id
        bundle_id = str(uuid4())
        diagnostic = settings.diagnostics_dir / "bundles" / (bundle_id + ".zip")
        diagnostic.parent.mkdir(parents=True)
        diagnostic.write_bytes(payload)
        session.add(DiagnosticBundle(id=bundle_id, status="ready", created_by_id="operator", created_by="operator",
            cutoff_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(days=1), request={},
            size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()))
    task = move_task(settings, root, monkeypatch)
    proof = backup(task, root)
    task.copy(apply=True, writers_stopped=True, backup=proof)
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    switch_cache(task, root)
    lease = ExportQueue(start_worker=False).acquire_download(job_id, 1, actor, None)
    try:
        assert lease.path.read_bytes() == payload
    finally:
        lease.release()
    with DiagnosticStore(settings.diagnostics_dir, DiagnosticLimits(min_free_bytes=0)) as store:
        lease = DiagnosticBundleQueue(store).acquire_download(bundle_id, actor)
        assert b"".join(lease.chunks()) == payload


def test_accepted_legacy_export_executes_on_cold_worker_after_migration(corpus, monkeypatch):
    from app.services.export_queue import ExportQueue
    from app.auth.models import User
    from zipfile import ZipFile
    settings, root = corpus
    settings.bulk_export_min_free_mb = 0
    monkeypatch.setattr("app.services.export_queue.get_settings", lambda: settings)
    actor = User(user_id="operator", username="operator", roles=["admin"])
    queue = ExportQueue(start_worker=False)
    job = queue.submit(["0000000000000001"], actor, ip_address=None)["id"]
    task = move_task(settings, root, monkeypatch)
    proof = backup(task, root)
    task.copy(apply=True, writers_stopped=True, backup=proof)
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    switch_cache(task, root)
    queue._execute(job)
    assert queue.get(job)["status"] == "completed"
    lease = queue.acquire_download(job, 1, actor, None)
    try:
        with ZipFile(lease.path) as archive:
            assert any(archive.read(name) == b"accepted original 1" for name in archive.namelist())
    finally:
        lease.release()


def test_migration_preserves_document_timestamps(corpus):
    task = migration(corpus)
    bundle = corpus[0].okf_dir / "0000000000000001"
    bundle.mkdir(parents=True)
    (bundle / "accepted.md").write_text("accepted", encoding="utf8")
    with session_scope() as session:
        before = session.get(Document, "0000000000000001").updated_at
    proof = backup(task, corpus[1])
    task.copy(apply=True, writers_stopped=True, backup=proof)
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    with session_scope() as session:
        assert session.get(Document, "0000000000000001").updated_at == before


@pytest.mark.parametrize("point", ["copy", "cutover"])
def test_interrupted_sql_commit_resumes_without_partial_selection(corpus, point):
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from app.services.artifacts.control import migration_access
    task = migration(corpus)
    proof = backup(task, corpus[1])
    if point == "cutover":
        task.copy(apply=True, writers_stopped=True, backup=proof)
    def interrupt(session):
        if session.info.get("artifact_storage_" + point):
            raise OSError("interrupted SQL commit")
    event.listen(Session, "before_commit", interrupt)
    try:
        with pytest.raises(OSError, match="interrupted SQL commit"):
            getattr(task, point)(apply=True, writers_stopped=True, backup=proof)
    finally:
        event.remove(Session, "before_commit", interrupt)
    assert task.active_store_id() == "source"
    with migration_access(), session_scope() as session:
        assert session.query(ArtifactBinding).filter_by(owner_type="original").count() == 0
    task.copy(apply=True, writers_stopped=True, backup=proof)
    assert task.cutover(apply=True, writers_stopped=True, backup=proof)["cutover"]


def test_changed_source_refused_before_publishing_target_object(corpus):
    task = migration(corpus)
    proof = backup(task, corpus[1])
    (corpus[0].uploads_dir / "0000000000000001.docx").write_bytes(b"changed source")
    with pytest.raises(ValueError):
        task.copy(apply=True, writers_stopped=True, backup=proof)
    assert not list((task.stores["target"].root / "objects").iterdir())


def test_portable_store_can_migrate_again_without_reading_old_source_cache(corpus, monkeypatch):
    first = migration(corpus)
    proof = backup(first, corpus[1])
    first.copy(apply=True, writers_stopped=True, backup=proof)
    first.cutover(apply=True, writers_stopped=True, backup=proof)
    settings = switch_cache(first, corpus[1])
    settings = migrate_again(settings, corpus[1], monkeypatch)
    from app.services.artifacts.original import original_path
    assert original_path(settings, "0000000000000001", "1.docx").read_bytes() == b"accepted original 1"


def migrate_again(settings, root, monkeypatch):
    from app.services.artifacts.migration import StorageMigration, installation_id
    from app.services.artifacts.control import descriptor, enforce_runtime
    from app.services.artifacts.rwx import RwxBlobStore
    source = {"version": 1, "installation_id": installation_id(), "store": descriptor(settings)}
    destination = root / "second-target"
    destination.mkdir()
    target = {"version": 1, "installation_id": source["installation_id"], "store": {"backend": "rwx", "store_id": "next", "root": str(destination)}}
    stores = {"target": LocalBlobStore(settings.artifact_root, store_id="target"), "next": RwxBlobStore(destination, store_id="next")}
    def build(actual):
        enforce_runtime(actual)
        return stores[actual.artifact_store_id]
    monkeypatch.setattr("app.services.artifacts.get_blob_store", build)
    task = StorageMigration(settings, source, target, root / "second-transfer" / "plan.json", stores=stores)
    proof = backup(task, root)
    task.copy(apply=True, writers_stopped=True, backup=proof)
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    settings = switch_cache(task, root)
    settings.data_dir = root / "third-worker"
    settings.diagnostics_dir = root / "third-diagnostics"
    return settings


def test_portable_candidate_snapshot_is_not_replaced_from_partial_cache(corpus, monkeypatch):
    from app.db.models import DocumentGenerationState
    from app.services.artifacts.publication import hydrate_publication
    from app.services.generation_files import generation_paths
    test_legacy_checkpoint_and_candidate_sidecars_survive_migration(corpus, monkeypatch)
    settings, root = corpus
    doc_id = "0000000000000001"
    with session_scope() as session:
        generation = session.get(DocumentGenerationState, doc_id).candidate_generation_id
    paths = generation_paths(settings, doc_id, generation)
    (paths.bundle / "tables" / "rows.jsonl").unlink()
    (paths.bundle / "cache-only.txt").write_bytes(b"not authoritative")
    settings = migrate_again(settings, root, monkeypatch)
    assert hydrate_publication(settings, doc_id, generation)
    paths = generation_paths(settings, doc_id, generation)
    assert (paths.bundle / "tables" / "rows.jsonl").read_text(encoding="utf8") == '{"cells":[7,9]}\n'
    assert not (paths.bundle / "cache-only.txt").exists()


def test_snapshot_marker_downgrade_cannot_be_read_as_portable(corpus):
    from app.services.artifacts.legacy import snapshot_ref
    from app.services.generation_artifacts import GenerationIntegrityError
    task = migration(corpus)
    bundle = corpus[0].okf_dir / "0000000000000001"
    bundle.mkdir(parents=True)
    (bundle / "accepted.md").write_bytes(b"accepted")
    proof = backup(task, corpus[1])
    task.copy(apply=True, writers_stopped=True, backup=proof)
    task.cutover(apply=True, writers_stopped=True, backup=proof)
    switch_cache(task, corpus[1])
    with session_scope() as session:
        session.get(Document, "0000000000000001").legacy_artifacts_version = 1
    with session_scope() as session, pytest.raises(GenerationIntegrityError):
        snapshot_ref(session, "0000000000000001", None, "bundle/accepted.md")
