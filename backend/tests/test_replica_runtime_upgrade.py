"""An installed identity changes only under its owned held maintenance epoch."""
import importlib
import importlib.util
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, MaintenanceState, ReplicaRuntimeIdentity
from app.db.session import get_engine
from app.services.work.maintenance_control import MaintenanceOperator, enter_maintenance
from tests.test_replica_runtime_identity_db import installed as installed


def module():
    assert importlib.util.find_spec("app.deployment.runtime_upgrade") is not None, "Coordinated runtime upgrade is missing"
    return importlib.import_module("app.deployment.runtime_upgrade")


def test_upgrade_preserves_installation_and_holds_queue_with_audited_resume(installed):
    tool = module()
    old = json.loads(installed.runtime_identity_file.read_bytes())
    settings = installed.model_copy(deep=True)
    settings.runtime_image_id = "okf-backend@sha256:" + "9" * 64
    with MaintenanceOperator(get_engine(), operation_id=uuid4().hex, owner_id="upgrade-test", settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            enter_maintenance(session, op.operation_id, kind="upgrade", intended_counts={"frontend": 2, "api": 2, "worker": 2})
        with op.transaction() as session:
            document = tool.rebind_runtime(session, settings, old, operation_id=op.operation_id)
        assert document["installation_id"] == old["installation_id"]
        assert document["runtime_fingerprint"] != old["runtime_fingerprint"]
        with op.transaction() as session:
            assert session.get(MaintenanceState, 1).enabled is True
            assert session.get(ReplicaRuntimeIdentity, 1).fingerprint == document["runtime_fingerprint"]
            assert tool.rebind_runtime(session, settings, old, operation_id=op.operation_id) == document
            assert len(list(session.scalars(select(AuditLog).where(AuditLog.action_type == "replica_runtime_upgrade")))) == 1


@pytest.mark.parametrize("fault", ["not_held", "old_fingerprint", "changed_storage"])
def test_upgrade_refuses_unbound_source_or_storage(installed, fault):
    tool = module()
    old = json.loads(installed.runtime_identity_file.read_bytes())
    settings = installed.model_copy(deep=True)
    settings.runtime_image_id = "okf-backend@sha256:" + "9" * 64
    with MaintenanceOperator(get_engine(), operation_id=uuid4().hex, owner_id="upgrade-test", settings=settings, allow_sqlite=True) as op:
        if fault != "not_held":
            with op.transaction() as session:
                enter_maintenance(session, op.operation_id, kind="upgrade", intended_counts={"frontend": 2, "api": 2, "worker": 2})
        if fault == "old_fingerprint":
            old["runtime_fingerprint"] = "0" * 64
        elif fault == "changed_storage":
            settings.artifact_s3_bucket = "other-bucket"
        with pytest.raises(ValueError), op.transaction() as session:
            tool.rebind_runtime(session, settings, old, operation_id=op.operation_id)
    with get_engine().connect() as connection:
        assert connection.scalar(select(ReplicaRuntimeIdentity.fingerprint)) == installed._runtime_identity.fingerprint


def test_upgrade_audit_failure_cannot_replace_installed_fingerprint(installed, monkeypatch):
    tool = module()
    old = json.loads(installed.runtime_identity_file.read_bytes())
    settings = installed.model_copy(deep=True)
    settings.runtime_image_id = "okf-backend@sha256:" + "9" * 64
    with MaintenanceOperator(get_engine(), operation_id=uuid4().hex, owner_id="upgrade-test", settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            enter_maintenance(session, op.operation_id, kind="upgrade", intended_counts={"frontend": 2, "api": 2, "worker": 2})
        def fail(*_args, **_kwargs):
            raise RuntimeError("audit unavailable")
        monkeypatch.setattr("app.services.audit.record_in_session", fail)
        with pytest.raises(RuntimeError, match="audit"), op.transaction() as session:
            tool.rebind_runtime(session, settings, old, operation_id=op.operation_id)
        with op.transaction() as session:
            assert session.get(ReplicaRuntimeIdentity, 1).fingerprint == old["runtime_fingerprint"]
            assert session.get(MaintenanceState, 1).enabled
