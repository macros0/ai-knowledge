# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Стабильные коды ошибок API — основа локализации сообщений.

Тело ошибки: {"detail": <диагностика>, "code": <стабильный код>, ...}.

Разделение ответственности:
  - `code` — контракт для клиента. По нему фронтенд берёт текст из СВОЕГО
    словаря (i18n/locales), поэтому сообщение приходит на языке интерфейса,
    а не на языке бэкенда. Код стабилен: менять его — ломать клиентов.
  - `detail` — диагностика для логов и разработчика, остаётся русской. Клиент
    показывает её только как фолбэк, если код ему неизвестен (старый бэкенд,
    новый клиент или наоборот).

Accept-Language бэкенд намеренно не читает: язык интерфейса живёт на клиенте
(localStorage + cookie, см. frontend/src/i18n/core.js), и дублировать его
резолв на сервере — значит завести второй источник истины о языке.
"""
from fastapi import HTTPException

from app.error_codes import *  # noqa: F401,F403 — коды доступны как errors.XXX
from app.services.errors import DomainError

class ApiError(HTTPException):
    """HTTPException со стабильным кодом в теле ответа.

    Обработчик в app.main разворачивает это в
    {"detail": ..., "code": ..., **extra} — плоско, как уже отдаются
    dependency_unavailable и duplicate, чтобы у клиента была одна форма тела.

    `headers` — отдельный именованный параметр, а не часть **extra: заголовки
    ответа (Retry-After на 429, WWW-Authenticate на 401) — часть HTTP-контракта.
    Попав в extra, они ушли бы в JSON-тело и до клиента как заголовки не дошли.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        headers: dict[str, str] | None = None,
        **extra,
    ):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.extra = extra


def domain_error(exc: DomainError, status_code: int) -> ApiError:
    """Доменная ошибка -> ответ API: код от исключения, статус от роутера.

    Код приходит из сервисного слоя вместе с исключением, поэтому клиент
    получает конкретику («задача не ожидает одобрения»), а не общее
    «некорректный запрос», выведенное из HTTP-статуса. Сам статус остаётся
    за роутером — он часть публичного контракта эндпоинта и не должен меняться
    от того, каким классом сервис бросил ошибку.
    """
    return ApiError(status_code=status_code, code=exc.code, detail=str(exc))
