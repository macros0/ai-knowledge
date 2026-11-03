"""Diagnostic requests survive API loss; only an owned claim grants readiness."""
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from zipfile import ZipFile

import pytest

from app.config import Settings
from app.db.models import DiagnosticBundle, WorkItem
from app.db.session import session_scope
from app.models.diagnostics import BundleRequest
from app.services.diagnostics.bundle_queue import DiagnosticBundleQueue, BundleQueueError
from app.services.diagnostics.schema import DiagnosticLimits
from app.services.diagnostics.store import DiagnosticStore
from app.services.work.models import LostOwnership
from app.services.work.store import claim
from tests.test_diagnostics_bundle_queue import actor
from tests.test_work_store import expire


@pytest.fixture
def durable_bundle(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path / "api", artifact_root=tmp_path / "durable",
                        artifact_uploads_enabled=True)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    with DiagnosticStore(settings.data_dir / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        yield DiagnosticBundleQueue(store), settings


def bundle_claim():
    with session_scope() as session:
        return claim(session, owner_id=uuid4().hex, supported={"diagnostic_bundle": {1}}, lease_seconds=120)


def test_bundle_admission_is_durable_without_local_queue(durable_bundle):
    queue, _ = durable_bundle
    result = queue.submit(BundleRequest(), actor())
    assert not queue._pending and not queue._reservations and queue.store.reserved_bytes == 0
    with session_scope() as session:
        work = session.query(WorkItem).filter_by(kind="diagnostic_bundle").one()
        assert work.payload_json == {"bundle_id": result.id} and work.status == "queued"


def test_bundle_handler_is_registered():
    from app.services.work.runtime import build_handlers
    assert build_handlers().supported["diagnostic_bundle"] == {1, 2}


def test_bundle_builder_is_serialized_across_claims(durable_bundle):
    from app.auth.models import User
    from app.services.work.handlers import WorkDeferred
    queue, _ = durable_bundle
    first = queue.submit(BundleRequest(), actor())
    second = queue.submit(BundleRequest(), User(user_id="other", username="Other", roles=["admin"]))
    one, two = bundle_claim(), bundle_claim()
    with session_scope() as session:
        session.get(DiagnosticBundle, first.id).status = "building"
    assert queue.execute_work(two) == WorkDeferred(1)
    with session_scope() as session:
        assert session.get(DiagnosticBundle, second.id).status == "queued"
    queue.execute_work(one)
    queue.execute_work(two)
    with session_scope() as session:
        assert session.get(DiagnosticBundle, second.id).status == "ready"


def test_bundle_audit_failure_rolls_back_work_and_public_record(durable_bundle, monkeypatch):
    queue, _ = durable_bundle
    def fail(*_a, **_k):
        raise OSError("audit unavailable")
    monkeypatch.setattr("app.services.audit.record_in_session", fail)
    with pytest.raises(BundleQueueError):
        queue.submit(BundleRequest(), actor())
    with session_scope() as session:
        assert session.query(DiagnosticBundle).count() == session.query(WorkItem).count() == 0


def test_legacy_bundle_startup_cannot_reset_or_dispatch_durable_requests(durable_bundle):
    queue, _ = durable_bundle
    result = queue.submit(BundleRequest(), actor())
    queue.recover()
    queue.start()
    try:
        assert not queue._running and queue._thread is None
        with session_scope() as session:
            assert session.get(DiagnosticBundle, result.id).status == "queued"
    finally:
        queue.shutdown()


def test_bundle_late_builder_cannot_publish_and_cold_worker_can(durable_bundle, tmp_path, monkeypatch):
    queue, settings = durable_bundle
    result = queue.submit(BundleRequest(), actor())
    old = bundle_claim()
    real_build = queue._build_isolated
    current = None
    def supersede(*args):
        nonlocal current
        built = real_build(*args)
        def replacement_claim():
            expire(old.work_id)
            return bundle_claim()
        with ThreadPoolExecutor(max_workers=1) as executor:
            current = executor.submit(replacement_claim).result(timeout=10)
        return built
    monkeypatch.setattr(queue, "_build_isolated", supersede)
    with pytest.raises(LostOwnership):
        queue.execute_work(old)
    with session_scope() as session:
        assert session.get(DiagnosticBundle, result.id).status == "building"
        assert session.get(WorkItem, old.work_id).fence == current.fence
    settings.data_dir = tmp_path / "worker-b"
    with DiagnosticStore(settings.data_dir / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        replacement = DiagnosticBundleQueue(store)
        replacement.execute_work(current)
    with session_scope() as session:
        assert session.get(DiagnosticBundle, result.id).artifact_version == 2
        assert session.get(WorkItem, old.work_id).status == "succeeded"
    settings.data_dir = tmp_path / "api-c"
    with DiagnosticStore(settings.data_dir / "spool", DiagnosticLimits(min_free_bytes=0)) as store:
        reader = DiagnosticBundleQueue(store)
        lease = reader.acquire_download(result.id, actor())
        with ZipFile(BytesIO(b"".join(lease.chunks()))) as archive:
            assert archive.testzip() is None


def test_diagnostic_retention_retires_bindings_without_breaking_a_reader(durable_bundle):
    from datetime import timedelta
    from app.db.models import ArtifactBinding
    from app.services.work.models import WorkSpec
    from app.services.work.store import enqueue
    from app.services.work.runtime import build_handlers
    queue, _ = durable_bundle
    result = queue.submit(BundleRequest(), actor())
    queue.execute_work(bundle_claim())
    lease = queue.acquire_download(result.id, actor())
    with session_scope() as session:
        row = session.get(DiagnosticBundle, result.id)
        row.expires_at = row.created_at - timedelta(seconds=1)
        enqueue(session, WorkSpec("diagnostic_maintenance", 1, {}, "diagnostic-retention"))
        work = claim(session, owner_id="diagnostic-retention", supported={"diagnostic_maintenance": {1}}, lease_seconds=120)
    build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        assert session.get(DiagnosticBundle, result.id).status == "expired"
        assert session.query(ArtifactBinding).filter_by(owner_type="diagnostic_bundle", owner_id=result.id).count() == 0
    with ZipFile(BytesIO(b"".join(lease.chunks()))) as archive:
        assert archive.testzip() is None


def test_diagnostic_maintenance_projects_a_terminal_failed_work(durable_bundle):
    from app.services.work.models import WorkSpec
    from app.services.work.store import enqueue
    from app.services.work.runtime import build_handlers
    queue, _ = durable_bundle
    result = queue.submit(BundleRequest(), actor())
    with session_scope() as session:
        session.query(WorkItem).filter_by(kind="diagnostic_bundle").one().status = "failed"
        enqueue(session, WorkSpec("diagnostic_maintenance", 1, {}, "diagnostic-reconcile"))
        work = claim(session, owner_id="diagnostic-maintenance", supported={"diagnostic_maintenance": {1}}, lease_seconds=120)
    build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        row = session.get(DiagnosticBundle, result.id)
        assert row.status == "failed" and row.error_code == "diagnostic_bundle_failed"


def test_diagnostic_metadata_pruning_preserves_pending_receipts(durable_bundle):
    from datetime import timedelta
    from app.services.work.models import WorkSpec
    from app.services.work.store import enqueue
    from app.services.work.runtime import build_handlers
    queue, _ = durable_bundle
    result = queue.submit(BundleRequest(), actor())
    with session_scope() as session:
        row = session.get(DiagnosticBundle, result.id)
        row.status, row.artifact_version = "failed", 2
        row.finished_at = row.created_at - timedelta(days=8)
        row.audit_receipts = ["pending"]
        enqueue(session, WorkSpec("diagnostic_maintenance", 1, {}, "diagnostic-prune"))
        work = claim(session, owner_id="diagnostic-prune", supported={"diagnostic_maintenance": {1}}, lease_seconds=120)
    handler = build_handlers().resolve(work.spec)
    handler(work)
    with session_scope() as session:
        assert session.get(DiagnosticBundle, result.id) is not None
        session.get(DiagnosticBundle, result.id).audit_receipts = []
    # A later maintenance slot scans again after the receipt was accepted.
    with session_scope() as session:
        enqueue(session, WorkSpec("diagnostic_maintenance", 1, {}, "diagnostic-prune-next"))
        current = claim(session, owner_id="diagnostic-prune-next", supported={"diagnostic_maintenance": {1}}, lease_seconds=120)
    handler(current)
    with session_scope() as session:
        assert session.get(DiagnosticBundle, result.id) is None
