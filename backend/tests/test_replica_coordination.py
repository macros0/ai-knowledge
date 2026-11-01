"""Transactional primitives shared by independent API/worker instances."""
from dataclasses import replace

import pytest
from sqlalchemy import text

from app.db.session import session_scope
from app.services.work.coordination import (
    reserve_capacity, renew_capacity, release_capacity, consume_rate,
    config_revision, bump_config_revision, CoordinationOwnershipLost,
)


def test_two_apis_share_capacity_and_release_requires_full_token():
    with session_scope() as session:
        first = reserve_capacity(session, resource="llm", owner_id="api-a", limit=1, ttl_seconds=60)
    with session_scope() as session:
        assert reserve_capacity(session, resource="llm", owner_id="worker-b", limit=1, ttl_seconds=60) is None
        with pytest.raises(CoordinationOwnershipLost):
            release_capacity(session, replace(first, owner_id="worker-b"))
    with session_scope() as session:
        release_capacity(session, first)
    with session_scope() as session:
        replacement = reserve_capacity(session, resource="llm", owner_id="worker-b", limit=1, ttl_seconds=60)
        assert replacement.fence > first.fence
        with pytest.raises(CoordinationOwnershipLost):
            release_capacity(session, first)


def test_expired_owner_does_not_reuse_provider_slot_before_protected_deadline():
    with session_scope() as session:
        lease = reserve_capacity(session, resource="llm", owner_id="api-a", limit=1, ttl_seconds=10, hold_seconds=120)
        session.execute(text("UPDATE replica_capacity_slots SET expires_at='2000-01-01' WHERE id=:id"), {"id": lease.id})
    with session_scope() as session:
        assert reserve_capacity(session, resource="llm", owner_id="api-b", limit=1, ttl_seconds=10, hold_seconds=120) is None
        with pytest.raises(CoordinationOwnershipLost):
            renew_capacity(session, lease, ttl_seconds=10)
        session.execute(text("UPDATE replica_capacity_slots SET protected_until='2000-01-01' WHERE id=:id"), {"id": lease.id})
    with session_scope() as session:
        assert reserve_capacity(session, resource="llm", owner_id="api-b", limit=1, ttl_seconds=10, hold_seconds=120).fence > lease.fence


def test_capacity_and_revision_changes_rollback_with_callers_transaction():
    with pytest.raises(RuntimeError), session_scope() as session:
        reserve_capacity(session, resource="llm", owner_id="api-a", limit=1, ttl_seconds=60)
        assert bump_config_revision(session, "stopwords") == 1
        raise RuntimeError("audit unavailable")
    with session_scope() as session:
        assert config_revision(session, "stopwords") == 0
        assert reserve_capacity(session, resource="llm", owner_id="api-b", limit=1, ttl_seconds=60)


def test_shared_rolling_rate_window_and_revision_visibility():
    with session_scope() as session:
        assert consume_rate(session, key="search:actor-a", limit=2, window_seconds=60)
        assert bump_config_revision(session, "ui_dictionary") == 1
    with session_scope() as session:
        assert consume_rate(session, key="search:actor-a", limit=2, window_seconds=60)
        assert not consume_rate(session, key="search:actor-a", limit=2, window_seconds=60)
        assert consume_rate(session, key="search:actor-b", limit=2, window_seconds=60)
        assert config_revision(session, "ui_dictionary") == 1
        assert bump_config_revision(session, "ui_dictionary") == 2


def test_different_capacity_policy_on_another_replica_fails_closed():
    with session_scope() as session:
        reserve_capacity(session, resource="llm", owner_id="api-a", limit=1, ttl_seconds=60)
    with session_scope() as session:
        with pytest.raises(ValueError, match="policy"):
            reserve_capacity(session, resource="llm", owner_id="api-b", limit=2, ttl_seconds=60)


def test_remote_stopword_edit_invalidates_a_warm_api_cache(tmp_path, monkeypatch):
    from app.config import Settings
    from app.services import stopwords
    from app.services.locale_service import import_stopwords
    stopwords.ensure_seeded()
    settings = Settings(_env_file=None, data_dir=tmp_path)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr(stopwords, "get_settings", lambda: settings)
    stopwords.invalidate()
    assert "replicatoken" not in stopwords.get_stopwords("marker")
    # A mutation on a different process cannot clear this instance's memory.
    monkeypatch.setattr("app.services.locale_service.invalidate", lambda *_args: None)
    from types import SimpleNamespace
    user = SimpleNamespace(user_id="admin", username="admin", roles=["admin"])
    import_stopwords("ru", ["replicatoken"], "marker", mode="merge", confirm=True, user=user)
    assert "replicatoken" in stopwords.get_stopwords("marker")


def test_fresh_rate_limiter_instances_share_the_same_action_budget(tmp_path, monkeypatch):
    from app.config import Settings
    from app.services.rate_limiter import RateLimiter, RateLimitExceeded
    settings = Settings(_env_file=None, data_dir=tmp_path)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    first, second = RateLimiter(), RateLimiter()
    first.check_action("actor", "chat", max_requests=1)
    with pytest.raises(RateLimitExceeded):
        second.check_action("actor", "chat", max_requests=1)


def test_denied_bulk_rate_admission_does_not_consume_other_budgets(tmp_path, monkeypatch):
    from app.config import Settings
    from app.services.rate_limiter import RateLimiter, RateLimitExceeded
    settings = Settings(_env_file=None, data_dir=tmp_path)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    first, second = RateLimiter(), RateLimiter()
    first.check_bulk_regenerate("actor", 2, max_ops_per_hour=2, max_docs_per_hour=3)
    with pytest.raises(RateLimitExceeded):
        second.check_bulk_regenerate("actor", 2, max_ops_per_hour=2, max_docs_per_hour=3)
    second.check_bulk_regenerate("actor", 1, max_ops_per_hour=2, max_docs_per_hour=3)
    with pytest.raises(RateLimitExceeded):
        RateLimiter().check_bulk_regenerate("actor", 1, max_ops_per_hour=2, max_docs_per_hour=3)


def test_uncertain_provider_slot_requires_completion_even_after_lease_expiry():
    from app.services.work.coordination import complete_capacity
    with session_scope() as session:
        lease = reserve_capacity(session, resource="llm", owner_id="api-a", limit=1,
            ttl_seconds=10, release_required=True)
        session.execute(text("UPDATE replica_capacity_slots SET expires_at='2000-01-01', "
            "protected_until='2000-01-01' WHERE id=:id"), {"id": lease.id})
    with session_scope() as session:
        assert reserve_capacity(session, resource="llm", owner_id="api-b", limit=1, ttl_seconds=10) is None
        with pytest.raises(CoordinationOwnershipLost):
            complete_capacity(session, replace(lease, owner_id="api-b"))
        # A genuine completion may release a still-matching quarantined owner;
        # expiry alone never establishes that a remote model stopped running.
        complete_capacity(session, lease)
    with session_scope() as session:
        assert reserve_capacity(session, resource="llm", owner_id="api-b", limit=1, ttl_seconds=10)
