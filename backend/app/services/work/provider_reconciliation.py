"""Trusted offline reconciliation for the owned synthetic provider stop recipe.

No timer, environment flag, API request or generic stop claim releases a slot.
The coordinator supplies fresh physical observations through a mandatory guard.
Real/external providers require their own implemented and qualified recipe.
"""
from hashlib import sha256
import json
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.db.models import ArtifactStorageControl, AuditLog, AuditOutbox, ReplicaCapacitySlot
from .coordination import CapacityLease, complete_capacity, _resource
from .maintenance_control import REQUIRED_WRITERS, MaintenanceRefused, _require_operator, _state

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Hex32 = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Zero = Annotated[int, Field(strict=True, ge=0, le=0)]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def leases_digest(leases):
    values = []
    for lease in leases:
        if (not isinstance(lease, CapacityLease) or lease.resource != "llm"
                or not re.fullmatch(r"[0-9a-f]{32}", lease.id) or not isinstance(lease.owner_id, str)
                or not 1 <= len(lease.owner_id) <= 128 or type(lease.fence) is not int or lease.fence < 1):
            raise MaintenanceRefused("Original provider lease identities required")
        values.append({"id": lease.id, "resource": lease.resource, "owner": lease.owner_id, "fence": lease.fence})
    if not values or len(values) > 10000 or len({v["id"] for v in values}) != len(values):
        raise MaintenanceRefused("Complete distinct original provider lease set required")
    return sha256(canonical(sorted(values, key=lambda v: v["id"]))).hexdigest()


class ProviderStopProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    recipe: Literal["owned-model-stub-pod-stop-v1"] = "owned-model-stub-pod-stop-v1"
    operation_id: Hex32
    installation_id: Hex32
    namespace: Annotated[str, Field(pattern=r"^replica-test-[a-z0-9]{8,32}$")]
    namespace_uid: UUID
    epoch: Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
    provider_name: Literal["model-stub"] = "model-stub"
    provider_pod_uid: UUID
    provider_image_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    writer_pod_uids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=1024)]
    writers: dict[str, Literal["stopped"]]
    unknown_writers: Zero
    unreachable_nodes: Zero
    provider_pods: Zero
    provider_endpoints: Zero
    observation_sha256: Digest
    leases_sha256: Digest

    @model_validator(mode="after")
    def all_stopped(self):
        if set(self.writers) != REQUIRED_WRITERS or len(set(self.writer_pod_uids)) != len(self.writer_pod_uids):
            raise ValueError("All distinct writers must be proven stopped")
        return self

    def digest(self):
        return sha256(canonical(self.model_dump(mode="json"))).hexdigest()


def _guard(guard, proof):
    if not callable(guard):
        raise MaintenanceRefused("Live provider stop observation guard required")
    actual = guard()
    if type(actual) is not ProviderStopProof or ProviderStopProof.model_validate_json(actual.model_dump_json()) != proof:
        raise MaintenanceRefused("Provider stop observations changed")


def _previous_event(session, proof_digest):
    ids = set(session.scalars(select(AuditLog.event_id).where(AuditLog.action_type == "replica_model_reconcile",
        AuditLog.target_type == "provider_stop_proof", AuditLog.target_id == proof_digest)))
    fragment = json.dumps({"type": "provider_stop_proof", "id": proof_digest}, separators=(",", ":"))
    for payload in session.scalars(select(AuditOutbox.payload).where(AuditOutbox.payload.contains(fragment))):
        event = json.loads(payload)
        if event.get("action") == "replica_model_reconcile":
            ids.add(event["event_id"])
    if len(ids) > 1:
        raise MaintenanceRefused("Provider reconciliation audit is ambiguous")
    return bool(ids)


def reconcile_stopped_provider(session, operation_id, leases, *, proof, guard):
    op = _require_operator(session, operation_id)
    if not isinstance(leases, (tuple, list)) or not 1 <= len(leases) <= 10000:
        raise MaintenanceRefused("Bounded original provider reservation set required")
    leases = tuple(leases)
    if type(proof) is not ProviderStopProof:
        raise MaintenanceRefused("Typed provider stop observations required")
    # Revalidate even a model_copy/model_construct created by a trusted caller.
    proof = ProviderStopProof.model_validate_json(proof.model_dump_json())
    settings = op.settings
    if (settings is None or getattr(settings, "model_mode", None) != "stub"
            or getattr(settings, "auth_provider", None) != "simulation"
            or getattr(settings, "embedding_provider", None) != "fake"
            or getattr(settings, "environment", "production") not in {"development", "test"}
            or getattr(settings, "llm_base_url", None) != "http://model-stub:8000/v1"
            or getattr(settings, "deployment_namespace", None) != proof.namespace
            or proof.operation_id != operation_id or leases_digest(leases) != proof.leases_sha256):
        raise MaintenanceRefused("This provider reconciliation recipe is not qualified for these inputs")
    _guard(guard, proof)
    state = _state(session, exclusive=True)
    control = session.scalar(select(ArtifactStorageControl).where(ArtifactStorageControl.id == 1).with_for_update())
    if (state is None or not state["enabled"] or state["operation_kind"] != "recovery" or state["operation_id"] != operation_id
            or state["owner_id"] != op.owner_id or state["epoch"] != proof.epoch or state["installation_id"] != proof.installation_id
            or control is None or control.installation_id != proof.installation_id or control.maintenance_token != operation_id):
        raise MaintenanceRefused("Owned recovery fence differs from provider observations")
    _resource(session, "llm")
    rows = list(session.scalars(select(ReplicaCapacitySlot).where(ReplicaCapacitySlot.resource == "llm")
        .with_for_update().execution_options(populate_existing=True)))
    owned = [r for r in rows if r.owner_id is not None]
    prior = _previous_event(session, proof.digest())
    if not owned and prior:
        by_id = {r.id: r for r in rows}
        if any(lease.id not in by_id or by_id[lease.id].fence != lease.fence or by_id[lease.id].release_required
                or by_id[lease.id].expires_at is not None or by_id[lease.id].protected_until is not None for lease in leases):
            raise MaintenanceRefused("Previously reconciled lease identity changed")
        _guard(guard, proof)
        return 0
    current = {(r.id, r.owner_id, r.fence) for r in owned if r.release_required}
    expected = {(lease.id, lease.owner_id, lease.fence) for lease in leases}
    if prior or len(owned) != len(current) or current != expected:
        raise MaintenanceRefused("Original provider reservation set changed")
    for lease in leases:
        complete_capacity(session, lease)  # Completion identity, never expiry or an unowned generic release.
    from app.services import audit
    from app.services.audit_event import AuditContext
    audit.record_in_session(session, action_type="replica_model_reconcile", user_id="system", username="system",
        target_type="provider_stop_proof", target_id=proof.digest(), new_value={"epoch": proof.epoch, "count": len(leases)},
        event_context=AuditContext(actor_type="system"), settings=settings)
    _guard(guard, proof)
    session.flush()
    return len(leases)
