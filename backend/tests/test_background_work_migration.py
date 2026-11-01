"""Scheduled cleanup is a bounded, deduplicated durable intent."""
from datetime import datetime, timedelta, timezone

from app.db.models import WorkItem
from app.db.session import session_scope
from app.services.work.store import claim, finish


def test_non_document_work_cannot_write_after_cancellation():
    import pytest
    from app.services.work.context import work_execution
    from app.services.work.models import LostOwnership, WorkSpec
    from app.services.work.store import cancel, enqueue
    with session_scope() as session:
        work_id = enqueue(session, WorkSpec("duplicate_refresh", 1, {}, "refresh"))
        work = claim(session, owner_id="old", supported={"duplicate_refresh": {1}}, lease_seconds=120)
    with session_scope() as session:
        cancel(session, work_id)
    with pytest.raises(LostOwnership), work_execution(work), session_scope() as session:
        enqueue(session, WorkSpec("test", 1, {}, "stale-write"))
    with session_scope() as session:
        assert session.query(WorkItem).count() == 1


def test_auth_retention_handler_removes_only_expired_sessions():
    from app.db.models import AuthSession
    from app.services.work.models import WorkSpec
    from app.services.work.runtime import build_handlers
    from app.services.work.store import enqueue
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        session.add_all([AuthSession(id="expired", external_id="user", identity={}, expires_at=now - timedelta(days=1)),
                         AuthSession(id="valid", external_id="user", identity={}, expires_at=now + timedelta(days=1))])
        enqueue(session, WorkSpec("auth_purge", 1, {}, "auth-purge"))
        work = claim(session, owner_id="worker", supported={"auth_purge": {1}}, lease_seconds=120)
    build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        assert session.get(AuthSession, "expired") is None
        assert session.get(AuthSession, "valid") is not None


def test_auth_retention_yields_after_bounded_page_and_resumes():
    from threading import Event
    from app.config import Settings
    from app.db.models import AuthSession
    from app.services.work.models import WorkSpec
    from app.services.work.runtime import build_handlers
    from app.services.work.store import enqueue
    from app.services.work.worker import Worker
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        session.add_all([AuthSession(id=f"{index:04}", external_id="user", identity={},
            expires_at=now - timedelta(days=1)) for index in range(101)])
        work_id = enqueue(session, WorkSpec("auth_purge", 1, {}, "auth-pages"))
    worker = Worker(Settings(_env_file=None), registry=build_handlers(), owner_id="worker")
    assert worker.run_once(Event())
    with session_scope() as session:
        assert session.query(AuthSession).count() == 1
        work = session.get(WorkItem, work_id)
        assert work.status == "queued"
        work.available_at = now - timedelta(seconds=1)
    assert worker.run_once(Event())
    with session_scope() as session:
        assert session.query(AuthSession).count() == 0
        assert session.get(WorkItem, work_id).status == "succeeded"


def test_chat_retention_handler_keeps_active_and_recent_threads(monkeypatch):
    from app.config import Settings
    from app.db.models import ChatSession
    from app.services import audit
    from app.services.work.models import WorkSpec
    from app.services.work.runtime import build_handlers
    from app.services.work.store import enqueue
    settings = Settings(_env_file=None, chat_history_retention_days=7)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        session.add_all([ChatSession(id="expired", user_id="user", deleted_at=now - timedelta(days=10)),
            ChatSession(id="recent", user_id="user", deleted_at=now - timedelta(days=1)),
            ChatSession(id="active", user_id="user")])
        enqueue(session, WorkSpec("chat_purge", 1, {}, "chat-purge"))
        work = claim(session, owner_id="worker", supported={"chat_purge": {1}}, lease_seconds=120)
    build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        assert session.get(ChatSession, "expired") is None
        assert session.get(ChatSession, "recent") is not None
        assert session.get(ChatSession, "active") is not None
    entries = audit.get_audit().query(target_id="expired")
    assert len(entries) == 1 and entries[0]["action_type"] == audit.CHAT_HISTORY_AUTO_DELETE


def test_bulk_coordinator_rollback_keeps_parent_and_children_unchanged():
    import pytest
    from app.services.work.context import coordinator_session
    from app.services.work.models import WorkSpec
    from app.services.work.store import enqueue
    with session_scope() as session:
        parent = enqueue(session, WorkSpec("bulk", 1, {"job_id": 1}, "bulk/1"))
        work = claim(session, owner_id="worker", supported={"bulk": {1}}, lease_seconds=120)
    with pytest.raises(ValueError, match="abort admission"), coordinator_session(work) as session:
        enqueue(session, WorkSpec("test", 1, {}, "child"))
        finish(session, work, outcome="succeeded")
        raise ValueError("abort admission")
    with session_scope() as session:
        assert session.get(WorkItem, parent).status == "running"
        assert session.query(WorkItem).count() == 1


def test_scheduler_dedupes_slot():
    from app.services.work.scheduler import PeriodicSpec, enqueue_due
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    spec = PeriodicSpec("trash_purge", interval_seconds=60)
    with session_scope() as session:
        first = enqueue_due(session, spec, at_utc=now)
    with session_scope() as session:
        assert enqueue_due(session, spec, at_utc=now + timedelta(seconds=10)) == first
        assert session.query(WorkItem).count() == 1


def test_scheduler_coalesces_pending_slots_before_creating_the_next():
    from app.services.work.scheduler import PeriodicSpec, enqueue_due
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    spec = PeriodicSpec("trash_purge", interval_seconds=60)
    with session_scope() as session:
        first = enqueue_due(session, spec, at_utc=now)
    with session_scope() as session:
        assert enqueue_due(session, spec, at_utc=now + timedelta(hours=1)) == first
        work = claim(session, owner_id="worker", supported={"trash_purge": {1}}, lease_seconds=120)
        finish(session, work, outcome="succeeded")
    with session_scope() as session:
        assert enqueue_due(session, spec, at_utc=now + timedelta(hours=1)) != first
        assert session.query(WorkItem).count() == 2
