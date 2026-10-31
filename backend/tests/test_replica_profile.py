from dataclasses import replace

import pytest

from app.deployment.replica_profile import (
    ReplicaProfile, ReplicaProfileError, validate_replica_profile, validate_runtime_profile,
)


def mixed():
    return ReplicaProfile(
        mode="distributed", role="api", artifact_backend="s3",
        postgres_provider="external", qdrant_provider="managed", artifact_provider="external",
        database_dialect="postgresql", shared_artifacts_verified=True,
    )


def test_mixed_providers_allowed():
    assert validate_replica_profile(mixed()) is None


def test_distributed_local_rejected():
    with pytest.raises(ReplicaProfileError, match="shared"):
        validate_replica_profile(replace(mixed(), artifact_backend="local"))


def test_sqlite_worker_rejected():
    with pytest.raises(ReplicaProfileError, match="PostgreSQL"):
        validate_replica_profile(replace(mixed(), role="worker", database_dialect="sqlite"))


def test_resilient_requires_certification():
    with pytest.raises(ReplicaProfileError, match="certification"):
        validate_replica_profile(replace(mixed(), resilience="resilient"))


@pytest.mark.parametrize("field,value", [
    ("role", "typo"), ("artifact_backend", "typo"), ("postgres_provider", "typo"),
    ("mode", "typo"), ("api_replicas", 0), ("api_replicas", True),
])
def test_invalid_inputs_fail_closed(field, value):
    with pytest.raises(ReplicaProfileError):
        validate_replica_profile(replace(mixed(), **{field: value}))


def test_runtime_does_not_enable_unimplemented_replicas():
    with pytest.raises(ReplicaProfileError, match="not implemented"):
        validate_runtime_profile("replicas-v1", mixed())


def test_unknown_schema_rejected():
    with pytest.raises(ReplicaProfileError, match="schema"):
        validate_runtime_profile("typo", mixed())


def test_legacy_profiles_unchanged():
    assert validate_runtime_profile(None, ReplicaProfile()) is None


def test_settings_reject_opt_in_until_capability_exists():
    from app.config import Settings
    with pytest.raises(ValueError, match="not implemented"):
        Settings(_env_file=None, okf_deployment_schema="replicas-v1")


def test_settings_reject_replica_role_without_schema():
    from app.config import Settings
    with pytest.raises(ValueError, match="schema"):
        Settings(_env_file=None, app_role="worker")


def test_preflight_rejects_unknown_schema(monkeypatch, capsys):
    from scripts.validate_storage_mode import main
    monkeypatch.setenv("OKF_DEPLOYMENT_SCHEMA", "typo")
    assert main() == 1
    assert "schema" in capsys.readouterr().err
