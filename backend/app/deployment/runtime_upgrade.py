"""Owned offline identity updates; never an application startup bypass."""
from hashlib import sha256
import json
import re

from sqlalchemy import or_, select, text

from app.db.models import ArtifactStorageControl, ArtifactStorageMigration, AuditLog, AuditOutbox, ChatRun, MaintenanceState, ReplicaCapacitySlot, ReplicaRuntimeIdentity, WorkItem
from app.services.artifacts.control import descriptor
from app.services.work.maintenance_control import MaintenanceRefused, _require_operator, _state
from .runtime_identity import IDENTITY_KEYS, SCHEMA_REVISION, identity_document


def _recorded(session, fingerprint, epoch, *, action="replica_runtime_upgrade", target_type="runtime_identity"):
    ids = {event_id for event_id, values in session.execute(select(AuditLog.event_id, AuditLog.new_value).where(
        AuditLog.action_type == action, AuditLog.target_type == target_type, AuditLog.target_id == fingerprint))
        if values == {"epoch": epoch}}
    fragment = json.dumps({"type": target_type, "id": fingerprint}, separators=(",", ":"))
    for payload in session.scalars(select(AuditOutbox.payload).where(AuditOutbox.payload.contains(fragment))):
        event = json.loads(payload)
        if event.get("action") == action and event.get("new_value") == {"epoch": epoch}:
            ids.add(event["event_id"])
    if len(ids) > 1:
        raise MaintenanceRefused("Runtime identity upgrade audit is ambiguous")
    return bool(ids)


def adopt_schema_upgrade(session, settings, expected_old_document, *, operation_id,
        previous_revision, intended_counts, writer_receipts):
    """Claim the real rpl05 migration fence and rebind, leaving all writers held.

    Receipts are the caller's verified stop/drain evidence, not an instruction to
    kill processes or clear uncertain work. Provider/corpus/backup verification
    and a separate explicit release remain the offline coordinator's duties.
    """
    from app.services.work.maintenance_control import REQUIRED_WRITERS
    op = _require_operator(session, operation_id)
    old = dict(expected_old_document)
    if (op.settings is not settings or operation_id == "0" * 32
            or (previous_revision, SCHEMA_REVISION) != ("rpl04_runtime_identity", "rpl05_maintenance_epoch")
            or set(old) != IDENTITY_KEYS or type(old.get("version")) is not int or old["version"] != 1
            or type(intended_counts) is not dict or set(intended_counts) != {"frontend", "api", "worker"}
            or any(type(n) is not int or not 0 <= n <= 64 for n in intended_counts.values())
            or type(writer_receipts) is not dict or set(writer_receipts) != REQUIRED_WRITERS
            or any(not isinstance(value, str) or value not in {"stopped", "drained"} for value in writer_receipts.values())):
        raise MaintenanceRefused("Explicit supported schema transition and verified writer receipts required")
    proof = sha256(json.dumps({"source": old, "previous_revision": previous_revision,
        "target_revision": SCHEMA_REVISION, "operation_id": operation_id, "owner_id": op.owner_id,
        "intended_counts": intended_counts, "writer_receipts": writer_receipts},
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    row = _state(session, exclusive=True)
    control = session.scalar(select(ArtifactStorageControl).where(ArtifactStorageControl.id == 1).with_for_update())
    installed = session.get(ReplicaRuntimeIdentity, 1, with_for_update=True)
    if (row is None or not row["enabled"] or row["operation_kind"] != "upgrade"
            or row["installation_id"] != old.get("installation_id") or control is None or installed is None
            or installed.schema_revision != SCHEMA_REVISION
            or session.execute(text("SELECT version_num FROM alembic_version")).scalars().all() != [SCHEMA_REVISION]
            or session.scalar(select(WorkItem.id).where(or_(WorkItem.status == "running",
                WorkItem.owner_id.is_not(None), WorkItem.lease_until.is_not(None))).limit(1))
            or session.scalar(select(ChatRun.id).where(ChatRun.status == "running").limit(1))
            or session.scalar(select(ReplicaCapacitySlot.id).where(
                ReplicaCapacitySlot.resource == "llm", or_(ReplicaCapacitySlot.owner_id.is_not(None),
                    ReplicaCapacitySlot.release_required.is_(True))).limit(1))):
        raise MaintenanceRefused("Schema adoption requires a held migrated installation with no uncertain work")
    if control.migration_id:
        history = session.get(ArtifactStorageMigration, control.migration_id)
        if (history is None or history.installation_id != control.installation_id or history.status != "cutover"
                or history.target_store_id != control.active_store_id or history.verified_hash != history.plan_hash):
            raise MaintenanceRefused("Artifact storage migration outcome is unknown")
    recorded = _recorded(session, proof, 2, action="replica_maintenance_enter", target_type="replica_schema_upgrade")
    if (row["epoch"], row["operation_id"], row["owner_id"], row["intended_counts_json"], row["writer_receipts_json"],
            control.maintenance_token) == (1, "0" * 32, "schema-migration", {}, {}, "0" * 32):
        if recorded or installed.fingerprint != old.get("runtime_fingerprint"):
            raise MaintenanceRefused("Migrated schema source differs from the expected identity")
        state = session.get(MaintenanceState, 1, populate_existing=True)
        state.epoch, state.operation_id, state.owner_id = 2, operation_id, op.owner_id
        state.intended_counts_json, state.writer_receipts_json = dict(intended_counts), dict(writer_receipts)
        control.maintenance_token = operation_id
        from app.services import audit
        from app.services.audit_event import AuditContext
        audit.record_in_session(session, action_type="replica_maintenance_enter", user_id="system", username="system",
            target_type="replica_schema_upgrade", target_id=proof, new_value={"epoch": 2},
            event_context=AuditContext(actor_type="system"), settings=settings)
        session.flush()
    elif ((row["epoch"], row["operation_id"], row["owner_id"], row["intended_counts_json"], row["writer_receipts_json"],
            control.maintenance_token) != (2, operation_id, op.owner_id, intended_counts, writer_receipts, operation_id)
            or not recorded):
        raise MaintenanceRefused("Schema adoption ownership or recorded source proof differs")
    return rebind_runtime(session, settings, old, operation_id=operation_id)


def rebind_runtime(session, settings, expected_old_document, *, operation_id):
    op = _require_operator(session, operation_id)
    old = dict(expected_old_document)
    if (op.settings is not settings or set(old) != IDENTITY_KEYS or type(old.get("version")) is not int or old["version"] != 1
            or settings.okf_deployment_schema != "replicas-v1" or not settings.runtime_image_id
            or not isinstance(old.get("runtime_fingerprint"), str) or not re.fullmatch(r"[0-9a-f]{64}", old["runtime_fingerprint"])):
        raise MaintenanceRefused("Explicit owned runtime upgrade identity required")
    document = identity_document(settings, installation_id=old["installation_id"], storage_receipt_digest=old["storage_receipt_digest"])
    if ({k: v for k, v in old.items() if k != "runtime_fingerprint"} != {k: v for k, v in document.items() if k != "runtime_fingerprint"}
            or document["runtime_fingerprint"] == old["runtime_fingerprint"]):
        raise MaintenanceRefused("Runtime upgrade must preserve the installation and change its fingerprint")
    state = _state(session, exclusive=True)
    control = session.scalar(select(ArtifactStorageControl).where(ArtifactStorageControl.id == 1).with_for_update())
    installed = session.get(ReplicaRuntimeIdentity, 1, with_for_update=True)
    if (state is None or not state["enabled"] or state["operation_kind"] not in {"upgrade", "recovery"}
            or state["operation_id"] != operation_id or state["owner_id"] != op.owner_id
            or state["installation_id"] != old["installation_id"] or control is None or installed is None
            or control.installation_id != old["installation_id"] or control.maintenance_token != operation_id
            or control.active_store_id != settings.artifact_store_id or control.active_descriptor != descriptor(settings)
            or installed.installation_id != old["installation_id"] or installed.storage_receipt_digest != old["storage_receipt_digest"]
            or installed.schema_revision != SCHEMA_REVISION
            or session.execute(text("SELECT version_num FROM alembic_version")).scalars().all() != [SCHEMA_REVISION]
            or session.scalar(select(ReplicaCapacitySlot.id).where(ReplicaCapacitySlot.resource == "llm", ReplicaCapacitySlot.owner_id.is_not(None)).limit(1))):
        raise MaintenanceRefused("Runtime upgrade requires its held unchanged installation and reconciled provider state")
    recorded = _recorded(session, document["runtime_fingerprint"], state["epoch"])
    if installed.fingerprint == document["runtime_fingerprint"] and recorded:
        return document
    if installed.fingerprint != old["runtime_fingerprint"] or recorded:
        raise MaintenanceRefused("Installed runtime differs from the expected upgrade source")
    installed.fingerprint = document["runtime_fingerprint"]
    from app.services.work.store import _now
    installed.created_at = _now(session)
    from app.services import audit
    from app.services.audit_event import AuditContext
    audit.record_in_session(session, action_type="replica_runtime_upgrade", user_id="system", username="system",
        target_type="runtime_identity", target_id=document["runtime_fingerprint"], new_value={"epoch": state["epoch"]},
        event_context=AuditContext(actor_type="system"), settings=settings)
    session.flush()
    return document
