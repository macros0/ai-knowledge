"""Bundle publication is audited, rate-limited and crash-safe."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.auth.models import User
from app.db.models import AuditLog, DiagnosticBundle
from app.db.session import session_scope
from app.models.diagnostics import BundleRequest
from app.services import audit
from app.services.diagnostics.bundle_queue import BundleQueueError, DiagnosticBundleQueue
from app.services.diagnostics.bundle import BundleTooLarge
from app.services.diagnostics.schema import DiagnosticLimits
from app.services.diagnostics.store import DiagnosticStore


def actor():
    return User(user_id="admin-1", username="Administrator", roles=["admin"])


@pytest.fixture
def queue(tmp_path):
    store = DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0))
    with store:
        yield DiagnosticBundleQueue(store)


def request():
    return BundleRequest()


def test_requested_is_committed_with_audit_before_admission(queue, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("CANARY_PRIVATE")
    monkeypatch.setattr(audit, "record_in_session", fail)
    with pytest.raises(BundleQueueError) as error:
        queue.submit(request(), actor())
    assert error.value.code == "diagnostic_audit_unavailable"
    assert queue.store.reserved_bytes == 0
    with session_scope() as db:
        assert list(db.scalars(select(DiagnosticBundle))) == []


def test_bounded_queue_and_durable_rate_limit(queue):
    first = queue.submit(request(), actor())
    second_actor = User(user_id="admin-2", username="Second admin", roles=["admin"])
    third_actor = User(user_id="admin-3", username="Third admin", roles=["admin"])
    second = queue.submit(request(), second_actor)
    assert first.status == second.status == "queued"
    with pytest.raises(BundleQueueError) as error:
        queue.submit(request(), third_actor)
    assert error.value.code == "diagnostic_bundle_busy"
    with session_scope() as db:
        for row in db.scalars(select(DiagnosticBundle)):
            row.status = "failed"
    third = queue.submit(request(), actor())
    assert third.status == "queued"
    with session_scope() as db:
        db.get(DiagnosticBundle, third.id).status = "failed"
    fourth = queue.submit(request(), actor())
    assert fourth.status == "queued"
    with session_scope() as db:
        db.get(DiagnosticBundle, fourth.id).status = "failed"
    with pytest.raises(BundleQueueError) as error:
        queue.submit(request(), actor())
    assert error.value.code == "diagnostic_bundle_rate_limited"


def test_same_admin_cannot_queue_a_second_unfinished_bundle(queue):
    first = queue.submit(request(), actor())
    with pytest.raises(BundleQueueError) as error:
        queue.submit(request(), actor())
    assert error.value.code == "diagnostic_bundle_busy"
    with session_scope() as db:
        assert db.get(DiagnosticBundle, first.id).status == "queued"
        assert len(list(db.scalars(select(DiagnosticBundle)))) == 1


def test_narrow_request_is_admitted_when_unrelated_spool_is_large(queue):
    source = queue.store.root / "events" / "baseline" / "0000000000000-00000000000000000000000000000000.jsonl"
    with source.open("wb") as handle:
        handle.truncate(90 * 1048576)
    now = datetime.now(timezone.utc)
    submitted = queue.submit(BundleRequest(from_utc=now - timedelta(seconds=1), to_utc=now), actor())
    assert submitted.status == "queued"


def test_download_chunks_stream_complete_without_absolute_deadline():
    from io import BytesIO
    from app.services.diagnostics.bundle_queue import DownloadLease

    class Queue:
        def _release_download(self, lease):
            lease.released = True
            lease.handle.close()

    lease = DownloadLease(Queue(), str(uuid4()), BytesIO(b"a" * 200000), "bundle.zip", 200000)
    assert not hasattr(lease, "deadline")
    assert len(b"".join(lease.chunks())) == 200000
    assert lease.released


def test_worker_publishes_only_verified_zip(queue):
    submitted = queue.submit(request(), actor())
    queue.start()
    assert queue.wait_idle(10)
    with session_scope() as db:
        row = db.get(DiagnosticBundle, submitted.id)
        assert row.status == "ready"
        assert row.size_bytes > 0 and len(row.sha256) == 64
        actions = list(db.scalars(select(AuditLog.action_type).where(AuditLog.target_id == submitted.id)))
        assert "diagnostic_bundle_requested" in actions
        assert "diagnostic_bundle_ready" in actions
    assert (queue.store.root / "bundles" / (submitted.id + ".zip")).exists()
    assert not (queue.store.root / "bundles" / (submitted.id + ".part")).exists()
    queue.shutdown()


def test_queue_builds_zip_outside_backend_process(queue, monkeypatch):
    import app.services.diagnostics.bundle_queue as module
    from zipfile import ZipFile

    submitted = queue.submit(request(), actor())

    def in_process_build(*args, **kwargs):
        raise AssertionError("ZIP assembly ran in the backend process")

    monkeypatch.setattr(module, "build_bundle", in_process_build, raising=False)
    queue.start()
    assert queue.wait_idle(15)
    with session_scope() as db:
        row = db.get(DiagnosticBundle, submitted.id)
        assert row.status == "ready"
        assert row.size_bytes > 0
    path = queue.store.root / "bundles" / (submitted.id + ".zip")
    with ZipFile(path) as archive:
        assert archive.testzip() is None
    assert queue.store.reserved_bytes == 0
    queue.shutdown()


def test_shutdown_terminates_active_builder_without_publishing(queue, monkeypatch):
    import subprocess
    import sys
    import threading
    import app.services.diagnostics.bundle_queue as module

    submitted = queue.submit(request(), actor())
    spawned = threading.Event()
    real_popen = subprocess.Popen

    def sleeping_builder(_command, **options):
        child = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **options)
        spawned.set()
        return child

    monkeypatch.setattr(module.subprocess, "Popen", sleeping_builder)
    queue.start()
    assert spawned.wait(10)
    with queue._condition:
        assert queue._child is not None
    assert queue.store.reserved_bytes == 0  # Prepare child stalled before requesting credit.
    queue.shutdown(timeout_seconds=5)
    assert queue.wait_idle(10)
    with session_scope() as db:
        row = db.get(DiagnosticBundle, submitted.id)
        assert row.status == "failed"
        assert row.sha256 is None
    assert not (queue.store.root / "bundles" / (submitted.id + ".zip")).exists()
    assert not (queue.store.root / "bundles" / (submitted.id + ".part")).exists()
    assert queue.store.reserved_bytes == 0


@pytest.mark.parametrize("termination", ["kill", "shutdown"])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_active_prepare_after_write_releases_pins_and_credits(queue, monkeypatch, termination, cleanup_failure):
    """Terminate a real child after it wrote credited bytes, before commit."""
    import threading
    import app.services.diagnostics.prepare as prepare_module
    from app.services.diagnostics.sanitize import encode_event

    now = datetime.now(timezone.utc).isoformat()
    boot_id = str(uuid4())
    for _ in range(700):
        assert queue.store.append(encode_event({
            "schema_version": 1, "event_id": str(uuid4()), "boot_id": boot_id,
            "timestamp_utc": now, "component": "backend", "origin": "server",
            "level": "ERROR", "event_code": "operation_failed", "error_code": "internal_error",
        }), stream="baseline")
    written = threading.Event()
    proceed = threading.Event()
    writer_reserved = queue.store.reserved_bytes
    writer_reservations = set(queue.store._reservations)
    real_commit = prepare_module.QuotaBroker.commit
    real_delete = queue.store.delete_tree

    def delete_tree(path):
        if cleanup_failure and Path(path).parent == queue.store.root / "snapshots":
            raise PermissionError("Synthetic cleanup denial")
        return real_delete(path)

    monkeypatch.setattr(queue.store, "delete_tree", delete_tree)

    def paused_commit(broker, grant_id, actual_bytes):
        if not written.is_set():
            assert actual_bytes > 0
            written.set()
            assert proceed.wait(15), "Test did not release the worker checkpoint"
            if cleanup_failure:
                raise prepare_module.FrameError("Synthetic termination before commit")
        return real_commit(broker, grant_id, actual_bytes)

    monkeypatch.setattr(prepare_module.QuotaBroker, "commit", paused_commit)
    submitted = queue.submit(request(), actor())
    queue.start()
    try:
        assert written.wait(15), "Real prepare child did not write its first credited block"
        child = queue._child
        assert child is not None and child.poll() is None
        assert queue.store._pins
        assert queue.store.reserved_bytes > writer_reserved
        assert any(path.stat().st_size > 0 for path in
                   (queue.store.root / "snapshots" / submitted.id).glob("*.jsonl"))
        if termination == "kill":
            child.kill()
        else:
            queue.shutdown(timeout_seconds=0.1)
        child.wait(timeout=5)
        proceed.set()
        assert queue.wait_idle(10)
        with session_scope() as db:
            row = db.get(DiagnosticBundle, submitted.id)
            assert row.status == "failed"
            assert row.sha256 is None
        assert queue.store.reserved_bytes == writer_reserved
        assert set(queue.store._reservations) == writer_reservations
        assert not queue.store._pins
        directory = queue.store.root / "snapshots" / submitted.id
        if cleanup_failure:
            assert directory.exists()
            for path in directory.glob("*.jsonl"):
                assert queue.store._index.size(path) == path.stat().st_size
            real_delete(directory)
        else:
            assert not directory.exists()
        assert not (queue.store.root / "bundles" / (submitted.id + ".part")).exists()
        assert not (queue.store.root / "bundles" / (submitted.id + ".zip")).exists()
    finally:
        proceed.set()
        queue.shutdown()


@pytest.mark.parametrize("termination", ["kill", "shutdown"])
def test_active_zip_after_compressed_write_releases_resources(queue, monkeypatch, termination):
    import subprocess
    import sys
    import time
    import app.services.diagnostics.bundle_queue as module
    from app.services.diagnostics.sanitize import encode_event

    assert queue.store.append(encode_event({
        "schema_version": 1, "event_id": str(uuid4()), "boot_id": str(uuid4()),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "component": "backend", "origin": "server", "level": "ERROR",
        "event_code": "operation_failed", "error_code": "internal_error",
    }), stream="baseline")
    writer_reserved = queue.store.reserved_bytes
    checkpoint = queue.store.root.parent / "zip-checkpoint"
    real_popen = subprocess.Popen
    code = f'''from pathlib import Path
import time
from zipfile import ZipFile
from app.services.diagnostics import bundle_worker
original = ZipFile.writestr
def checkpoint_write(archive, *args, **kwargs):
    result = original(archive, *args, **kwargs)
    archive.fp.flush()
    Path({str(checkpoint)!r}).touch()
    time.sleep(30)
    return result
ZipFile.writestr = checkpoint_write
raise SystemExit(bundle_worker.main())
'''

    def spawn(command, **options):
        if command[-1] == "app.services.diagnostics.bundle_worker":
            command = [sys.executable, "-c", code]
        return real_popen(command, **options)

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    submitted = queue.submit(request(), actor())
    queue.start()
    try:
        deadline = time.monotonic() + 15
        while not checkpoint.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert checkpoint.exists(), "Real ZIP writer did not reach its compressed-write checkpoint"
        child = queue._child
        assert child is not None and child.poll() is None
        partial = queue.store.root / "bundles" / (submitted.id + ".part")
        assert partial.stat().st_size > 0
        assert queue.store._pins
        assert queue.store.reserved_bytes > writer_reserved
        if termination == "kill":
            child.kill()
        else:
            queue.shutdown(timeout_seconds=0.1)
        child.wait(timeout=5)
        assert queue.wait_idle(10)
        with session_scope() as db:
            row = db.get(DiagnosticBundle, submitted.id)
            assert row.status == "failed"
            assert row.sha256 is None
        assert queue.store.reserved_bytes == writer_reserved
        assert not queue.store._pins
        assert not partial.exists()
        assert not (queue.store.root / "bundles" / (submitted.id + ".zip")).exists()
        assert not (queue.store.root / "snapshots" / submitted.id).exists()
    finally:
        queue.shutdown()


def test_zip_worker_uses_spawn_without_parent_fork(queue, monkeypatch):
    import os
    import subprocess
    import app.services.diagnostics.bundle_queue as module

    real_popen = subprocess.Popen
    worker_options = []
    posix_spawned = []

    if os.name == "posix":
        original_spawn = real_popen._posix_spawn

        def record_spawn(self, *args, **kwargs):
            if self.args[-1] == "app.services.diagnostics.bundle_worker":
                posix_spawned.append(True)
            return original_spawn(self, *args, **kwargs)

        monkeypatch.setattr(real_popen, "_posix_spawn", record_spawn)

    def observe(command, **options):
        if command[-1] == "app.services.diagnostics.bundle_worker":
            worker_options.append(options)
        return real_popen(command, **options)

    monkeypatch.setattr(module.subprocess, "Popen", observe)
    submitted = queue.submit(request(), actor())
    queue.start()
    assert queue.wait_idle(15)
    with session_scope() as db:
        assert db.get(DiagnosticBundle, submitted.id).status == "ready"
    assert len(worker_options) == 1
    if os.name == "posix":
        options = worker_options[0]
        assert "cwd" not in options
        assert options["close_fds"] is False
        assert str(Path(__file__).resolve().parents[1]) in options["env"]["PYTHONPATH"].split(os.pathsep)
        assert posix_spawned == [True]
    queue.shutdown()


def test_shutdown_releases_reservations_for_queued_bundles(queue):
    first = queue.submit(request(), actor())
    second = queue.submit(request(), User(user_id="admin-2", username="Second admin", roles=["admin"]))
    assert queue.store.reserved_bytes > 0
    queue.shutdown()
    assert queue.store.reserved_bytes == 0
    with session_scope() as db:
        assert db.get(DiagnosticBundle, first.id).status == "queued"
        assert db.get(DiagnosticBundle, second.id).status == "queued"


def test_restart_does_not_publish_partial(queue):
    bundle_id = str(uuid4())
    with session_scope() as db:
        db.add(DiagnosticBundle(id=bundle_id, status="building", created_by_id=actor().user_id,
                                created_by=actor().username, created_at=datetime.now(timezone.utc),
                                cutoff_at=datetime.now(timezone.utc), request=request().model_dump(mode="json"),
                                manifest={}, counts={}, size_bytes=0, audit_receipts=[], schema_version=1))
    partial = queue.store.root / "bundles" / (bundle_id + ".part")
    queue.store.write_bytes(partial, b"CANARY_PRIVATE partial ZIP")
    queue.recover()
    with session_scope() as db:
        row = db.get(DiagnosticBundle, bundle_id)
        assert row.status == "failed"
        assert row.error_code == "server_restarted"
        assert not row.sha256
    assert not partial.exists()


def test_oversize_worker_failure_is_audited_without_download(queue, monkeypatch):
    submitted = queue.submit(request(), actor())
    monkeypatch.setattr(queue, "_build_isolated", lambda *_: (_ for _ in ()).throw(BundleTooLarge()))
    queue.start()
    assert queue.wait_idle(10)
    with session_scope() as db:
        row = db.get(DiagnosticBundle, submitted.id)
        assert row.status == "failed"
        assert row.error_code == "diagnostic_bundle_too_large"
        assert row.sha256 is None
        assert "diagnostic_bundle_failed" in list(db.scalars(
            select(AuditLog.action_type).where(AuditLog.target_id == submitted.id)))
    assert not (queue.store.root / "bundles" / (submitted.id + ".zip")).exists()
    queue.shutdown()


def test_delete_during_build_remains_deleted_without_failure_audit(queue, monkeypatch):
    import threading

    submitted = queue.submit(request(), actor())
    entered = threading.Event()
    resume = threading.Event()

    def paused_builder(*_args):
        entered.set()
        assert resume.wait(5)
        raise RuntimeError("builder stopped after deletion")

    monkeypatch.setattr(queue, "_build_isolated", paused_builder)
    queue.start()
    try:
        assert entered.wait(5)
        assert queue.delete(submitted.id, actor()).status == "deleted"
    finally:
        resume.set()
        assert queue.wait_idle(10)
        queue.shutdown()
    with session_scope() as db:
        row = db.get(DiagnosticBundle, submitted.id)
        assert row.status == "deleted"
        actions = list(db.scalars(select(AuditLog.action_type).where(AuditLog.target_id == submitted.id)))
        assert "diagnostic_bundle_deleted" in actions
        assert "diagnostic_bundle_failed" not in actions


def test_ready_audit_failure_never_publishes_download(queue, monkeypatch):
    submitted = queue.submit(request(), actor())
    original = audit.record_in_session
    def reject_ready(db, **kwargs):
        if kwargs["action_type"] == "diagnostic_bundle_ready":
            raise OSError("CANARY_PRIVATE")
        return original(db, **kwargs)
    monkeypatch.setattr(audit, "record_in_session", reject_ready)
    queue.start()
    assert queue.wait_idle(10)
    with session_scope() as db:
        assert db.get(DiagnosticBundle, submitted.id).status == "failed"
    assert not (queue.store.root / "bundles" / (submitted.id + ".zip")).exists()
    queue.shutdown()
