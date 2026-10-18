# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Роут справочника номеров разработки (Этап 4)."""
from typing import Annotated

from app.api import errors
from app.api.reference_editing import request_edit_session
from app.services.errors import DomainError
from app.api.errors import ApiError
from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.auth.models import User
from app.auth.service import require_role, require_user
from app.models.schemas import (
    DevelopmentCreate,
    DevelopmentListOut,
    DevelopmentOut,
    DevelopmentUpdate,
)
from app.services.development_registry import (
    DevelopmentConflictError,
    DevelopmentModuleError,
    DevelopmentNumberExistsError,
    get_development_registry,
)
from app.services.locale_service import request_locale

router = APIRouter(prefix="/developments", tags=["developments"])

_registry = get_development_registry()

SORTABLE_COLUMNS = ("number", "name", "module", "documents_count")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("", response_model=DevelopmentListOut)
def list_developments(
    request: Request,
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
        locale=request_locale(request),
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
        dev = _registry.create(body.number, body.name, body.module, created_by=user.username,
                               canonical_locale=body.canonical_locale or request_locale(request, fallback="und"),
                               actor=user, ip_address=_client_ip(request))
    except DevelopmentModuleError as exc:
        raise ApiError(
            status_code=422,
            code=errors.INVALID_REQUEST,
            detail=str(exc),
        ) from exc
    except DevelopmentNumberExistsError as exc:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "code": errors.DUPLICATE_NUMBER},
        )
    return DevelopmentOut(**dev)


@router.get("/{dev_id}", response_model=DevelopmentOut)
def get_development(dev_id: int, request: Request, user: User = Depends(require_user)):
    dev = _registry.get(dev_id, locale=request_locale(request))
    if dev is None:
        raise ApiError(
            status_code=404,
            code=errors.DEVELOPMENT_NOT_FOUND,
            detail="Разработка не найдена",
        )
    return DevelopmentOut(**dev)


@router.patch("/{dev_id}", response_model=DevelopmentOut)
def update_development(
    dev_id: int,
    body: DevelopmentUpdate,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    try:
        dev = _registry.update(
            dev_id, body.version, number=body.number, name=body.name, module=body.module,
            expected_reference_revision=body.expected_reference_revision, actor=user, lease=request_edit_session(request),
        )
    except DevelopmentModuleError as exc:
        raise ApiError(
            status_code=422,
            code=errors.INVALID_REQUEST,
            detail=str(exc),
        ) from exc
    except DevelopmentNumberExistsError as exc:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "code": errors.DUPLICATE_NUMBER},
        )
    except DevelopmentConflictError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "code": errors.VERSION_CONFLICT,
                "current": jsonable_encoder(exc.current),
            },
        )
    except DomainError as exc:
        raise errors.domain_error(exc, 404) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return DevelopmentOut(**dev)


@router.delete("/{dev_id}")
def delete_development(
    dev_id: int,
    request: Request,
    version: int = Query(...),
    expected_reference_revision: int | None = Query(default=None, ge=1),
    user: User = Depends(require_role("editor", "admin")),
):
    try:
        deleted = _registry.delete(dev_id, version, expected_reference_revision=expected_reference_revision,
            actor=user, lease=request_edit_session(request))
    except DevelopmentConflictError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "code": errors.VERSION_CONFLICT,
                "current": jsonable_encoder(exc.current),
            },
        )
    if not deleted:
        raise ApiError(
            status_code=404,
            code=errors.DEVELOPMENT_NOT_FOUND,
            detail="Разработка не найдена",
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
        raise ApiError(
            status_code=404,
            code=errors.DEVELOPMENT_NOT_FOUND,
            detail="Разработка не найдена",
        )
    from app.services.registry import get_registry

    docs, total = get_registry().list_page(
        development_id=dev_id,
        search=search,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return {"documents": docs, "total": total, "limit": limit, "offset": offset}
