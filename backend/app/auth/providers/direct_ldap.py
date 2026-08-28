"""Провайдер прямой интеграции с AD/LDAP — заглушка (точка расширения).

Реализуется при онбординге клиента с прямым LDAP: заполнить start_login /
handle_callback (bind, search base_dn, memberOf → groups) и вернуть
AuthenticatedIdentity. Authorization/модель User/JIT-provisioning не меняются.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.auth.providers.base import AuthProvider

if TYPE_CHECKING:
    from fastapi import Request

    from app.auth.identity import AuthenticatedIdentity


class DirectLdapProvider(AuthProvider):
    key = "direct_ldap"
    mode = "sso"

    def __init__(self, settings=None):
        self._settings = settings

    async def start_login(self, request: "Request"):
        raise NotImplementedError("direct_ldap не реализован")

    async def handle_callback(self, request: "Request") -> "AuthenticatedIdentity":
        raise NotImplementedError("direct_ldap не реализован")
