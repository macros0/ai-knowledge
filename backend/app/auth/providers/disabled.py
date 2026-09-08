"""Провайдер «disabled»: защита выключена, все считаются анонимами.

Для локальной разработки и тестов — /api открыт без сессии.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.auth.providers.base import AuthProvider

from app.api import errors
from app.api.errors import ApiError

if TYPE_CHECKING:
    from fastapi import Request

    from app.auth.identity import AuthenticatedIdentity


class DisabledProvider(AuthProvider):
    key = "disabled"
    mode = "disabled"

    def __init__(self, settings=None):
        pass

    def is_disabled(self) -> bool:
        return True

    async def start_login(self, request: "Request"):

        raise ApiError(
            status_code=400,
            code=errors.AUTH_DISABLED,
            detail="Авторизация отключена",
        )

    async def handle_callback(self, request: "Request") -> "AuthenticatedIdentity":

        raise ApiError(
            status_code=400,
            code=errors.AUTH_DISABLED,
            detail="Авторизация отключена",
        )
