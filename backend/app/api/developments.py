# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Роут справочника номеров разработки (Этап 4)."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth.models import User
from app.auth.service import require_role, require_user
from app.models.schemas import (
    DevelopmentCreate,
    DevelopmentListOut,
    DevelopmentOut,
    DevelopmentUpdate,
)
from app.services import audit
from app.services.development_registry import (
    DevelopmentModuleError,
    DevelopmentNumberExistsError,
    get_development_registry,
)

router = APIRouter(prefix="/developments", tags=["developments"])

_registry = get_development_registry()

SORTABLE_COLUMNS = ("number", "name", "module", "documents_count")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("", response_model=DevelopmentListOut)
def list_developments(
    search: str | None = Query(default=None),
    module: str | None = Query(default=None),
    sort: str = Query(default="number"),
    order: str = Query(default="asc"),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_user),
):
    sort_by = sort if sort in SORTABLE_COLUMNS else "number"
    order = "desc" if order == "desc" else "asc"
    items, total = _registry.query(
        search=search,
        module=module,
        sort_by=sort_by,
        order=order,
        limit=limit,
        offset=offset,
    )
    return DevelopmentListOut(
        developments=[DevelopmentOut(**d) for d in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=DevelopmentOut)
def create_development(
    body: DevelopmentCreate,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    try:
        dev = _registry.create(body.number, body.name, body.module, created_by=user.username)
    except DevelopmentModuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DevelopmentNumberExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(
        user,
        audit.DEVELOPMENT_CREATE,
        audit.TARGET_DEVELOPMENT,
        target_id=str(dev["id"]),
        new_value={"number": dev["number"], "name": dev["name"], "module": dev["module"]},
        ip_address=_client_ip(request),
    )
    return DevelopmentOut(**dev)


@router.get("/{dev_id}", response_model=DevelopmentOut)
def get_development(dev_id: int, user: User = Depends(require_user)):
    dev = _registry.get(dev_id)
    if dev is None:
        raise HTTPException(status_code=404, detail="Разработка не найдена")
    return DevelopmentOut(**dev)


@router.patch("/{dev_id}", response_model=DevelopmentOut)
def update_development(
    dev_id: int,
    body: DevelopmentUpdate,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    try:
        dev = _registry.update(dev_id, number=body.number, name=body.name, module=body.module)
    except DevelopmentModuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DevelopmentNumberExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(
        user,
        audit.DEVELOPMENT_UPDATE,
        audit.TARGET_DEVELOPMENT,
        target_id=str(dev_id),
        new_value={"number": dev["number"], "name": dev["name"], "module": dev["module"]},
        ip_address=_client_ip(request),
    )
    return DevelopmentOut(**dev)


@router.delete("/{dev_id}")
def delete_development(
    dev_id: int,
    request: Request,
    user: User = Depends(require_role("admin")),
):
    if not _registry.delete(dev_id):
        raise HTTPException(status_code=404, detail="Разработка не найдена")
    audit.record(
        user,
        audit.DEVELOPMENT_DELETE,
        audit.TARGET_DEVELOPMENT,
        target_id=str(dev_id),
        ip_address=_client_ip(request),
    )
    return {"status": "deleted"}


@router.get("/{dev_id}/documents")
def list_development_documents(
    dev_id: int,
    search: str | None = None,
    sort: str = "date_desc",
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    user: User = Depends(require_user),
):
    if _registry.get(dev_id) is None:
        raise HTTPException(status_code=404, detail="Разработка не найдена")
    from app.services.registry import get_registry

    docs, total = get_registry().list_page(
        development_id=dev_id,
        search=search,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return {"documents": docs, "total": total, "limit": limit, "offset": offset}
