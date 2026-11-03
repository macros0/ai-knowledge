"""Recovery changes identity/auth atomically while the restored queue stays fenced."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.config import Settings
from app.db.models import (ArtifactStorageControl, AuthSession, MaintenanceState, ReplicaRuntimeIdentity,
    ReplicaCapacityResource, ReplicaCapacitySlot)
from app.db.session import get_engine
from app.services.work.maintenance_control import MaintenanceOperator
from tests.test_replica_backup import fixture_plan


def load():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/restore_identity.py"
    assert path.is_file(), "Offline restore identity adoption is missing"
    spec = importlib.util.spec_from_file_location("restore_identity", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def prepare(tmp_path):
    from app.deployment.bootstrap import canonical
    from hashlib import sha256
    settings = Settings(_env_file=None, data_dir=tmp_path, app_secret_key="new-key" * 12)
    settings.okf_deployment_schema = "replicas-v1"
    settings.app_role = "api"
    settings.deployment_namespace = "replica-test-target01"
    settings.artifact_backend, settings.artifact_store_id = "s3", "fixture-store"
    settings.artifact_s3_endpoint = "https://artifacts.replica-test-target01.svc:9000"
    settings.artifact_s3_bucket, settings.artifact_s3_prefix = "replica-test-target01", "replica_test/target01/application/"
    plan, _ = fixture_plan()
    plan = plan.model_copy(update={"model_vector_sha256": sha256(canonical("1" * 64)).hexdigest()})
    now = datetime.now(timezone.utc)
    with get_engine().begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('rpl05_maintenance_epoch')"))
        connection.execute(ArtifactStorageControl.__table__.update().values(installation_id=plan.source.installation_id,
            active_store_id=settings.artifact_store_id, active_descriptor={"backend": "s3", "endpoint": "https://source.svc"},
            maintenance_token=plan.backup_id, migration_id=None))
        connection.execute(ReplicaRuntimeIdentity.__table__.insert().values(id=1, installation_id=plan.source.installation_id,
            fingerprint="1" * 64, storage_receipt_digest="sha256:" + "2" * 64, schema_revision=plan.schema_revision, created_at=now))
        connection.execute(MaintenanceState.__table__.insert().values(id=1, installation_id=plan.source.installation_id,
            epoch=plan.source.epoch, enabled=True, operation_id=plan.backup_id, operation_kind="backup", owner_id="source",
            intended_counts_json={"frontend": 2, "api": 2, "worker": 2}, writer_receipts_json={}))
        connection.execute(AuthSession.__table__.insert().values(id="old-session", external_id="old-user", identity={},
            created_at=now, expires_at=now + timedelta(days=1)))
    return settings, plan


def test_rebind_changes_installation_revokes_auth_and_keeps_jobs_held(tmp_path):
    tool = load()
    settings, plan = prepare(tmp_path)
    operation = uuid4().hex
    with MaintenanceOperator(get_engine(), operation_id=operation, owner_id="restore-test", settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            document, counts = tool.rebind_target(session, settings, plan, installation_id="5" * 32,
                operation_id=operation, selection_digest="sha256:" + "7" * 64)
        with op.transaction() as session:
            state = session.get(MaintenanceState, 1)
            assert state.enabled and state.epoch == plan.source.epoch + 1
            assert state.operation_id == operation and state.installation_id == "5" * 32
            assert session.scalar(select(AuthSession)) is None
            assert session.get(ArtifactStorageControl, 1).active_descriptor["bucket"] == settings.artifact_s3_bucket
            assert session.get(ReplicaRuntimeIdentity, 1).fingerprint == document["runtime_fingerprint"]
    assert counts["invalidated_sessions"] == 1
    assert document["installation_id"] == "5" * 32


def test_rebind_without_owned_operator_cannot_mutate_target(tmp_path):
    tool = load()
    settings, plan = prepare(tmp_path)
    from sqlalchemy.orm import Session
    with Session(get_engine()) as session, pytest.raises(ValueError):
        tool.rebind_target(session, settings, plan, installation_id="5" * 32,
            operation_id=uuid4().hex, selection_digest="sha256:" + "7" * 64)


def test_uncertain_provider_owner_blocks_rebind_and_auth_cleanup(tmp_path):
    tool = load()
    settings, plan = prepare(tmp_path)
    with get_engine().begin() as connection:
        connection.execute(ReplicaCapacityResource.__table__.insert().values(resource="llm", capacity_limit=1))
        connection.execute(ReplicaCapacitySlot.__table__.insert().values(id=uuid4().hex, resource="llm", slot=0,
            owner_id="unknown-provider-request", fence=1, release_required=True))
    operation = uuid4().hex
    with MaintenanceOperator(get_engine(), operation_id=operation, owner_id="restore-test", settings=settings, allow_sqlite=True) as op:
        with pytest.raises(ValueError), op.transaction() as session:
            tool.rebind_target(session, settings, plan, installation_id="5" * 32,
                operation_id=operation, selection_digest="sha256:" + "7" * 64)
    with get_engine().connect() as connection:
        assert connection.scalar(select(AuthSession.id)) == "old-session"
        assert connection.scalar(select(ReplicaRuntimeIdentity.installation_id)) == plan.source.installation_id


def prepare_rwx(tmp_path, monkeypatch):
    from app.deployment import rwx_restore
    from app.services.artifacts.control import descriptor
    from tests.test_replica_rwx_restore import fixture_binding
    settings, plan = prepare(tmp_path)
    settings.artifact_backend, settings.artifact_provider = "rwx", "external"
    settings.artifact_root = tmp_path / "artifacts"
    settings.artifact_root.mkdir()
    bound, source = fixture_binding()
    plan = plan.model_copy(update={"storage": bound.storage})
    with get_engine().begin() as connection:
        connection.execute(ArtifactStorageControl.__table__.update().values(active_descriptor=descriptor(settings)))
    proof = dict(source_scope_sha256=bound.storage[2].scope_sha256, namespace=settings.deployment_namespace,
        nfs_source="172.18.0.1:/fresh-target", inode=8194, uid=1000, gid=1000, mode=0o770)
    monkeypatch.setattr(rwx_restore, "verify_rwx_relocation", lambda *args: dict(proof))
    return settings, plan, source, proof


def test_verified_rwx_same_local_descriptor_adopts_and_resumes(tmp_path, monkeypatch):
    tool = load()
    settings, plan, source, _ = prepare_rwx(tmp_path, monkeypatch)
    operation = uuid4().hex
    with MaintenanceOperator(get_engine(), operation_id=operation, owner_id="restore-test", settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            first, counts = tool.rebind_target(session, settings, plan, installation_id="5" * 32,
                operation_id=operation, selection_digest="sha256:" + "7" * 64, rwx_source_binding=source)
        with op.transaction() as session:
            resumed, repeated = tool.rebind_target(session, settings, plan, installation_id="5" * 32,
                operation_id=operation, selection_digest="sha256:" + "7" * 64, rwx_source_binding=source)
            assert session.get(MaintenanceState, 1).enabled
    assert first == resumed and first["artifact_backend"] == "rwx"
    assert counts["invalidated_sessions"] == 1 and repeated["invalidated_sessions"] == 0


def test_rwx_resume_refuses_changed_physical_target_even_with_same_settings(tmp_path, monkeypatch):
    tool = load()
    settings, plan, source, proof = prepare_rwx(tmp_path, monkeypatch)
    operation = uuid4().hex
    with MaintenanceOperator(get_engine(), operation_id=operation, owner_id="restore-test", settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            first, _ = tool.rebind_target(session, settings, plan, installation_id="5" * 32,
                operation_id=operation, selection_digest="sha256:" + "7" * 64, rwx_source_binding=source)
        proof["inode"] += 1
        with pytest.raises(ValueError), op.transaction() as session:
            tool.rebind_target(session, settings, plan, installation_id="5" * 32,
                operation_id=operation, selection_digest="sha256:" + "7" * 64, rwx_source_binding=source)
        with op.transaction() as session:
            assert session.get(ReplicaRuntimeIdentity, 1).storage_receipt_digest == first["storage_receipt_digest"]
            state = session.get(MaintenanceState, 1)
            assert state.enabled and state.epoch == plan.source.epoch + 1


def test_rwx_bad_proof_preserves_source_auth_and_identity(tmp_path, monkeypatch):
    from app.deployment import rwx_restore
    tool = load()
    settings, plan, source, _ = prepare_rwx(tmp_path, monkeypatch)
    def refuse(*args):
        raise ValueError("Unproven NFS relocation")
    monkeypatch.setattr(rwx_restore, "verify_rwx_relocation", refuse)
    operation = uuid4().hex
    with MaintenanceOperator(get_engine(), operation_id=operation, owner_id="restore-test", settings=settings, allow_sqlite=True) as op:
        with pytest.raises(ValueError), op.transaction() as session:
            tool.rebind_target(session, settings, plan, installation_id="5" * 32,
                operation_id=operation, selection_digest="sha256:" + "7" * 64, rwx_source_binding=source)
    with get_engine().connect() as connection:
        assert connection.scalar(select(AuthSession.id)) == "old-session"
        assert connection.scalar(select(ReplicaRuntimeIdentity.installation_id)) == plan.source.installation_id


def test_rwx_mount_change_inside_adoption_refuses_probed_digest(tmp_path, monkeypatch):
    tool = load()
    settings, plan, source, _ = prepare_rwx(tmp_path, monkeypatch)
    operation = uuid4().hex
    with MaintenanceOperator(get_engine(), operation_id=operation, owner_id="restore-test", settings=settings, allow_sqlite=True) as op:
        with pytest.raises(ValueError), op.transaction() as session:
            tool.rebind_target(session, settings, plan, installation_id="5" * 32,
                operation_id=operation, selection_digest="sha256:" + "7" * 64,
                rwx_source_binding=source, rwx_target_sha256="0" * 64)
    with get_engine().connect() as connection:
        assert connection.scalar(select(AuthSession.id)) == "old-session"
        assert connection.scalar(select(ReplicaRuntimeIdentity.installation_id)) == plan.source.installation_id
