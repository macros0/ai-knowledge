"""Ready archives remain downloadable without the producing worker's disk."""
import shutil
from zipfile import ZipFile

import pytest
from sqlalchemy import select

from app.auth.models import User
from app.db.models import ArtifactBinding, ArtifactObject
from app.db.session import session_scope
from app.services.artifacts import get_blob_store
from app.services.artifacts.repository import attach_blob
from app.services.export_queue import ExportQueue
from app.services.work.models import WorkSpec, LostOwnership
from app.services.work.store import enqueue, claim
from tests.test_export_queue import _settings, _document

DOC = "0123456789abcdef"
ACTOR = User(user_id="export-admin", username="admin", roles=["admin"])


@pytest.fixture
def portable_exports(tmp_path, monkeypatch):
    from uuid import uuid4
    settings = _settings(tmp_path / "worker-a")
    settings.artifact_root = tmp_path / "durable"
    settings.artifact_uploads_enabled = True
    monkeypatch.setattr("app.services.export_queue.get_settings", lambda: settings)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    _document(settings, DOC, "source.docx", b"approved original source")
    with (settings.uploads_dir / (DOC + ".docx")).open("rb") as stream:
        ref = get_blob_store(settings).put(stream, object_id=uuid4().hex, media_type="application/octet-stream")
    with session_scope() as session:
        attach_blob(session, ref=ref, owner_type="original", owner_id=DOC, generation_id=None, logical_name="original")
    service = ExportQueue(start_worker=False)
    job = service.submit([DOC], ACTOR, ip_address=None)
    return service, settings, job["id"]


def cold(settings, tmp_path):
    shutil.rmtree(settings.data_dir)
    settings.data_dir = tmp_path / "worker-b"


def test_future_export_admission_persists_work_without_starting_local_queue(portable_exports):
    from app.db.models import WorkItem
    service, settings, _ = portable_exports
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    before = service._queue.qsize()
    actor = User(user_id="another-admin", username="other", roles=["admin"])
    job = service.submit([DOC], actor, ip_address=None)
    with session_scope() as session:
        work = session.query(WorkItem).filter_by(kind="export").one()
        assert work.payload_json == {"job_id": job["id"]}
    assert service._queue.qsize() == before


def test_transient_export_storage_failure_retries_without_closing_public_job(portable_exports, monkeypatch):
    from threading import Event
    from app.db.models import WorkItem
    from app.services.artifacts.contract import BlobUnavailable
    from app.services.work.runtime import build_handlers
    from app.services.work.worker import Worker
    import app.services.export_queue as module
    service, settings, job_id = portable_exports
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "worker"
    monkeypatch.setattr(module, "get_export_queue", lambda: service)
    with session_scope() as session:
        work_id = enqueue(session, WorkSpec("export", 1, {"job_id": job_id}, "export-transient"))
    build = module.build_export_parts
    def offline(*_a, **_k):
        raise BlobUnavailable("temporary storage outage")
    monkeypatch.setattr(module, "build_export_parts", offline)
    runner = Worker(settings, registry=build_handlers(), owner_id="export-retry")
    assert runner.run_once(Event())
    with session_scope() as session:
        row = session.get(WorkItem, work_id)
        assert row.status == "queued"
        row.available_at = row.created_at
    assert service.get(job_id)["status"] == "running"
    monkeypatch.setattr(module, "build_export_parts", build)
    assert runner.run_once(Event())
    assert service.get(job_id)["status"] == "completed"


def test_export_maintenance_reconciles_a_failed_work_receipt(portable_exports, monkeypatch):
    from app.db.models import Job, WorkItem
    from app.services.work.runtime import build_handlers
    service, settings, job_id = portable_exports
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "worker"
    monkeypatch.setattr("app.services.export_queue.get_export_queue", lambda: service)
    with session_scope() as session:
        parent = enqueue(session, WorkSpec("export", 1, {"job_id": job_id}, "export-failed-receipt"))
        session.get(WorkItem, parent).status = "failed"
        session.get(Job, job_id).status = "running"
        enqueue(session, WorkSpec("export_purge", 1, {}, "export-failure-maintenance"))
        work = claim(session, owner_id="export-maintenance", supported={"export_purge": {1}}, lease_seconds=120)
    build_handlers().resolve(work.spec)(work)
    assert service.get(job_id)["status"] == "failed"


def test_export_resume_after_worker_loss(portable_exports, tmp_path, monkeypatch):
    from app.db.models import WorkItem
    from app.services.work.runtime import build_handlers
    from tests.test_work_store import expire
    service, settings, job_id = portable_exports
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "worker"
    monkeypatch.setattr("app.services.export_queue.get_export_queue", lambda: service)
    with session_scope() as session:
        work_id = enqueue(session, WorkSpec("export", 1, {"job_id": job_id}, "export-loss"))
        old = claim(session, owner_id="old", supported={"export": {1}}, lease_seconds=120)
    import app.services.export_queue as module
    original_build, replacement = module.build_export_parts, []
    def old_returns(*args, **kwargs):
        parts = original_build(*args, **kwargs)
        from threading import Thread
        def reclaim():
            expire(work_id)
            with session_scope() as session:
                replacement.append(claim(session, owner_id="new", supported={"export": {1}}, lease_seconds=120))
        other = Thread(target=reclaim)
        other.start()
        other.join(5)
        assert not other.is_alive() and len(replacement) == 1
        return parts
    monkeypatch.setattr(module, "build_export_parts", old_returns)
    with pytest.raises(LostOwnership):
        build_handlers().resolve(old.spec)(old)
    assert service.get(job_id)["status"] == "running"
    monkeypatch.setattr(module, "build_export_parts", original_build)
    cold(settings, tmp_path)
    build_handlers().resolve(replacement[0].spec)(replacement[0])
    with session_scope() as session:
        assert session.get(WorkItem, work_id).status == "succeeded"
    assert service.get(job_id)["status"] == "completed"
    lease = service.acquire_download(job_id, 1, ACTOR, None)
    try:
        with ZipFile(lease.path) as archive:
            assert archive.testzip() is None
            assert any(archive.read(name) == b"approved original source" for name in archive.namelist())
    finally:
        lease.release()


def test_export_retention_work_preserves_another_instances_reader(portable_exports, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.db.models import Job
    from app.services.work.runtime import build_handlers
    from app.services.work.progress import load_progress
    service, settings, job_id = portable_exports
    service._execute(job_id)
    lease = service.acquire_download(job_id, 1, ACTOR, None)
    try:
        settings.okf_deployment_schema, settings.app_role = "replicas-v1", "worker"
        other = ExportQueue(start_worker=False)
        monkeypatch.setattr("app.services.export_queue.get_export_queue", lambda: other)
        with session_scope() as session:
            job = session.get(Job, job_id)
            job.result = dict(job.result, expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
            enqueue(session, WorkSpec("export_purge", 1, {}, "export-purge"))
            work = claim(session, owner_id="purge", supported={"export_purge": {1}}, lease_seconds=120)
        build_handlers().resolve(work.spec)(work)
        assert other.get(job_id)["result"]["artifact_status"] == "expired"
        with ZipFile(lease.path) as archive:
            assert archive.testzip() is None
        with session_scope() as session:
            assert load_progress(session, work, version=1)["last_id"] == job_id
    finally:
        lease.release()


def test_queued_export_builds_from_bound_original_on_cold_worker(portable_exports, tmp_path):
    service, settings, job = portable_exports
    cold(settings, tmp_path)
    service._execute(job)
    assert service.get(job)["status"] == "completed"
    lease = service.acquire_download(job, 1, ACTOR, None)
    try:
        with ZipFile(lease.path) as archive:
            assert any(archive.read(name) == b"approved original source" for name in archive.namelist())
    finally:
        lease.release()


def test_completed_export_download_survives_worker_disk_loss(portable_exports, tmp_path):
    service, settings, job = portable_exports
    service._execute(job)
    assert service.get(job)["status"] == "completed"
    cold(settings, tmp_path)
    lease = ExportQueue(start_worker=False).acquire_download(job, 1, ACTOR, None)
    try:
        assert lease.path.is_file()
        with ZipFile(lease.path) as archive:
            assert archive.testzip() is None
    finally:
        lease.release()
    assert not lease.path.exists()


def test_retiring_portable_export_preserves_already_materialized_reader(portable_exports, tmp_path):
    from app.services.artifacts.lifecycle import collect_blob
    service, settings, job = portable_exports
    service._execute(job)
    with session_scope() as session:
        ids = list(session.scalars(select(ArtifactBinding.object_id).where(ArtifactBinding.owner_type == "export")))
    assert ids
    lease = service.acquire_download(job, 1, ACTOR, None)
    try:
        other = ExportQueue(start_worker=False)
        assert other.delete_artifacts(job, ACTOR, None)["result"]["artifact_status"] == "deleted"
        assert all(collect_blob(get_blob_store(settings), oid, grace_seconds=0) for oid in ids)
        with ZipFile(lease.path) as archive:
            assert archive.testzip() is None
    finally:
        lease.release()


def test_missing_archive_blob_cannot_fall_back_to_ready_directory(portable_exports):
    from app.services.export_queue import ExportAdmissionError
    service, settings, job = portable_exports
    service._execute(job)
    with session_scope() as session:
        oid = session.scalar(select(ArtifactBinding.object_id).where(ArtifactBinding.owner_type == "export"))
        assert oid is not None
        key = session.get(ArtifactObject, oid).key
    (settings.artifact_root / key).unlink()
    with pytest.raises(ExportAdmissionError):
        service.acquire_download(job, 1, ACTOR, None)


def test_portable_export_http_disconnect_releases_private_scratch(portable_exports, monkeypatch):
    import asyncio
    from starlette.requests import Request
    from app.api import jobs
    service, settings, job = portable_exports
    service._execute(job)
    monkeypatch.setattr(jobs, "get_export_queue", lambda: service)
    monkeypatch.setattr(jobs, "get_settings", lambda: settings)
    response = jobs.download_export_part(job, 1, Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)}), ACTOR)
    path = response.path
    async def receive():
        return {"type": "http.disconnect"}
    async def send(_message):
        raise RuntimeError("disconnected")
    with pytest.raises(RuntimeError, match="disconnected"):
        asyncio.run(response({"type": "http", "method": "GET", "headers": [], "asgi": {"spec_version": "2.4"}}, receive, send))
    assert service.active_leases(job) == 0
    assert not path.exists()


def test_portable_export_if_range_survives_private_cache_replacement(portable_exports, monkeypatch, tmp_path):
    import asyncio
    from starlette.requests import Request
    from app.api import jobs
    service, settings, job = portable_exports
    service._execute(job)
    monkeypatch.setattr(jobs, "get_export_queue", lambda: service)
    monkeypatch.setattr(jobs, "get_settings", lambda: settings)
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
    async def deliver(response, headers):
        messages = []
        async def receive():
            return {"type": "http.disconnect"}
        async def send(message):
            messages.append(message)
        await response({"type": "http", "method": "GET", "headers": headers, "asgi": {"spec_version": "2.4"}}, receive, send)
        return messages
    first = jobs.download_export_part(job, 1, request, ACTOR)
    etag = first.headers["etag"]
    original = asyncio.run(deliver(first, []))
    cold(settings, tmp_path)
    second = jobs.download_export_part(job, 1, request, ACTOR)
    second_etag = second.headers["etag"]
    ranged = asyncio.run(deliver(second, [(b"range", b"bytes=0-3"), (b"if-range", etag.encode())]))
    assert second_etag == etag
    assert ranged[0]["status"] == 206
    assert b"".join(item.get("body", b"") for item in ranged) == b"".join(item.get("body", b"") for item in original)[:4]
    assert service.active_leases(job) == 0


def test_export_audit_commit_failure_is_classified_and_releases_scratch(portable_exports, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from app.services import audit
    from app.services.export_queue import ExportAuditUnavailableError
    service, settings, job = portable_exports
    service._execute(job)
    record = audit.record_in_session
    def record_download(session, **kwargs):
        record(session, **kwargs)
        if kwargs.get("action_type") == audit.DOCUMENT_BULK_EXPORT_DOWNLOAD_STARTED:
            session.info["fail_download_commit"] = True
    def fail(session):
        if session.info.get("fail_download_commit"):
            raise OSError("audit commit interrupted")
    monkeypatch.setattr(audit, "record_in_session", record_download)
    event.listen(Session, "before_commit", fail)
    try:
        with pytest.raises(ExportAuditUnavailableError):
            service.acquire_download(job, 1, ACTOR, None)
    finally:
        event.remove(Session, "before_commit", fail)
    assert service.active_leases(job) == 0
    assert not list((settings.data_dir / "artifact-scratch").glob("artifact-*"))


def test_queued_export_pins_original_until_job_finishes_or_fails(portable_exports):
    from app.services.artifacts.lifecycle import collect_blob
    from app.services.registry import DocumentRegistry
    service, settings, job = portable_exports
    with session_scope() as session:
        ref = session.scalar(select(ArtifactBinding.object_id).where(ArtifactBinding.owner_type == "original"))
    assert DocumentRegistry().delete(DOC)
    assert not collect_blob(get_blob_store(settings), ref, grace_seconds=0)
    service._execute(job)
    assert service.get(job)["status"] == "failed"
    assert collect_blob(get_blob_store(settings), ref, grace_seconds=0)


def test_portable_export_rollback_retires_refs_without_local_ready_directory(portable_exports, tmp_path):
    service, settings, job = portable_exports
    service._execute(job)
    cold(settings, tmp_path)
    plan = service.plan_rollback(include_ready=True)
    assert plan.artifact_bytes == service.get(job)["result"]["total_bytes"]
    result = service.apply_rollback(plan, actor=ACTOR)
    assert not result.errors
    assert service.get(job)["result"]["artifact_status"] == "deleted"
    with session_scope() as session:
        assert not list(session.scalars(select(ArtifactBinding).where(ArtifactBinding.owner_type == "export")))


def test_consumer_schema_downgrade_refuses_ready_portable_export(portable_exports, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from alembic import command
    from alembic.config import Config
    from app.db.session import get_engine
    service, _, job = portable_exports
    service._execute(job)
    engine = get_engine()
    if engine.dialect.name == "sqlite":
        url = "sqlite:///" + Path(engine.url.database).as_posix()
    else:
        from sqlalchemy import text
        with session_scope() as session:
            schema = session.scalar(text("select current_schema()"))
        assert schema.startswith("artifact_") and len(schema) == 41
        monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=10000")
        url = engine.url.set(query={}).render_as_string(hide_password=False)
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(db_url=url))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl01f_file_consumers")
    with pytest.raises(RuntimeError, match="portable export"):
        command.downgrade(config, "rpl01e_source_caches")


def test_portable_export_checks_scratch_space_before_provider_access(portable_exports, monkeypatch):
    from types import SimpleNamespace
    from app.services.export_queue import ExportAdmissionError
    service, _, job = portable_exports
    service._execute(job)
    monkeypatch.setattr("app.services.export_queue.shutil.disk_usage", lambda *_a: SimpleNamespace(free=0))
    monkeypatch.setattr("app.services.artifacts.get_blob_store", lambda *_a: pytest.fail("provider read before scratch admission"))
    with pytest.raises(ExportAdmissionError) as error:
        service.acquire_download(job, 1, ACTOR, None)
    assert error.value.code == "storage_full"


@pytest.fixture
def portable_diagnostics(tmp_path, monkeypatch):
    from app.config import Settings
    from app.services.diagnostics.bundle_queue import DiagnosticBundleQueue
    from app.services.diagnostics.schema import DiagnosticLimits
    from app.services.diagnostics.store import DiagnosticStore
    from app.models.diagnostics import BundleRequest
    settings = Settings(_env_file=None, data_dir=tmp_path / "worker-a", artifact_root=tmp_path / "durable",
                        artifact_uploads_enabled=True)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    store = DiagnosticStore(settings.data_dir / "spool", DiagnosticLimits(min_free_bytes=0))
    store.open()
    queue = DiagnosticBundleQueue(store)
    submitted = queue.submit(BundleRequest(), ACTOR)
    queue.start()
    assert queue.wait_idle(25)
    queue.shutdown()
    yield queue, settings, submitted.id
    store.close()


def test_diagnostic_archive_downloads_on_another_api_replica(portable_diagnostics, tmp_path):
    from io import BytesIO
    from app.services.diagnostics.bundle_queue import DiagnosticBundleQueue
    from app.services.diagnostics.schema import DiagnosticLimits
    from app.services.diagnostics.store import DiagnosticStore
    queue, settings, bundle_id = portable_diagnostics
    queue.store.close()
    shutil.rmtree(settings.data_dir)
    settings.data_dir = tmp_path / "worker-b"
    with DiagnosticStore(settings.data_dir / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        lease = DiagnosticBundleQueue(store).acquire_download(bundle_id, ACTOR)
        payload = b"".join(lease.chunks())
        with ZipFile(BytesIO(payload)) as archive:
            assert archive.testzip() is None
        assert lease.released


def test_diagnostic_retention_detaches_blobs_and_preserves_open_reader(portable_diagnostics):
    from io import BytesIO
    from app.services.artifacts.lifecycle import collect_blob
    queue, settings, bundle_id = portable_diagnostics
    with session_scope() as session:
        ids = list(session.scalars(select(ArtifactBinding.object_id).where(ArtifactBinding.owner_type == "diagnostic_bundle")))
    assert ids
    lease = queue.acquire_download(bundle_id, ACTOR)
    assert queue.delete(bundle_id, ACTOR).status == "deleted"
    assert all(collect_blob(get_blob_store(settings), oid, grace_seconds=0) for oid in ids)
    with ZipFile(BytesIO(b"".join(lease.chunks()))) as archive:
        assert archive.testzip() is None


def test_diagnostics_permission_denied_before_blob_access(portable_diagnostics, monkeypatch):
    from app.services.diagnostics.bundle_queue import BundleQueueError
    queue, _, bundle_id = portable_diagnostics
    monkeypatch.setattr("app.services.artifacts.get_blob_store", lambda *_a: pytest.fail("provider accessed before role check"))
    with pytest.raises(BundleQueueError) as error:
        queue.acquire_download(bundle_id, User(user_id="viewer", username="viewer", roles=["viewer"]))
    assert error.value.code == "forbidden"


def test_portable_diagnostic_download_reserves_and_releases_scratch_budget(portable_diagnostics):
    queue, _, bundle_id = portable_diagnostics
    lease = queue.acquire_download(bundle_id, ACTOR)
    try:
        assert queue.store.reserved_bytes >= lease.size_bytes
    finally:
        lease.release()
    assert queue.store.reserved_bytes == 0


def test_diagnostic_reader_bookkeeping_survives_scratch_cleanup_failure(portable_diagnostics):
    queue, _, bundle_id = portable_diagnostics
    lease = queue.acquire_download(bundle_id, ACTOR)
    def fail_cleanup():
        raise OSError("cleanup failed")
    lease.artifact_context.callback(fail_cleanup)
    with pytest.raises(OSError, match="cleanup failed"):
        lease.release()
    assert bundle_id not in queue._downloads
    assert queue.store.reserved_bytes == 0


def test_portable_diagnostic_download_cannot_bypass_spool_quota(portable_diagnostics, monkeypatch):
    from app.services.diagnostics.bundle_queue import BundleQueueError
    queue, _, bundle_id = portable_diagnostics
    credit = queue.store.reserve(queue.store.limits.backend_bytes - queue.store.status()["used_bytes"] - 1)
    monkeypatch.setattr("app.services.artifacts.get_blob_store", lambda *_a: pytest.fail("provider accessed before quota admission"))
    try:
        with pytest.raises(BundleQueueError) as error:
            queue.acquire_download(bundle_id, ACTOR)
        assert error.value.code == "diagnostic_storage_low"
    finally:
        credit.release()
