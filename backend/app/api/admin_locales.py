"""Админ-эндпоинты «Поддержка языков» (Этап 7 roadmap, фаза A).

Роль admin (require_role). Все мутирующие операции — в append-only audit_log.
Импорт стоп-слов двухшаговый: без confirm — preview diff, с confirm — применение
(одна транзакция + синхронная инвалидация кэша стоп-слов).
"""
import logging

from app.api.reference_editing import request_edit_session
from app.services.reference_mutation import ReferenceWriteContext
from app.services.reference_resources import ReferenceKey
from app.services.reference_concurrency import ReferenceError
from app.api import errors
from app.api.errors import ApiError
from app.services.errors import DomainError
from fastapi import APIRouter, Depends, Query, Request

from app.auth.models import User
from app.auth.service import require_role
from app.models.schemas import (
    ReferenceRevisionInput,
    LocaleCreate,
    LocaleListOut,
    LocaleOut,
    LocaleUpdate,
    StopwordAddRequest,
    StopwordHistoryOut,
    StopwordHistoryEntry,
    StopwordImportRequest,
    StopwordImportResult,
    StopwordListOut,
    StopwordProbeRequest,
    StopwordProbeResponse,
    StopwordProbeResult,
    StopwordProbeHit,
    StopwordRenameRequest,
    StopwordRollbackRequest,
    StopwordWordOut,
    UiDictionaryHistoryEntry,
    UiDictionaryHistoryOut,
    UiDictionaryImportRequest,
    UiDictionaryRollbackRequest,
    UiDictionaryImportResult,
    UiDictionaryAdminOut,
)
from app.services import locale_service, ui_dictionary
from app.services.locale_service import LocaleError, LocaleNotFoundError

router = APIRouter(prefix="/admin/locales", tags=["admin-locales"])

admin = Depends(require_role("admin"))


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _reference_context(kind, code, expected, request, user, subkind=None):
    try:
        key = ReferenceKey(kind, code, kind=subkind)
    except ValueError as exc:
        raise ReferenceError("invalid_request", 422) from exc
    lease = request_edit_session(request)
    return ReferenceWriteContext(user, {key: expected}, {key: lease} if lease else {})


def _raise(exc: Exception) -> ApiError:
    """Ошибка локали -> ответ API со стабильным кодом.

    ApiError, а не голый HTTPException: без `code` фронтенд не найдёт ключ в
    своём словаре и откатится на русский detail — ровно в разделе «Поддержка
    языков», компоненты которого локализованы (friendlyApiError).
    """
    if isinstance(exc, LocaleNotFoundError):
        return ApiError(status_code=404, code=errors.LOCALE_NOT_FOUND, detail=str(exc))
    if isinstance(exc, LocaleError):
        return ApiError(status_code=422, code=errors.INVALID_REQUEST, detail=str(exc))
    return ApiError(status_code=500, code=errors.INTERNAL_ERROR, detail="Внутренняя ошибка")


@router.get("", response_model=LocaleListOut)
def list_locales(user: User = admin):
    return LocaleListOut(locales=[LocaleOut(**loc) for loc in locale_service.list_locales()])


@router.post("", response_model=LocaleOut)
def create_locale(body: LocaleCreate, request: Request, user: User = admin):
    try:
        loc = locale_service.create_locale(body.code, body.name, user=user, ip_address=_client_ip(request))
    except LocaleError as exc:
        raise _raise(exc) from exc
    return LocaleOut(**loc)


@router.patch("/{code}", response_model=LocaleOut)
def update_locale(code: str, body: LocaleUpdate, request: Request, user: User = admin):
    try:
        loc = locale_service.update_locale(code, name=body.name, status=body.status, user=user, ip_address=_client_ip(request),
            reference_context=_reference_context("locale", code, body.expected_reference_revision, request, user))
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    return LocaleOut(**loc)


@router.post("/{code}/activate", response_model=LocaleOut)
def activate_locale(code: str, request: Request, body: ReferenceRevisionInput | None = None, user: User = admin):
    try:
        loc = locale_service.activate(code, user=user, ip_address=_client_ip(request),
            reference_context=_reference_context("locale", code, body.expected_reference_revision if body else None, request, user))
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    # Автосид en-копии при активации языка без runtime-override (фаза 2): словарь
    # нужен не как блокер активации, а как стартовая точка перевода — провал сида
    # не должен ронять успешную активацию (см. контракт seed_english_copy).
    try:
        ui_dictionary.seed_english_copy(code, user=user, ip_address=_client_ip(request))
    except Exception:
        logging.getLogger(__name__).exception(
            "seed_english_copy для '%s' бросил исключение (не ожидается — "
            "внутри уже обработка ошибок)", code
        )
    return LocaleOut(**loc)


@router.post("/{code}/disable", response_model=LocaleOut)
def disable_locale(code: str, request: Request, body: ReferenceRevisionInput | None = None, user: User = admin):
    try:
        loc = locale_service.disable(code, user=user, ip_address=_client_ip(request),
            reference_context=_reference_context("locale", code, body.expected_reference_revision if body else None, request, user))
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    return LocaleOut(**loc)


# --- Стоп-слова ---


@router.get("/{code}/stopwords", response_model=StopwordListOut)
def list_stopwords(code: str, kind: str | None = None, user: User = admin):
    try:
        result = locale_service.stopword_snapshot(code, kind=kind)
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    return StopwordListOut(**result)


@router.post("/{code}/stopwords/import", response_model=StopwordImportResult)
def import_stopwords(
    code: str,
    body: StopwordImportRequest,
    request: Request,
    user: User = admin,
    mode: str = "merge",
    kind: str = "bm25",
):
    try:
        result = locale_service.import_stopwords(
            code,
            body.words,
            kind,
            mode,
            confirm=body.confirm,
            confirm_empty_replace=body.confirm_empty_replace,
            reference_context=_reference_context("stopword_set", code, body.expected_reference_revision, request, user, kind),
            preview_digest=body.preview_digest,
            user=user,
            ip_address=_client_ip(request),
        )
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    return StopwordImportResult(**result)


@router.post("/{code}/stopwords", response_model=StopwordWordOut)
def add_stopword(code: str, body: StopwordAddRequest, request: Request, user: User = admin):
    try:
        result = locale_service.add_stopword(
            code, body.word, body.kind, user=user, ip_address=_client_ip(request),
            reference_context=_reference_context("stopword_set", code, body.expected_reference_revision, request, user, body.kind)
        )
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    return StopwordWordOut(**result)


@router.patch("/{code}/stopwords/{word}", response_model=StopwordWordOut)
def rename_stopword(
    code: str, word: str, body: StopwordRenameRequest, request: Request, user: User = admin, kind: str = "bm25"
):
    try:
        result = locale_service.rename_stopword(
            code, word, kind, body.word, user=user, ip_address=_client_ip(request),
            reference_context=_reference_context("stopword_set", code, body.expected_reference_revision, request, user, kind)
        )
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    return StopwordWordOut(**result)


@router.delete("/{code}/stopwords/{word}", response_model=StopwordWordOut)
def delete_stopword(
    code: str, word: str, request: Request, user: User = admin, kind: str = "bm25", expected_reference_revision: int | None = Query(None, ge=1)
):
    try:
        result = locale_service.delete_stopword(
            code, word, kind, user=user, ip_address=_client_ip(request),
            reference_context=_reference_context("stopword_set", code, expected_reference_revision, request, user, kind)
        )
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    return StopwordWordOut(**result)


@router.get("/{code}/stopwords/history", response_model=StopwordHistoryOut)
def stopwords_history(code: str, user: User = admin):
    try:
        entries = locale_service.stopwords_history(code)
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    return StopwordHistoryOut(entries=[StopwordHistoryEntry(**e) for e in entries])


@router.post("/{code}/stopwords/rollback", response_model=StopwordImportResult)
def rollback_stopwords(code: str, body: StopwordRollbackRequest, request: Request, user: User = admin):
    if "confirm" not in body.model_fields_set or body.kind is None:
        raise ReferenceError("reference_precondition_required", 428)
    try:
        result = locale_service.rollback_stopwords(
            code, body.entry_id, user=user, ip_address=_client_ip(request), confirm=body.confirm, expected_kind=body.kind,
            preview_digest=body.preview_digest, reference_context=_reference_context("stopword_set", code, body.expected_reference_revision, request, user, body.kind)
        )
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    return StopwordImportResult(**result)


@router.post("/{code}/stopwords/probe", response_model=StopwordProbeResponse)
def probe_stopwords(code: str, body: StopwordProbeRequest, user: User = admin):
    try:
        locale_service.get_locale(code)
    except LocaleNotFoundError as exc:
        raise _raise(exc) from exc
    queries = [q for q in body.queries if q and q.strip()][:20]
    if not queries:
        raise ApiError(
            status_code=422,
            code=errors.EMPTY_DOCUMENT_LIST,
            detail="Пустой набор запросов для probe",
        )
    results = locale_service.probe(queries)
    return StopwordProbeResponse(
        results=[
            StopwordProbeResult(
                query=r["query"],
                hits=[StopwordProbeHit(**h) for h in r["hits"]],
            )
            for r in results
        ]
    )


# --- Runtime-override UI-словарей (фаза C) ---


@router.post("/{code}/ui-dictionary/import", response_model=UiDictionaryImportResult)
def import_ui_dictionary(
    code: str,
    body: UiDictionaryImportRequest,
    request: Request,
    user: User = admin,
):
    """Двухшаговый импорт override-словаря: без confirm — preview/валидация."""
    if body.confirm and "expected_active_version" not in body.model_fields_set:
        raise ReferenceError("reference_precondition_required", 428)
    try:
        result = ui_dictionary.import_dictionary(
            code,
            body.data,
            body.note,
            user.username,
            confirm=body.confirm,
            reference_context=_reference_context("ui_dictionary", code, body.expected_reference_revision, request, user),
            preview_digest=body.preview_digest, expected_active_version=body.expected_active_version,
            user=user,
            ip_address=_client_ip(request),
        )
    except DomainError as exc:
        raise errors.domain_error(exc, 404) from exc
    except ValueError as exc:
        # ui_dictionary бросает ValueError только на «языка нет» — код должен
        # быть про локаль, а не про документ.
        raise ApiError(
            status_code=404,
            code=errors.LOCALE_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return UiDictionaryImportResult(**result)


@router.get("/{code}/ui-dictionary/history", response_model=UiDictionaryHistoryOut)
def ui_dictionary_history(code: str, user: User = admin):
    try:
        locale_service.get_locale(code)
    except LocaleNotFoundError as exc:
        raise _raise(exc) from exc
    entries = ui_dictionary.history(code)
    return UiDictionaryHistoryOut(entries=[UiDictionaryHistoryEntry(**e) for e in entries])


@router.get("/{code}/ui-dictionary", response_model=UiDictionaryAdminOut)
def get_active_ui_dictionary(code: str, user: User = admin):
    """Активный override-словарь локали (read-only, для редактора).

    При отсутствии — 200 с {version: null, data: null}: редактор получает штатное
    «ещё нет override» вместо исключительного 404 (публичный GET /api/i18n/{locale}
    сохраняет 404-семантику для клиента).
    """
    try:
        return UiDictionaryAdminOut(**ui_dictionary.get_admin_snapshot(code))
    except ValueError as exc:
        raise ApiError(status_code=404, code=errors.LOCALE_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{code}/ui-dictionary/rollback")
def rollback_ui_dictionary(
    code: str, body: UiDictionaryRollbackRequest, request: Request, user: User = admin
):
    """Возвращает локали актуальную версию словаря к исторической (entry_id)."""
    if "confirm" not in body.model_fields_set or (body.confirm and "expected_active_version" not in body.model_fields_set):
        raise ReferenceError("reference_precondition_required", 428)
    try:
        result = ui_dictionary.rollback(
            code, body.entry_id, user=user, ip_address=_client_ip(request), confirm=body.confirm,
            reference_context=_reference_context("ui_dictionary", code, body.expected_reference_revision, request, user),
            preview_digest=body.preview_digest, expected_active_version=body.expected_active_version,
        )
    except DomainError as exc:
        raise errors.domain_error(exc, 404) from exc
    except ValueError as exc:
        # ui_dictionary бросает ValueError только на «языка нет» — код должен
        # быть про локаль, а не про документ.
        raise ApiError(
            status_code=404,
            code=errors.LOCALE_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return result
