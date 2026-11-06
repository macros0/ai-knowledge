"""Startup and every SQL boundary bind a replica to the installed identity."""
import pytest
from sqlalchemy import select, text

from app.db.models import ArtifactStorageControl
from app.db.session import get_engine, session_scope
from app.services.artifacts.control import descriptor, migration_access
from tests.test_replica_runtime_identity import configured


@pytest.fixture
def installed(tmp_path, monkeypatch):
    from app.config import Settings
    from app.db.models import ReplicaRuntimeIdentity, MaintenanceState
    from app.deployment.runtime_identity import SCHEMA_REVISION
    from app.services.work.store import _now
    values, document = configured(tmp_path)
    settings = Settings(**values)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    with migration_access(), session_scope() as session:
        control = session.get(ArtifactStorageControl, 1)
        control.installation_id = document["installation_id"]
        control.active_store_id = settings.artifact_store_id
        control.active_descriptor = descriptor(settings)
        session.add(ReplicaRuntimeIdentity(id=1, installation_id=document["installation_id"],
            fingerprint=document["runtime_fingerprint"], storage_receipt_digest=document["storage_receipt_digest"],
            schema_revision=SCHEMA_REVISION, created_at=_now(session)))
        session.add(MaintenanceState(id=1, installation_id=document["installation_id"], epoch=0,
            enabled=False, intended_counts_json={}, writer_receipts_json={}))
    with get_engine().begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        connection.execute(text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": SCHEMA_REVISION})
    from app.deployment.runtime_identity import enforce_runtime_identity
    enforce_runtime_identity(settings)
    return settings


def test_startup_requires_matching_installed_identity(installed):
    from app.deployment.runtime_identity import enforce_runtime_identity
    assert enforce_runtime_identity(installed).installation_id == "1" * 32


def test_changed_installed_fingerprint_fences_existing_sessions(installed):
    from app.db.models import ReplicaRuntimeIdentity
    with migration_access(), session_scope() as session:
        session.get(ReplicaRuntimeIdentity, 1).fingerprint = "f" * 64
    with pytest.raises(ValueError, match="identity"):
        with session_scope() as session:
            session.scalar(select(ArtifactStorageControl))


def test_startup_rejects_wrong_database_revision(installed):
    from app.deployment.runtime_identity import enforce_runtime_identity
    with get_engine().begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='rpl03b_diagnostics'"))
    with pytest.raises(ValueError, match="schema"):
        enforce_runtime_identity(installed)


def test_fenced_replica_is_not_ready(installed, monkeypatch):
    from app.db.models import ReplicaRuntimeIdentity
    from app.services import health
    monkeypatch.setattr(health, "get_settings", lambda: installed)
    with migration_access(), session_scope() as session:
        session.get(ReplicaRuntimeIdentity, 1).fingerprint = "f" * 64
    assert health._check_database()["status"] == "down"


def test_http_command_validates_identity_before_recorder(tmp_path, monkeypatch):
    from app.config import Settings
    from app.diagnostic_entrypoint import main
    settings = Settings(_env_file=None, data_dir=tmp_path)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    def forbidden(*_a, **_k):
        raise AssertionError("Recorder installed before identity admission")
    monkeypatch.setattr("app.services.diagnostics.runtime.install_entrypoint_runtime", forbidden)
    with pytest.raises(ValueError, match="identity"):
        main([], settings=settings)


@pytest.mark.parametrize("role", ["api", "worker"])
def test_entrypoint_rejects_uninstalled_identity_before_starting_consumers(tmp_path, monkeypatch, role):
    # Import before patching the source of worker's module-bound runtime import.
    import app.worker as command
    from app.config import Settings
    settings = Settings(_env_file=None, data_dir=tmp_path)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", role
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Side effect before runtime identity validation")
    monkeypatch.setattr("app.services.audit_runtime.validate_existing_audit", forbidden)
    monkeypatch.setattr("app.services.work.runtime.start_runtime", forbidden)
    monkeypatch.setattr(command, "start_runtime", forbidden)
    with pytest.raises(ValueError, match="identity"):
        if role == "worker":
            command.main([], settings=settings)
        else:
            import anyio
            import app.main as application
            from types import SimpleNamespace
            monkeypatch.setattr(application, "get_settings", lambda: settings)
            async def enter():
                async with application._api_lifespan(SimpleNamespace(state=SimpleNamespace())):
                    pytest.fail("Uninstalled API started")
            anyio.run(enter)
