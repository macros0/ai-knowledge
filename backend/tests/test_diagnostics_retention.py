from datetime import timedelta

from sqlalchemy import select

from app.db.models import AuditLog, DiagnosticBundle, DiagnosticSession
from app.db.session import session_scope
from tests.test_diagnostics_bundle_queue import queue as _queue_fixture, request
from tests.test_diagnostics_sessions import actor, service as _service_fixture

service = _service_fixture
queue = _queue_fixture


def test_metadata_pruned_only_after_files_and_pending_audit_are_retired(service):
    captured = service.start("system", 5, actor())
    service.stop(captured.id, actor(), "manual")
    after = service.utcnow() + timedelta(days=8)
    service.store.sweep(after)
    service.prune_metadata(after)
    with session_scope() as db:
        assert db.get(DiagnosticSession, captured.id) is None
        assert len(list(db.scalars(select(AuditLog).where(AuditLog.target_id == captured.id)))) == 2


def test_bundle_metadata_pruned_after_seven_days_only_when_file_and_download_retired(queue):
    created = queue.submit(request(), actor())
    queue.start()
    assert queue.wait_idle(10)
    lease = queue.acquire_download(created.id, actor())
    queue.delete(created.id, actor())
    old = queue.utcnow() - timedelta(days=8)
    with session_scope() as db:
        db.get(DiagnosticBundle, created.id).finished_at = old
    assert queue.prune_metadata(queue.utcnow()) == 0
    with session_scope() as db:
        assert db.get(DiagnosticBundle, created.id) is not None
    lease.release()
    assert queue.prune_metadata(queue.utcnow()) == 1
    with session_scope() as db:
        assert db.get(DiagnosticBundle, created.id) is None
        assert list(db.scalars(select(AuditLog).where(AuditLog.target_id == created.id)))
    queue.shutdown()
