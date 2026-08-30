# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Роут generic мини-справочника строковых атрибутов (module, component, ...)."""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.models import User
from app.auth.service import require_role, require_user
from app.models.schemas import AttributeCreate, AttributeListOut, AttributeValueOut
from app.services import audit
from app.services.attribute_registry import AttributeValueInUseError, get_attribute_registry

router = APIRouter(prefix="/attributes", tags=["attributes"])

_registry = get_attribute_registry()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/{key}", response_model=AttributeListOut)
def list_attribute_values(key: str, user: User = Depends(require_user)):
    return AttributeListOut(values=[AttributeValueOut(**v) for v in _registry.list(key)])


@router.post("/{key}", response_model=AttributeValueOut)
def add_attribute_value(
    key: str,
    body: AttributeCreate,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    item = _registry.add(
        key,
        body.value.strip(),
        label=body.label,
        sort_order=body.sort_order,
        created_by=user.username,
    )
    audit.record(
        user,
        audit.ATTRIBUTE_CREATE,
        audit.TARGET_ATTRIBUTE,
        target_id=f"{key}:{body.value}",
        new_value={"attribute_key": key, "value": body.value},
        ip_address=_client_ip(request),
    )
    return AttributeValueOut(**item)


@router.delete("/{key}/{value}")
def remove_attribute_value(
    key: str,
    value: str,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    try:
        removed = _registry.remove(key, value)
    except AttributeValueInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Значение не найдено")
    audit.record(
        user,
        audit.ATTRIBUTE_DELETE,
        audit.TARGET_ATTRIBUTE,
        target_id=f"{key}:{value}",
        old_value={"attribute_key": key, "value": value},
        ip_address=_client_ip(request),
    )
    return {"status": "deleted"}
