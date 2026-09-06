"""Админ-эндпоинты «Поддержка языков» (Этап 7 roadmap, фаза A).

Роль admin (require_role). Все мутирующие операции — в append-only audit_log.
Импорт стоп-слов двухшаговый: без confirm — preview diff, с confirm — применение
(одна транзакция + синхронная инвалидация кэша стоп-слов).
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.models import User
from app.auth.service import require_role
from app.models.schemas import (
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
    UiDictionaryImportResult,
    UiDictionaryAdminOut,
)
from app.services import audit, locale_service, ui_dictionary
from app.services.locale_service import LocaleError, LocaleNotFoundError

router = APIRouter(prefix="/admin/locales", tags=["admin-locales"])

admin = Depends(require_role("admin"))


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _raise(exc: Exception) -> HTTPException:
    if isinstance(exc, LocaleNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, LocaleError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Внутренняя ошибка")


@router.get("", response_model=LocaleListOut)
def list_locales(user: User = admin):
    return LocaleListOut(locales=[LocaleOut(**loc) for loc in locale_service.list_locales()])


@router.post("", response_model=LocaleOut)
def create_locale(body: LocaleCreate, request: Request, user: User = admin):
    try:
        loc = locale_service.create_locale(body.code, body.name)
    except LocaleError as exc:
        raise _raise(exc) from exc
    audit.record(
        user,
        audit.LOCALE_CREATE,
        audit.TARGET_LOCALE,
        target_id=loc["code"],
        new_value={"code": loc["code"], "name": loc["name"]},
        ip_address=_client_ip(request),
    )
    return LocaleOut(**loc)


@router.patch("/{code}", response_model=LocaleOut)
def update_locale(code: str, body: LocaleUpdate, request: Request, user: User = admin):
    try:
        loc = locale_service.update_locale(code, name=body.name, status=body.status)
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    audit.record(
        user,
        audit.LOCALE_UPDATE,
        audit.TARGET_LOCALE,
        target_id=code,
        old_value=None,
        new_value={"name": body.name, "status": body.status},
        ip_address=_client_ip(request),
    )
    return LocaleOut(**loc)


@router.post("/{code}/activate", response_model=LocaleOut)
def activate_locale(code: str, request: Request, user: User = admin):
    try:
        loc = locale_service.activate(code)
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    audit.record(
        user, audit.LOCALE_ACTIVATE, audit.TARGET_LOCALE, target_id=code, ip_address=_client_ip(request)
    )
    return LocaleOut(**loc)


@router.post("/{code}/disable", response_model=LocaleOut)
def disable_locale(code: str, request: Request, user: User = admin):
    try:
        loc = locale_service.disable(code)
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    audit.record(
        user, audit.LOCALE_DISABLE, audit.TARGET_LOCALE, target_id=code, ip_address=_client_ip(request)
    )
    return LocaleOut(**loc)


# --- Стоп-слова ---


@router.get("/{code}/stopwords", response_model=StopwordListOut)
def list_stopwords(code: str, kind: str | None = None, user: User = admin):
    try:
        words = locale_service.list_stopwords(code, kind=kind)
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    return StopwordListOut(locale=code, words=[StopwordWordOut(**w) for w in words])


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
            code, body.word, body.kind, user=user, ip_address=_client_ip(request)
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
            code, word, kind, body.word, user=user, ip_address=_client_ip(request)
        )
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    return StopwordWordOut(**result)


@router.delete("/{code}/stopwords/{word}", response_model=StopwordWordOut)
def delete_stopword(
    code: str, word: str, request: Request, user: User = admin, kind: str = "bm25"
):
    try:
        result = locale_service.delete_stopword(
            code, word, kind, user=user, ip_address=_client_ip(request)
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
    try:
        result = locale_service.rollback_stopwords(
            code, body.entry_id, user=user, ip_address=_client_ip(request)
        )
    except (LocaleError, LocaleNotFoundError) as exc:
        raise _raise(exc) from exc
    return StopwordImportResult(
        locale=result["locale"],
        kind=result["kind"],
        mode="replace",
        total_after=result["total_after"],
        applied=True,
    )


@router.post("/{code}/stopwords/probe", response_model=StopwordProbeResponse)
def probe_stopwords(code: str, body: StopwordProbeRequest, user: User = admin):
    try:
        locale_service.get_locale(code)
    except LocaleNotFoundError as exc:
        raise _raise(exc) from exc
    queries = [q for q in body.queries if q and q.strip()][:20]
    if not queries:
        raise HTTPException(status_code=422, detail="Пустой набор запросов для probe")
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
    try:
        result = ui_dictionary.import_dictionary(
            code,
            body.data,
            body.note,
            user.username,
            confirm=body.confirm,
            user=user,
            ip_address=_client_ip(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return UiDictionaryImportResult(**result)


@router.get("/{code}/ui-dictionary/history", response_model=UiDictionaryHistoryOut)
def ui_dictionary_history(code: str, user: User = admin):
    try:
        entries = ui_dictionary.history(code)
    except Exception:
        entries = []
    return UiDictionaryHistoryOut(entries=[UiDictionaryHistoryEntry(**e) for e in entries])


@router.get("/{code}/ui-dictionary", response_model=UiDictionaryAdminOut)
def get_active_ui_dictionary(code: str, user: User = admin):
    """Активный override-словарь локали (read-only, для редактора).

    При отсутствии — 200 с {version: null, data: null}: редактор получает штатное
    «ещё нет override» вместо исключительного 404 (публичный GET /api/i18n/{locale}
    сохраняет 404-семантику для клиента).
    """
    try:
        locale_service.get_locale(code)
    except LocaleNotFoundError as exc:
        raise _raise(exc) from exc
    active = ui_dictionary.get_active(code)
    if active is None:
        return UiDictionaryAdminOut(locale=code)
    return UiDictionaryAdminOut(**active)


@router.post("/{code}/ui-dictionary/rollback")
def rollback_ui_dictionary(
    code: str, body: StopwordRollbackRequest, request: Request, user: User = admin
):
    """Возвращает локали актуальную версию словаря к исторической (entry_id)."""
    try:
        result = ui_dictionary.rollback(
            code, body.entry_id, user=user, ip_address=_client_ip(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result
