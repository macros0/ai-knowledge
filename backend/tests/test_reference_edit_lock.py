from datetime import datetime, timedelta, timezone

import pytest

from app.auth.models import User
from app.db.session import session_scope


def setup(monkeypatch):
    from app.services import reference_edit_lock as locks
    from app.services.reference_resources import ReferenceKey
    from app.services.tag_registry import TagRegistry
    tag = TagRegistry()
    tag.add(["lease-test"])
    key = ReferenceKey("tag", str(tag.all()[0]["id"]))
    now = [datetime(2026, 10, 18, tzinfo=timezone.utc)]
    monkeypatch.setattr(locks, "utc_now", lambda: now[0])
    alice = User(user_id="a", username="Alice", roles=["admin"])
    bob = User(user_id="b", username="Bob", roles=["admin"])
    return locks, key, now, alice, bob


def test_two_tabs_and_late_requests_cannot_touch_new_owner(monkeypatch):
    from app.services.reference_concurrency import ReferenceError
    locks, key, now, alice, bob = setup(monkeypatch)
    with session_scope() as s:
        first = locks.acquire_edit_lock(s, key, alice, "a" * 64)
    for actor, token in [(alice, "b" * 64), (bob, "c" * 64)]:
        with pytest.raises(ReferenceError) as exc:
            with session_scope() as s:
                locks.acquire_edit_lock(s, key, actor, token)
        assert exc.value.code == "reference_edit_locked"
    now[0] += timedelta(seconds=180)
    with session_scope() as s:
        second = locks.acquire_edit_lock(s, key, bob, "c" * 64)
        assert second["lease_epoch"] > first["lease_epoch"]
        locks.release_edit_lock(s, key, alice, "a" * 64, first["lease_epoch"])
        locks.require_edit_lock(s, key, bob, "c" * 64, second["lease_epoch"])
    with pytest.raises(ReferenceError):
        with session_scope() as s:
            locks.renew_edit_lock(s, key, alice, "a" * 64, first["lease_epoch"], 1)


def test_merge_cannot_resurrect_an_expired_borrowed_source(monkeypatch):
    from app.services.reference_concurrency import ReferenceError
    from app.services.reference_resources import ReferenceKey
    from app.services.tag_registry import TagRegistry
    locks, key, now, alice, _ = setup(monkeypatch)
    TagRegistry().add(["merge-target"])
    target = ReferenceKey("tag", next(item["id"] for item in TagRegistry().all() if item["name"] == "merge-target"))
    with session_scope() as session:
        first = locks.acquire_edit_lock(session, key, alice, "a" * 64)
    now[0] += timedelta(seconds=180)
    with pytest.raises(ReferenceError) as exc:
        with session_scope() as session:
            locks.acquire_edit_locks(session, [key, target], alice, {key: "a" * 64, target: "b" * 64},
                                     {key: first["lease_epoch"]})
    assert exc.value.code == "reference_edit_lock_lost"
    with session_scope() as session:
        assert locks.edit_lock_status(session, target)["state"] == "available"
        assert locks.edit_lock_status(session, key)["lease_epoch"] == first["lease_epoch"]


def test_heartbeat_and_duplicate_acquire_do_not_keep_forgotten_editor_alive(monkeypatch):
    from app.services.reference_concurrency import ReferenceError
    locks, key, now, alice, _ = setup(monkeypatch)
    start = now[0]
    with session_scope() as s:
        epoch = locks.acquire_edit_lock(s, key, alice, "a" * 64)["lease_epoch"]
    for minute in range(1, 10):
        now[0] = start + timedelta(minutes=minute)
        with session_scope() as s:
            locks.renew_edit_lock(s, key, alice, "a" * 64, epoch, 0)
            locks.acquire_edit_lock(s, key, alice, "a" * 64)
    now[0] = start + timedelta(minutes=10)
    with pytest.raises(ReferenceError):
        with session_scope() as s:
            locks.renew_edit_lock(s, key, alice, "a" * 64, epoch, 1)


def test_new_activity_extends_idle_once_and_token_never_leaks(monkeypatch):
    locks, key, now, alice, _ = setup(monkeypatch)
    with session_scope() as s:
        status = locks.acquire_edit_lock(s, key, alice, "a" * 64)
    epoch = status["lease_epoch"]
    now[0] += timedelta(seconds=60)
    with session_scope() as s:
        new = locks.renew_edit_lock(s, key, alice, "a" * 64, epoch, 1)
    now[0] += timedelta(seconds=60)
    with session_scope() as s:
        replay = locks.renew_edit_lock(s, key, alice, "a" * 64, epoch, 1)
    assert new["idle_expires_at"] == replay["idle_expires_at"]
    assert "a" * 64 not in str(status) + str(new) + str(replay)
    with session_scope() as s:
        from app.services.reference_concurrency import lock_reference_resources
        assert lock_reference_resources(s, [key])[key].revision == 1


def test_revoke_only_expected_epoch_and_acquire_many_rolls_back(monkeypatch):
    from app.services.reference_resources import ReferenceKey
    from app.services.reference_concurrency import ReferenceError
    from app.services.tag_registry import TagRegistry
    locks, key, now, alice, bob = setup(monkeypatch)
    TagRegistry().add(["another"])
    other = ReferenceKey("tag", str(next(t["id"] for t in TagRegistry().all() if t["name"] == "another")))
    with session_scope() as s:
        epoch = locks.acquire_edit_lock(s, key, alice, "a" * 64)["lease_epoch"]
    with pytest.raises(ReferenceError):
        with session_scope() as s:
            locks.acquire_edit_locks(s, [other, key], bob, {other: "b" * 64, key: "c" * 64})
    with session_scope() as s:
        assert locks.edit_lock_status(s, other)["state"] == "available"
    with pytest.raises(ReferenceError):
        with session_scope() as s:
            locks.revoke_edit_lock(s, key, bob, epoch - 1, "test")
    with session_scope() as s:
        locks.revoke_edit_lock(s, key, bob, epoch, "test")
        assert locks.edit_lock_status(s, key)["state"] == "available"


def test_released_state_keeps_content_revision_and_monotonic_epoch(monkeypatch):
    locks, key, now, alice, bob = setup(monkeypatch)
    with session_scope() as s:
        first = locks.acquire_edit_lock(s, key, alice, "a" * 64)
        locks.release_edit_lock(s, key, alice, "a" * 64, first["lease_epoch"])
        second = locks.acquire_edit_lock(s, key, bob, "b" * 64)
        assert second["lease_epoch"] > first["lease_epoch"]
        assert second["reference_revision"] == first["reference_revision"] == 1
