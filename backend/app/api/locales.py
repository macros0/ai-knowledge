"""Публичный роут активных языков (для LocaleToggle и клампа локали).

Возвращает только АКТИВНЫЕ языки; список меняется редко → ETag + Cache-Control,
чтобы фронтенд не делал лишний round-trip на каждый рендер. Fallback-язык ru
всегда активен (disable запрещён на уровне сервиса).
"""
import hashlib
import json

from fastapi import APIRouter, Request, Response

from app.services.locale_service import list_locales
from app.services.stopwords import LOCALE_STATUS_ACTIVE

router = APIRouter(prefix="/locales", tags=["locales"])


def _etag(active: list[dict]) -> str:
    payload = json.dumps(
        [{"code": l["code"], "updated_at": str(l["updated_at"])} for l in active],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@router.get("")
def get_active_locales(request: Request, response: Response):
    all_locales = list_locales()
    active = [
        {"code": loc["code"], "name": loc["name"]}
        for loc in all_locales
        if loc["status"] == LOCALE_STATUS_ACTIVE
    ]
    etag = _etag(all_locales)
    # Та же связка, что в api/i18n.py: 304 — отдельный ответ, и валидатор
    # обязан ехать в НЁМ (RFC 7232 §4.1), а не в инжектированном `response`.
    cache_headers = {"ETag": etag, "Cache-Control": "private, max-age=60"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cache_headers)
    response.headers.update(cache_headers)
    return {"locales": active}
