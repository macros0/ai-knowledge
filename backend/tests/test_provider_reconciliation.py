"""A stopped-provider proof, never elapsed time, authorizes exact audited release."""
import importlib
import importlib.util
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import ArtifactStorageControl, AuditLog, MaintenanceState, ReplicaCapacitySlot
from app.db.session import get_engine, session_scope
from app.services.work.coordination import reserve_capacity
from app.services.work.maintenance_control import MaintenanceOperator, enter_maintenance
from tests.test_replica_maintenance import installed_maintenance as installed_maintenance


def module():
    assert importlib.util.find_spec("app.services.work.provider_reconciliation") is not None, "Provider reconciliation is missing"
    return importlib.import_module("app.services.work.provider_reconciliation")


def prepare(settings):
    settings.model_mode, settings.auth_provider, settings.embedding_provider = "stub", "simulation", "fake"
    settings.deployment_namespace = "replica-test-reconcile01"
    settings.llm_base_url = "http://model-stub:8000/v1"
    with session_scope() as session:
        lease = reserve_capacity(session, resource="llm", owner_id="provider-call-owner", limit=1, ttl_seconds=120, release_required=True)
    return lease


def proof(tool, op, settings, lease):
    with op.transaction() as session:
        installation = session.scalar(select(ArtifactStorageControl.installation_id))
        epoch = session.get(MaintenanceState, 1).epoch
    return tool.ProviderStopProof(operation_id=op.operation_id, installation_id=installation,
        namespace=settings.deployment_namespace, namespace_uid=uuid4(), epoch=epoch,
        provider_pod_uid=uuid4(), provider_image_digest="sha256:" + "3" * 64,
        writer_pod_uids=(uuid4(),), writers={k: "stopped" for k in tool.REQUIRED_WRITERS},
        unknown_writers=0, unreachable_nodes=0, provider_pods=0, provider_endpoints=0,
        observation_sha256="4" * 64, leases_sha256=tool.leases_digest([lease]))


def test_reconcile_exact_lease_is_atomic_audited_idempotent_and_keeps_fence(installed_maintenance):
    tool = module()
    settings = installed_maintenance
    lease = prepare(settings)
    with MaintenanceOperator(get_engine(), operation_id=uuid4().hex, owner_id="test-recovery", settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            enter_maintenance(session, op.operation_id, kind="recovery", intended_counts={"frontend": 2, "api": 2, "worker": 2})
        stopped = proof(tool, op, settings, lease)
        with op.transaction() as session:
            assert tool.reconcile_stopped_provider(session, op.operation_id, [lease], proof=stopped, guard=lambda: stopped) == 1
        with op.transaction() as session:
            row = session.get(ReplicaCapacitySlot, lease.id)
            assert row.owner_id is None and row.release_required is False
            assert session.get(MaintenanceState, 1).enabled is True
            event = session.scalar(select(AuditLog).where(AuditLog.action_type == "replica_model_reconcile"))
            assert event.target_id == stopped.digest()
            assert event.new_value == {"epoch": 1, "count": 1}
            assert tool.reconcile_stopped_provider(session, op.operation_id, [lease], proof=stopped, guard=lambda: stopped) == 0


@pytest.mark.parametrize("fault", ["live_provider", "unknown_writer", "unreachable_node", "changed_epoch", "changed_lease", "lost_guard", "changed_after_release", "new_owner"])
def test_uncertain_or_changed_evidence_cannot_release_capacity(installed_maintenance, fault):
    tool = module()
    settings = installed_maintenance
    lease = prepare(settings)
    with MaintenanceOperator(get_engine(), operation_id=uuid4().hex, owner_id="test-recovery", settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            enter_maintenance(session, op.operation_id, kind="recovery", intended_counts={"frontend": 2, "api": 2, "worker": 2})
        stopped = proof(tool, op, settings, lease)
        changed = stopped.model_dump()
        if fault not in {"lost_guard", "changed_after_release", "new_owner"}:
            key = {"live_provider": "provider_pods", "unknown_writer": "unknown_writers", "unreachable_node": "unreachable_nodes",
                "changed_epoch": "epoch", "changed_lease": "leases_sha256"}[fault]
            changed[key] = "0" * 64 if key == "leases_sha256" else 2
        if fault == "new_owner":
            with op.transaction() as session:
                row = session.get(ReplicaCapacitySlot, lease.id)
                row.owner_id, row.fence = "replacement-call-owner", row.fence + 1
        calls = []
        def observe():
            calls.append(True)
            return None if fault == "lost_guard" or fault == "changed_after_release" and len(calls) > 1 else candidate
        with pytest.raises(ValueError), op.transaction() as session:
            candidate = tool.ProviderStopProof(**changed)
            tool.reconcile_stopped_provider(session, op.operation_id, [lease], proof=candidate, guard=observe)
        with op.transaction() as session:
            assert session.get(ReplicaCapacitySlot, lease.id).owner_id == ("replacement-call-owner" if fault == "new_owner" else lease.owner_id)


def test_stdout_only_audit_proves_idempotent_reconciliation(installed_maintenance):
    tool = module()
    settings = installed_maintenance
    lease = prepare(settings)
    settings.audit_db_enabled, settings.audit_stdout_mode = False, "durable"
    with MaintenanceOperator(get_engine(), operation_id=uuid4().hex, owner_id="test-recovery", settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            enter_maintenance(session, op.operation_id, kind="recovery", intended_counts={"frontend": 2, "api": 2, "worker": 2})
        stopped = proof(tool, op, settings, lease)
        with op.transaction() as session:
            assert tool.reconcile_stopped_provider(session, op.operation_id, [lease], proof=stopped, guard=lambda: stopped) == 1
        with op.transaction() as session:
            assert session.scalar(select(AuditLog.id).where(AuditLog.action_type == "replica_model_reconcile")) is None
            assert tool.reconcile_stopped_provider(session, op.operation_id, [lease], proof=stopped, guard=lambda: stopped) == 0


def test_audit_failure_rolls_back_capacity_release(installed_maintenance, monkeypatch):
    tool = module()
    settings = installed_maintenance
    lease = prepare(settings)
    with MaintenanceOperator(get_engine(), operation_id=uuid4().hex, owner_id="test-recovery", settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            enter_maintenance(session, op.operation_id, kind="recovery", intended_counts={"frontend": 2, "api": 2, "worker": 2})
        stopped = proof(tool, op, settings, lease)
        def fail(*_args, **_kwargs):
            raise RuntimeError("audit unavailable")
        monkeypatch.setattr("app.services.audit.record_in_session", fail)
        with pytest.raises(RuntimeError, match="audit"), op.transaction() as session:
            tool.reconcile_stopped_provider(session, op.operation_id, [lease], proof=stopped, guard=lambda: stopped)
        with op.transaction() as session:
            assert session.get(ReplicaCapacitySlot, lease.id).owner_id == lease.owner_id
