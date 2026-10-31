"""Transactional work ownership; real concurrent claims are probed on PostgreSQL."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import session_scope
from app.services.work.models import WorkSpec, LostOwnership
from app.services.work.store import enqueue, claim, renew, assert_owner, finish, cancel
from tests.test_artifact_storage_migration import corpus as corpus


SUPPORTED = {"test": {1}}


def spec(key="request-1", **kwargs):
    return WorkSpec(kind="test", payload_version=1, payload={"document_id": "a" * 16}, dedupe_key=key, **kwargs)


def take(owner="worker-a"):
    with session_scope() as session:
        return claim(session, owner_id=owner, supported=SUPPORTED, lease_seconds=120)


def test_dedupe_in_same_transaction():
    with session_scope() as session:
        first = enqueue(session, spec())
        assert enqueue(session, spec()) == first
    with session_scope() as session:
        assert enqueue(session, spec()) == first
    work = take()
    assert work.work_id == first and work.spec == spec() and work.fence == 1
    assert take("worker-b") is None


def test_rollback_leaves_no_work():
    with pytest.raises(RuntimeError), session_scope() as session:
        enqueue(session, spec())
        raise RuntimeError("rollback caller transaction")
    assert take() is None


def test_dedupe_does_not_accept_changed_payload():
    with session_scope() as session:
        enqueue(session, spec())
    with pytest.raises(ValueError), session_scope() as session:
        enqueue(session, WorkSpec("test", 1, {"document_id": "b" * 16}, "request-1", None))


def test_unknown_version_not_claimed():
    with session_scope() as session:
        unknown = enqueue(session, WorkSpec("test", 99, {}, "future", None))
        known = enqueue(session, spec())
    assert take().work_id == known
    assert take() is None
    with session_scope() as session:
        from app.db.models import WorkItem
        assert session.get(WorkItem, unknown).status == "queued"


def expire(work_id):
    from app.db.models import WorkItem
    with session_scope() as session:
        session.get(WorkItem, work_id).lease_until = datetime.now(timezone.utc) - timedelta(seconds=10)


def test_old_fence_cannot_renew_finish():
    with session_scope() as session:
        enqueue(session, spec())
    old = take()
    expire(old.work_id)
    new = take("worker-b")
    assert new.work_id == old.work_id and new.fence == old.fence + 1
    for operation in (lambda s: assert_owner(s, old), lambda s: renew(s, old, lease_seconds=120),
                      lambda s: finish(s, old, outcome="succeeded")):
        with pytest.raises(LostOwnership), session_scope() as session:
            operation(session)
    with session_scope() as session:
        assert_owner(session, new)
        finish(session, new, outcome="succeeded")
    assert take() is None


def test_cancel_prevents_publish_guard():
    with session_scope() as session:
        work_id = enqueue(session, spec())
    work = take()
    with session_scope() as session:
        assert cancel(session, work_id)
    with pytest.raises(LostOwnership), session_scope() as session:
        assert_owner(session, work)
    with session_scope() as session:
        finish(session, work, outcome="cancelled")
        assert not cancel(session, work_id)
    assert take() is None


def test_queued_cancel_never_executes():
    with session_scope() as session:
        work_id = enqueue(session, spec())
        assert cancel(session, work_id)
    assert take() is None


def test_expired_owner_cannot_publish_even_before_reclaim():
    with session_scope() as session:
        enqueue(session, spec())
    work = take()
    expire(work.work_id)
    with pytest.raises(LostOwnership), session_scope() as session:
        finish(session, work, outcome="succeeded")


def test_cancelled_crashed_owner_becomes_terminal_after_expiry():
    from app.db.models import WorkItem
    with session_scope() as session:
        work_id = enqueue(session, spec())
    take()
    with session_scope() as session:
        cancel(session, work_id)
    expire(work_id)
    assert take() is None
    with session_scope() as session:
        assert session.get(WorkItem, work_id).status == "cancelled"


def test_json_boolean_and_number_are_different_dedupe_requests():
    with session_scope() as session:
        enqueue(session, WorkSpec("test", 1, {"flag": True}, "typed-json"))
    with pytest.raises(ValueError), session_scope() as session:
        enqueue(session, WorkSpec("test", 1, {"flag": 1}, "typed-json"))


def test_unknown_payload_is_visible_as_unsupported_and_can_later_be_supported():
    from app.db.models import WorkItem
    with session_scope() as session:
        work_id = enqueue(session, WorkSpec("test", 99, {}, "future"))
    assert take() is None
    with session_scope() as session:
        row = session.get(WorkItem, work_id)
        assert row.status == "queued" and row.error_code == "unsupported_payload"
        future = claim(session, owner_id="updated-worker", supported={"test": {99}}, lease_seconds=120)
        assert future.work_id == work_id


def test_storage_migration_fingerprints_accepted_work(corpus):
    from tests.test_artifact_storage_migration import migration, backup
    from app.db.models import WorkItem
    with session_scope() as session:
        work_id = enqueue(session, spec())
    task = migration(corpus)
    proof = backup(task, corpus[1])
    with session_scope() as session:
        session.get(WorkItem, work_id).payload_json = {"document_id": "changed"}
    with pytest.raises(ValueError):
        task.copy(apply=True, writers_stopped=True, backup=proof)
