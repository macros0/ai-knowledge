"""Lease endpoints recheck authenticated roles and resource access on every call."""
from fastapi import APIRouter, Depends, Query

from app.auth.models import User
from app.auth.service import require_role
from app.db.session import session_scope
from app.models.reference_editing import EditToken, EditSession, EditRenew, EditRevoke, EditAcquireMany
from app.services.reference_resources import ReferenceKey, require_access, require_resource
from app.services.reference_concurrency import ReferenceError
from app.services import reference_edit_lock as locks
from app.config import get_settings

router = APIRouter(prefix="/reference-edit-locks", tags=["reference-editing"])


@router.get("/summary")
def summary(user: User = Depends(require_role("admin"))):
    from app.services.diagnostics.reference_counters import snapshot
    return snapshot()


def request_edit_session(request):
    token = request.headers.get("X-Reference-Edit-Token")
    epoch = request.headers.get("X-Reference-Edit-Epoch")
    return {"edit_token": token, "lease_epoch": int(epoch)} if token and epoch and epoch.isdecimal() else None


def resource_key(resource_type: str, resource_id: str, kind: str | None = Query(default=None),
                 org_id: int | None = Query(default=None, ge=1)) -> ReferenceKey:
    try:
        return ReferenceKey(resource_type, resource_id, kind=kind, org_id=org_id)
    except ValueError as exc:
        raise ReferenceError("invalid_request", 422) from exc


@router.post("/acquire-many")
def acquire_many(body: EditAcquireMany, user: User = Depends(require_role("editor", "admin"))):
    try:
        keys = [ReferenceKey(item.resource_type, item.resource_id, item.kind, item.org_id) for item in body.resources]
    except ValueError as exc:
        raise ReferenceError("invalid_request", 422) from exc
    with session_scope() as session:
        if not get_settings().reference_edit_locks_enabled:
            for key in keys:
                require_access(key, user)
                require_resource(session, key)
            return {"resources": [{"resource_type": key.resource_type, "resource_id": key.resource_id,
                **locks.edit_lock_status(session, key)} for key in keys]}
        statuses = locks.acquire_edit_locks(session, keys, user,
            {key: item.edit_token for key, item in zip(keys, body.resources)},
            {key: item.lease_epoch for key, item in zip(keys, body.resources) if item.lease_epoch is not None})
        return {"resources": [{"resource_type": key.resource_type, "resource_id": key.resource_id,
            **statuses[key]} for key in keys]}


@router.get("/{resource_type}/{resource_id}")
def status(key: ReferenceKey = Depends(resource_key), user: User = Depends(require_role("editor", "admin"))):
    with session_scope() as session:
        require_access(key, user)
        require_resource(session, key)
        return locks.edit_lock_status(session, key)


@router.post("/{resource_type}/{resource_id}")
def acquire(body: EditToken, key: ReferenceKey = Depends(resource_key), user: User = Depends(require_role("editor", "admin"))):
    with session_scope() as session:
        if not get_settings().reference_edit_locks_enabled:
            require_access(key, user)
            require_resource(session, key)
            return locks.edit_lock_status(session, key)
        return locks.acquire_edit_lock(session, key, user, body.edit_token)


@router.put("/{resource_type}/{resource_id}")
def renew(body: EditRenew, key: ReferenceKey = Depends(resource_key), user: User = Depends(require_role("editor", "admin"))):
    if not get_settings().reference_edit_locks_enabled:
        raise ReferenceError("reference_edit_lock_lost")
    with session_scope() as session:
        return locks.renew_edit_lock(session, key, user, body.edit_token, body.lease_epoch, body.activity_sequence)


@router.delete("/{resource_type}/{resource_id}")
def release(body: EditSession, key: ReferenceKey = Depends(resource_key), user: User = Depends(require_role("editor", "admin"))):
    with session_scope() as session:
        return locks.release_edit_lock(session, key, user, body.edit_token, body.lease_epoch)


@router.post("/{resource_type}/{resource_id}/revoke")
def revoke(body: EditRevoke, key: ReferenceKey = Depends(resource_key), user: User = Depends(require_role("admin"))):
    with session_scope() as session:
        return locks.revoke_edit_lock(session, key, user, body.lease_epoch, body.reason)
