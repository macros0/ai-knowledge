"""Провайдер собственной системы авторизации клиента — заглушка (точка расширения).

Реализуется при онбординге клиента с проприетарным API: заполнить start_login /
handle_callback и вернуть AuthenticatedIdentity через from_mapping с заданным
field_mapping под поля их API. Authorization/модель User/JIT-provisioning не меняются.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.auth.providers.base import AuthProvider

if TYPE_CHECKING:
    from fastapi import Request

    from app.auth.identity import AuthenticatedIdentity


class CustomClientProvider(AuthProvider):
    key = "custom_client"
    mode = "sso"

    def __init__(self, settings=None):
        self._settings = settings

    async def start_login(self, request: "Request"):
        raise NotImplementedError("custom_client не реализован")

    async def handle_callback(self, request: "Request") -> "AuthenticatedIdentity":
        raise NotImplementedError("custom_client не реализован")
