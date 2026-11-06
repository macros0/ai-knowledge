"""Maintenance invalidates old writers; operator uncertainty never releases work."""
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.config import get_settings
from app.db.session import get_engine, get_session_factory, session_scope
from app.services.work.maintenance import (MaintenanceOperator, MaintenanceRefused,
    REQUIRED_WRITERS, bind_runtime_epoch, enter_maintenance, leave_maintenance)


@pytest.fixture
def installed_maintenance():
    from app.db.models import ArtifactStorageControl, MaintenanceState
    engine = get_engine()
    with engine.begin() as conn:
        installation = conn.scalar(select(ArtifactStorageControl.installation_id))
        conn.execute(MaintenanceState.__table__.insert().values(id=1, installation_id=installation,
            epoch=0, enabled=False, intended_counts_json={}, writer_receipts_json={}))
    settings = get_settings()
    settings._maintenance_epoch = 0
    return settings


@contextmanager
def operator(operation_id=None):
    with MaintenanceOperator(get_engine(), operation_id=operation_id or uuid4().hex,
            owner_id="test-operator", allow_sqlite=True) as op:
        yield op


def enter(op):
    with op.transaction() as session:
        return enter_maintenance(session, operation_id=op.operation_id, kind="backup",
            intended_counts={"frontend": 2, "api": 2, "worker": 2})


def stopped():
    return {writer: "stopped" for writer in REQUIRED_WRITERS}


def test_enter_blocks_new_request_writes_and_exit_retires_old_process(installed_maintenance, monkeypatch):
    from app.db.models import ConfigRevision
    with operator() as op:
        assert enter(op) == 1
        with pytest.raises(MaintenanceRefused):
            with session_scope() as s:
                s.add(ConfigRevision(domain="must-not-commit", revision=1))
        with op.transaction() as s:
            assert leave_maintenance(s, operation_id=op.operation_id, writer_receipts=stopped()) == 1
    with pytest.raises(MaintenanceRefused, match="epoch"):
        with session_scope():
            pass
    fresh = installed_maintenance.model_copy(deep=True)
    fresh._maintenance_epoch = None
    monkeypatch.setattr("app.config.get_settings", lambda: fresh)
    with get_session_factory()() as s:
        assert bind_runtime_epoch(s, fresh) == 1
    with session_scope() as s:
        assert s.get(ConfigRevision, "must-not-commit") is None
        s.add(ConfigRevision(domain="new-process", revision=1))


def test_direct_orm_writer_cannot_bypass_maintenance(installed_maintenance):
    from app.db.models import ConfigRevision
    with operator() as op:
        enter(op)
        with pytest.raises(MaintenanceRefused):
            with get_session_factory().begin() as s:
                s.add(ConfigRevision(domain="direct", revision=1))


def test_same_operation_resumes_without_another_epoch(installed_maintenance):
    operation_id = uuid4().hex
    with operator(operation_id) as op:
        assert enter(op) == 1
    with operator(operation_id) as op:
        assert enter(op) == 1


def test_other_operation_and_concurrent_operator_are_refused(installed_maintenance):
    with operator() as op:
        enter(op)
        with pytest.raises(MaintenanceRefused):
            with operator():
                pass
    with operator() as other:
        with pytest.raises(MaintenanceRefused):
            enter(other)


@pytest.mark.parametrize("receipts", [{}, {"api": "stopped"},
    {writer: "unknown" for writer in REQUIRED_WRITERS}])
def test_unknown_writer_cannot_end_maintenance(installed_maintenance, receipts):
    from app.db.models import MaintenanceState
    with operator() as op:
        enter(op)
        with pytest.raises(MaintenanceRefused):
            with op.transaction() as s:
                leave_maintenance(s, operation_id=op.operation_id, writer_receipts=receipts)
        with op.transaction() as s:
            assert s.get(MaintenanceState, 1).enabled is True


def test_uncertain_provider_is_never_released_by_maintenance(installed_maintenance):
    from app.db.models import ReplicaCapacitySlot
    from app.services.work.coordination import reserve_capacity
    with session_scope() as s:
        lease = reserve_capacity(s, resource="llm", owner_id="uncertain-call", limit=1,
            ttl_seconds=1, release_required=True)
    with operator() as op:
        enter(op)
        with pytest.raises(MaintenanceRefused, match="[Pp]rovider"):
            with op.transaction() as s:
                leave_maintenance(s, operation_id=op.operation_id, writer_receipts=stopped())
        with op.transaction() as s:
            assert s.get(ReplicaCapacitySlot, lease.id).owner_id == "uncertain-call"


def test_existing_storage_migration_excludes_replica_operation(installed_maintenance):
    from app.db.models import ArtifactStorageControl
    with get_engine().begin() as conn:
        conn.execute(update(ArtifactStorageControl).values(maintenance_token=uuid4().hex))
    with operator() as op:
        with pytest.raises(MaintenanceRefused, match="storage"):
            enter(op)


def test_old_work_claim_cannot_finish_after_maintenance_exit(installed_maintenance, monkeypatch):
    from app.services.work.store import enqueue, claim, finish
    from app.services.work.models import WorkSpec, LostOwnership
    with session_scope() as s:
        enqueue(s, WorkSpec("probe", 1, {}, "maintenance-probe"))
        old = claim(s, owner_id="old-worker", supported={"probe": {1}}, lease_seconds=120)
    with operator() as op:
        enter(op)
        with op.transaction() as s:
            leave_maintenance(s, operation_id=op.operation_id, writer_receipts=stopped())
    fresh = installed_maintenance.model_copy(deep=True)
    fresh._maintenance_epoch = None
    monkeypatch.setattr("app.config.get_settings", lambda: fresh)
    with get_session_factory()() as s:
        bind_runtime_epoch(s, fresh)
    with pytest.raises(LostOwnership):
        with get_session_factory().begin() as s:
            finish(s, old, outcome="succeeded")


def test_enter_and_audit_are_one_transaction(installed_maintenance, monkeypatch):
    from app.db.models import MaintenanceState
    from app.services import audit
    def refused(*_a, **_k):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(audit, "record_in_session", refused)
    with operator() as op:
        with pytest.raises(RuntimeError, match="audit"):
            enter(op)
        with op.transaction() as s:
            state = s.get(MaintenanceState, 1)
            assert state.epoch == 0 and state.enabled is False


def test_owned_operator_can_fence_an_installed_retired_runtime_without_loading_it(installed_maintenance, monkeypatch):
    from app.db.models import ArtifactStorageControl, ReplicaRuntimeIdentity
    settings = installed_maintenance
    with get_engine().begin() as conn:
        installation = conn.scalar(select(ArtifactStorageControl.installation_id))
        conn.execute(ReplicaRuntimeIdentity.__table__.insert().values(id=1, installation_id=installation,
            fingerprint="f" * 64, storage_receipt_digest="sha256:" + "a" * 64,
            schema_revision="rpl04_runtime_identity", created_at=datetime.now(timezone.utc)))
    def forbidden():
        pytest.fail("Offline operator must not load retired live settings")
    monkeypatch.setattr("app.config.get_settings", forbidden)
    with MaintenanceOperator(get_engine(), operation_id=uuid4().hex, owner_id="upgrade-operator",
            settings=settings, allow_sqlite=True) as op:
        assert enter(op) == 1


def test_revalidating_same_process_cannot_adopt_a_new_epoch(installed_maintenance):
    with operator() as op:
        enter(op)
        with op.transaction() as s:
            leave_maintenance(s, op.operation_id, writer_receipts=stopped())
    with get_session_factory()() as s, pytest.raises(MaintenanceRefused, match="epoch"):
        bind_runtime_epoch(s, installed_maintenance)
    assert installed_maintenance._maintenance_epoch == 0


def test_replica_operation_owns_the_storage_migration_mutex(installed_maintenance):
    from app.db.models import ArtifactStorageControl
    with operator() as op:
        enter(op)
        with op.transaction() as s:
            assert s.get(ArtifactStorageControl, 1).maintenance_token == op.operation_id
        assert enter(op) == 1
        with op.transaction() as s:
            leave_maintenance(s, op.operation_id, writer_receipts=stopped())
            assert s.get(ArtifactStorageControl, 1).maintenance_token is None


def test_completed_storage_migration_does_not_permanently_block_backup(installed_maintenance):
    from app.db.models import ArtifactStorageControl, ArtifactStorageMigration
    migration_id = uuid4().hex
    with get_engine().begin() as conn:
        installation = conn.scalar(select(ArtifactStorageControl.installation_id))
        conn.execute(ArtifactStorageMigration.__table__.insert().values(id=migration_id,
            installation_id=installation, plan_hash="1" * 64, source_revision="2" * 64,
            source_store_id="before", target_store_id="after", status="cutover",
            backup_hash="3" * 64, verified_hash="1" * 64, receipts={}))
        conn.execute(update(ArtifactStorageControl).values(active_store_id="after", migration_id=migration_id))
    with operator() as op:
        assert enter(op) == 1
