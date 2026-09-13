"""Admin/editor API for the query-expansion glossary."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, Request, status

from app.api import errors
from app.api.errors import ApiError
from app.auth.models import User
from app.auth.service import require_role
from app.config import get_settings
from app.models.glossary import (
    GlossaryAliasAdd,
    GlossaryAliasPatch,
    GlossaryListOut,
    GlossaryPendingOut,
    GlossaryPreviewOut,
    GlossaryPreviewRequest,
    GlossarySourcePatch,
    GlossaryTermCreate,
    GlossaryTermOut,
    GlossaryTermPatch,
    GlossaryTranslationBackfillRequest,
    GlossaryTranslationPatch,
    GlossaryTranslationReview,
)
from app.services.glossary.expansion import prepare_query
from app.services.glossary.normalization import GlossaryValidationError
from app.services.glossary.normalization import SUPPORTED_KINDS, validate_locale
from app.services.glossary.registry import (
    GlossaryAliasConflictError,
    GlossaryCanonicalConflictError,
    GlossaryNotFoundError,
    GlossaryRegistry,
    GlossaryVersionConflictError,
    get_glossary_registry,
)
from app.services.glossary.translations import (
    GlossaryTranslationConflictError,
    GlossaryTranslationValidationError,
    backfill_glossary_translations,
    review_glossary_translation,
    set_glossary_translation,
)
from app.services.glossary.types import GlossaryAliasInput


router = APIRouter(prefix="/admin/glossary", tags=["glossary"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _validation_error(exc: GlossaryValidationError) -> ApiError:
    message = str(exc)
    lowered = message.lower()
    if any(word in lowered for word in ("корот", "числов", "автоматичес", "триггер")):
        code = errors.GLOSSARY_UNSAFE_AUTO_EXPAND
    elif "язык" in lowered:
        code = errors.GLOSSARY_INVALID_LOCALE
    else:
        code = errors.GLOSSARY_INVALID_ALIAS
    return ApiError(status_code=422, code=code, detail=message)


def _raise_mutation_error(exc: Exception, *, alias_not_found: bool = False) -> None:
    if isinstance(exc, GlossaryNotFoundError):
        code = errors.GLOSSARY_ALIAS_NOT_FOUND if alias_not_found else errors.GLOSSARY_TERM_NOT_FOUND
        raise ApiError(status_code=404, code=code, detail=str(exc)) from exc
    if isinstance(exc, GlossaryCanonicalConflictError):
        raise ApiError(status_code=409, code=errors.GLOSSARY_CANONICAL_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, GlossaryAliasConflictError):
        raise ApiError(status_code=409, code=errors.GLOSSARY_ALIAS_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, GlossaryVersionConflictError):
        raise ApiError(status_code=409, code=errors.VERSION_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, GlossaryValidationError):
        raise _validation_error(exc) from exc
    raise exc


def _raise_translation_error(exc: Exception) -> None:
    if isinstance(exc, GlossaryNotFoundError):
        raise ApiError(status_code=404, code=errors.GLOSSARY_TERM_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, GlossaryTranslationConflictError):
        raise ApiError(status_code=409, code=errors.VERSION_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, GlossaryTranslationValidationError):
        raise ApiError(status_code=422, code=errors.INVALID_REQUEST, detail=str(exc)) from exc
    raise exc


def _registry() -> GlossaryRegistry:
    return get_glossary_registry()


@router.get("", response_model=GlossaryListOut)
def list_glossary(
    q: str | None = None,
    kind: str | None = None,
    enabled: bool | None = None,
    alias_locale: str | None = None,
    needs_review: bool | None = None,
    target_locale: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_role("editor", "admin")),
):
    if kind is not None and kind not in SUPPORTED_KINDS:
        raise ApiError(status_code=422, code=errors.INVALID_REQUEST, detail="Неподдерживаемый вид термина")
    for value in (alias_locale, target_locale):
        if value is not None:
            try:
                validate_locale(value)
            except GlossaryValidationError as exc:
                raise _validation_error(exc) from exc
    return _registry().list_page(
        q=q,
        kind=kind,
        enabled=enabled,
        alias_locale=alias_locale,
        needs_review=needs_review,
        target_locale=target_locale,
        limit=limit,
        offset=offset,
    )


@router.post("/preview", response_model=GlossaryPreviewOut)
def preview_query(
    body: GlossaryPreviewRequest,
    user: User = Depends(require_role("editor", "admin")),
):
    settings = get_settings()
    plan = prepare_query(body.query, ui_locale=body.locale, enabled=True, settings=settings)
    return GlossaryPreviewOut(
        original_query=plan.original_query,
        dense_query=plan.dense_query,
        added_sparse_texts=list(plan.added_sparse_texts),
        expansion_status=plan.status,
        applied_terms=[asdict(item) for item in plan.applied_terms],
        match_groups=[asdict(item) for item in plan.match_groups],
        skipped_reasons=[asdict(item) for item in plan.skipped_reasons],
        limits={
            "max_terms_per_query": settings.glossary_max_terms_per_query,
            "max_added_aliases_per_term": settings.glossary_max_added_aliases_per_term,
            "max_added_tokens": settings.glossary_max_added_tokens,
            "max_added_chars": settings.glossary_max_added_chars,
        },
    )


@router.get("/translations/pending", response_model=GlossaryPendingOut)
def pending_glossary_translations(
    locale: str = Query(...),
    user: User = Depends(require_role("editor", "admin")),
):
    try:
        locale = validate_locale(locale, allow_und=False)
    except GlossaryValidationError as exc:
        raise _validation_error(exc) from exc
    return {"locale": locale, "pending": _registry().pending_counts(locale)}


@router.post("/translations/backfill")
def backfill_glossary_translations_route(
    body: GlossaryTranslationBackfillRequest,
    request: Request,
    user: User = Depends(require_role("admin")),
):
    try:
        return backfill_glossary_translations(
            body.locale,
            body.term_ids,
            expected_translation_versions=body.expected_translation_versions,
            user=user,
            ip_address=_client_ip(request),
        )
    except Exception as exc:
        _raise_translation_error(exc)
    raise AssertionError("unreachable")


@router.patch("/{term_id}/translations/{locale}", response_model=GlossaryTermOut)
def update_glossary_translation(
    term_id: int,
    locale: str,
    body: GlossaryTranslationPatch,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    try:
        return set_glossary_translation(
            term_id,
            locale,
            display_name=body.display_name,
            description=body.description,
            translation_version=body.translation_version,
            source_revision=body.source_revision,
            user=user,
            ip_address=_client_ip(request),
        )
    except Exception as exc:
        _raise_translation_error(exc)
    raise AssertionError("unreachable")


@router.post("/{term_id}/translations/{locale}/review", response_model=GlossaryTermOut)
def review_glossary_translation_route(
    term_id: int,
    locale: str,
    body: GlossaryTranslationReview,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    try:
        return review_glossary_translation(
            term_id,
            locale,
            translation_version=body.translation_version,
            source_revision=body.source_revision,
            user=user,
            ip_address=_client_ip(request),
        )
    except Exception as exc:
        _raise_translation_error(exc)
    raise AssertionError("unreachable")


@router.get("/{term_id}", response_model=GlossaryTermOut)
def get_glossary_term(term_id: int, user: User = Depends(require_role("editor", "admin"))):
    result = _registry().get(term_id)
    if result is None:
        raise ApiError(status_code=404, code=errors.GLOSSARY_TERM_NOT_FOUND, detail="Термин не найден")
    return result


@router.post("", response_model=GlossaryTermOut, status_code=status.HTTP_201_CREATED)
def create_glossary_term(
    body: GlossaryTermCreate,
    request: Request,
    user: User = Depends(require_role("admin")),
):
    try:
        return _registry().create(
            body.canonical,
            body.kind,
            body.original_name,
            body.original_description,
            body.canonical_locale,
            user.user_id,
            aliases=[GlossaryAliasInput(**item.model_dump(), created_by=user.user_id) for item in body.aliases],
            ip_address=_client_ip(request),
            audit_username=user.username,
        )
    except Exception as exc:
        _raise_mutation_error(exc)
    raise AssertionError("unreachable")


@router.patch("/{term_id}", response_model=GlossaryTermOut)
def update_glossary_term(
    term_id: int,
    body: GlossaryTermPatch,
    request: Request,
    user: User = Depends(require_role("admin")),
):
    try:
        return _registry().update(
            term_id, body.version, enabled=body.enabled, updated_by=user.user_id,
            ip_address=_client_ip(request),
            audit_username=user.username,
        )
    except Exception as exc:
        _raise_mutation_error(exc)
    raise AssertionError("unreachable")


@router.patch("/{term_id}/source", response_model=GlossaryTermOut)
def update_glossary_source(
    term_id: int,
    body: GlossarySourcePatch,
    request: Request,
    user: User = Depends(require_role("admin")),
):
    values = {
        field: getattr(body, field)
        for field in ("original_name", "original_description", "canonical_locale", "enabled")
        if field in body.model_fields_set
    }
    try:
        return _registry().update(
            term_id, body.version, updated_by=user.user_id,
            ip_address=_client_ip(request), **values,
            audit_username=user.username,
        )
    except Exception as exc:
        _raise_mutation_error(exc)
    raise AssertionError("unreachable")


@router.post("/{term_id}/aliases", response_model=GlossaryTermOut)
def add_glossary_alias(
    term_id: int,
    body: GlossaryAliasAdd,
    request: Request,
    user: User = Depends(require_role("admin")),
):
    try:
        return _registry().add_alias(
            term_id, body.version, body.alias, locale=body.locale, auto_expand=body.auto_expand,
            search_enabled=body.search_enabled, updated_by=user.user_id,
            ip_address=_client_ip(request),
            audit_username=user.username,
        )
    except Exception as exc:
        _raise_mutation_error(exc)
    raise AssertionError("unreachable")


@router.patch("/{term_id}/aliases/{alias_id}", response_model=GlossaryTermOut)
def update_glossary_alias(
    term_id: int,
    alias_id: int,
    body: GlossaryAliasPatch,
    request: Request,
    user: User = Depends(require_role("admin")),
):
    try:
        return _registry().update_alias(
            term_id, body.version, alias_id, alias=body.alias, locale=body.locale,
            auto_expand=body.auto_expand, search_enabled=body.search_enabled, updated_by=user.user_id,
            ip_address=_client_ip(request),
            audit_username=user.username,
        )
    except Exception as exc:
        _raise_mutation_error(exc, alias_not_found=True)
    raise AssertionError("unreachable")


@router.delete("/{term_id}/aliases/{alias_id}", response_model=GlossaryTermOut)
def delete_glossary_alias(
    term_id: int,
    alias_id: int,
    request: Request,
    version: int = Query(..., ge=1),
    user: User = Depends(require_role("admin")),
):
    try:
        return _registry().delete_alias(
            term_id, version, alias_id, updated_by=user.user_id,
            ip_address=_client_ip(request),
            audit_username=user.username,
        )
    except Exception as exc:
        _raise_mutation_error(exc, alias_not_found=True)
    raise AssertionError("unreachable")
