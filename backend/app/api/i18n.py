"""Публичный роут runtime-override UI-словарей (Этап 7 фаза C).

GET /i18n/{locale} — актуальный override-словарь локали (require_user), ETag по
версии. 404 — активного override нет (фронт использует версионированный словарь
релиза). Кэш-заголовки: версия меняется редко, ключ/значения — неизменяемый
снапшот.
"""
from app.api import errors
from app.api.errors import ApiError
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth.models import User
from app.auth.service import require_user
from app.models.schemas import UiDictionaryActiveOut
from app.services.ui_dictionary import get_active

router = APIRouter(prefix="/i18n", tags=["i18n"])


@router.get("/{locale}", response_model=UiDictionaryActiveOut)
def get_ui_dictionary(locale: str, request: Request, response: Response, user: User = Depends(require_user)):
    active = get_active(locale)
    if active is None:
        raise ApiError(
            status_code=404,
            code=errors.OVERRIDE_NOT_FOUND,
            detail="Нет активного override-словаря",
        )
    etag = f"v{active['version']}"
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=60"
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    return UiDictionaryActiveOut(**active)
