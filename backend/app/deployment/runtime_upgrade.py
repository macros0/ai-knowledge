"""Offline, same-schema runtime identity update; never an application startup bypass."""
import json
import re

from sqlalchemy import select, text

from app.db.models import ArtifactStorageControl, AuditLog, AuditOutbox, ReplicaCapacitySlot, ReplicaRuntimeIdentity
from app.services.artifacts.control import descriptor
from app.services.work.maintenance_control import MaintenanceRefused, _require_operator, _state
from .runtime_identity import IDENTITY_KEYS, SCHEMA_REVISION, identity_document


def _recorded(session, fingerprint, epoch):
    ids = {event_id for event_id, values in session.execute(select(AuditLog.event_id, AuditLog.new_value).where(
        AuditLog.action_type == "replica_runtime_upgrade", AuditLog.target_type == "runtime_identity", AuditLog.target_id == fingerprint))
        if values == {"epoch": epoch}}
    fragment = json.dumps({"type": "runtime_identity", "id": fingerprint}, separators=(",", ":"))
    for payload in session.scalars(select(AuditOutbox.payload).where(AuditOutbox.payload.contains(fragment))):
        event = json.loads(payload)
        if event.get("action") == "replica_runtime_upgrade" and event.get("new_value") == {"epoch": epoch}:
            ids.add(event["event_id"])
    if len(ids) > 1:
        raise MaintenanceRefused("Runtime identity upgrade audit is ambiguous")
    return bool(ids)


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
