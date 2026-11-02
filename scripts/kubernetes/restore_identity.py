"""Offline identity adoption after verified recovery into new isolated storage.

Mount this operator script outside the application's package in the exact saved
image. The common fingerprint is computed there, with the target's real settings,
packages and trust. This program keeps maintenance enabled and never starts jobs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from hashlib import sha256
from pathlib import Path

# In the saved backend image app/ is under its working directory /app.
if Path("/app/app").is_dir():
    sys.path.insert(0, "/app")


def operator_database_url(settings, path):
    """A projected operator credential may change only the selected SQL principal.

    Settings keep the application credentials used by the keyed identity.
    Kubernetes Secret projections legitimately use symlinks; the opened target
    must be a bounded regular file. Nonblocking open refuses FIFOs without
    waiting for a writer. The Job must mount this separate Secret read-only.
    """
    if path is None:
        return settings.db_url
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError()
            raw = stream.read(16385)
        if not raw or len(raw) > 16384:
            raise ValueError()
        value = raw.decode("utf-8").strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError()
        expected, actual = make_url(settings.db_url), make_url(value)
        if (expected.drivername not in {"postgresql", "postgresql+psycopg"}
                or actual.drivername != expected.drivername or not actual.username or not actual.password
                or not expected.host or not expected.database
                or expected.port is not None and not 1 <= expected.port <= 65535
                or actual.host != expected.host or actual.database != expected.database
                or (5432 if actual.port is None else actual.port) != (5432 if expected.port is None else expected.port)
                or actual.query != expected.query
                or expected.query.get("sslmode") != "verify-full" or not expected.query.get("sslrootcert")):
            raise ValueError()
        return value
    except (OSError, ValueError, UnicodeError, ArgumentError):
        raise ValueError("Operator database file does not match the verified application target") from None


def rebind_target(session, settings, plan, *, installation_id, operation_id, selection_digest):
    from app.db.models import (
        ArtifactStorageControl,
        AuthSession,
        MaintenanceState,
        ReplicaCapacitySlot,
        ReplicaRuntimeIdentity,
    )
    from app.deployment.bootstrap import _receipt, canonical
    from app.deployment.runtime_identity import SCHEMA_REVISION, identity_document
    from app.services.artifacts.control import descriptor
    from app.services.audit_hold import validate_outbox_contract
    from app.services.work.maintenance_control import _require_operator
    from app.services.work.store import _now
    from sqlalchemy import func, inspect, select, text

    op = _require_operator(session, operation_id)
    if (not re.fullmatch(r"[0-9a-f]{32}", installation_id) or installation_id == plan.source.installation_id
            or settings.deployment_namespace == plan.source.namespace or settings.artifact_backend != "s3"
            or plan.schema_revision != SCHEMA_REVISION or plan.source.epoch >= 2**63 - 1):
        raise ValueError("New supported recovery identity required")
    if session.execute(text("SELECT version_num FROM alembic_version")).scalars().all() != [SCHEMA_REVISION]:
        raise ValueError("Restored schema differs")
    receipt = _receipt(settings, installation_id, selection_digest)
    document = identity_document(settings, installation_id=installation_id,
        storage_receipt_digest="sha256:" + sha256(canonical(receipt)).hexdigest())
    identity = session.get(ReplicaRuntimeIdentity, 1, with_for_update=True)
    state = session.get(MaintenanceState, 1, with_for_update=True)
    control = session.get(ArtifactStorageControl, 1, with_for_update=True)
    if (identity is None or state is None or control is None or not state.enabled
            or session.scalar(select(func.count()).select_from(ReplicaCapacitySlot).where(
                ReplicaCapacitySlot.resource == "llm", ReplicaCapacitySlot.owner_id.is_not(None)))):
        raise ValueError("Recovery fence or provider requests are uncertain")
    if identity.installation_id == installation_id:
        if (identity.fingerprint != document["runtime_fingerprint"] or identity.storage_receipt_digest != document["storage_receipt_digest"]
                or state.installation_id != installation_id or state.operation_id != operation_id or state.owner_id != op.owner_id
                or state.operation_kind != "restore" or state.epoch != plan.source.epoch + 1
                or control.installation_id != installation_id or control.maintenance_token != operation_id
                or control.active_descriptor != descriptor(settings) or session.scalar(select(func.count()).select_from(AuthSession))):
            raise ValueError("Recovery identity resume differs")
        return document, {"invalidated_sessions": 0, "invalidated_oidc_flows": 0, "held_audit_events": 0}
    if (identity.installation_id != plan.source.installation_id or identity.schema_revision != SCHEMA_REVISION
            or sha256(canonical(identity.fingerprint)).hexdigest() != plan.model_vector_sha256
            or state.installation_id != plan.source.installation_id or state.operation_id != plan.backup_id
            or state.operation_kind != "backup" or state.epoch != plan.source.epoch
            or control.installation_id != plan.source.installation_id or control.maintenance_token != plan.backup_id
            or control.active_store_id != settings.artifact_store_id or control.migration_id is not None
            or control.active_descriptor == descriptor(settings) or control.readonly_roots):
        raise ValueError("Restored source identity or storage differs")
    validate_outbox_contract(session.connection())
    invalidated = session.execute(text("DELETE FROM auth_sessions")).rowcount
    flows = 0
    if "oidc_login_flows" in inspect(session.connection()).get_table_names():
        flows = session.execute(text("DELETE FROM oidc_login_flows")).rowcount
    held = session.execute(text("UPDATE audit_outbox SET status='held',last_error_code='restored_backup' WHERE status='pending'")).rowcount
    identity.installation_id, identity.fingerprint = installation_id, document["runtime_fingerprint"]
    identity.storage_receipt_digest, identity.created_at = document["storage_receipt_digest"], _now(session)
    control.installation_id, control.active_descriptor, control.maintenance_token = installation_id, descriptor(settings), operation_id
    state.installation_id, state.epoch = installation_id, plan.source.epoch + 1
    state.operation_id, state.operation_kind, state.owner_id = operation_id, "restore", op.owner_id
    state.started_at, state.completed_at, state.writer_receipts_json = _now(session), None, {}
    from app.services import audit
    from app.services.audit_event import AuditContext
    audit.record_in_session(session, action_type="replica_maintenance_enter", user_id="system", username="system",
        target_type="replica_operation", target_id=operation_id, new_value={"epoch": state.epoch},
        event_context=AuditContext(actor_type="system"), settings=settings)
    session.flush()
    return document, {"invalidated_sessions": invalidated, "invalidated_oidc_flows": flows, "held_audit_events": held}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--backup-plan", type=Path, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--operator-db-file", type=Path,
        help="Separate read-only projected operator credential for the same verified PostgreSQL target")
    args = parser.parse_args()
    try:
        from app.db.session import configure_database, get_engine
        from app.deployment.bootstrap import (
            OperatorSettings,
            canonical,
            probe_artifacts,
            write_identity,
        )
        from app.deployment.replica_backup import BackupPlan
        from app.deployment.storage_selection import StorageSelection
        from app.services.work.maintenance_control import MaintenanceOperator
        settings = OperatorSettings(_env_file=None)
        selection = StorageSelection.model_validate_json(args.selection.read_bytes())
        selection.validate_runtime(settings)
        plan = BackupPlan.model_validate_json(args.backup_plan.read_bytes())
        if (settings.deployment_namespace != selection.namespace or not settings.deployment_namespace.startswith("replica-test-")
                or settings.deployment_namespace == plan.source.namespace or selection.installation_id == plan.source.installation_id
                or settings.artifact_backend != "s3"):
            raise ValueError("Explicit isolated recovery target required")
        configure_database(operator_database_url(settings, args.operator_db_file))
        selection_digest = "sha256:" + sha256(canonical(selection.model_dump())).hexdigest()
        with MaintenanceOperator(get_engine(), operation_id=args.operation_id, owner_id="owned-lab-restore", settings=settings) as op:
            probe_artifacts(settings, installation_id=selection.installation_id, selection_digest=selection_digest)
            with op.transaction() as session:
                document, counts = rebind_target(session, settings, plan, installation_id=selection.installation_id,
                    operation_id=args.operation_id, selection_digest=selection_digest)
            write_identity(settings.runtime_identity_file, document)
        print("REPLICA_RESTORE_IDENTITY:" + json.dumps({"identity": document, "counts": counts,
            "operation_id": args.operation_id, "jobs_held": True, "production_schedule": "disabled"}, sort_keys=True), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 -- never expose raw provider/settings failures from an operator Job.
        print(json.dumps({"restore_identity": "refused", "type": type(exc).__name__, "jobs_held": True}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
