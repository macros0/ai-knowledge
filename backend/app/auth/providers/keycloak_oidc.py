"""Провайдер Keycloak OIDC (Authorization Code Flow) через Authlib.

Перенос логики из oidc.py в класс провайдера. Занимается только аутентификацией:
authorize_redirect → callback → token → userinfo → AuthenticatedIdentity.
Роли здесь НЕ вычисляются — их считает GroupRoleAuthorizer.
"""
from __future__ import annotations

import logging

from fastapi import Request
from authlib.integrations.starlette_client import OAuth

from app.auth.identity import AuthenticatedIdentity
from app.auth.providers.base import AuthProvider
from app.config import Settings

logger = logging.getLogger(__name__)


class KeycloakOidcProvider(AuthProvider):
    key = "keycloak_oidc"
    mode = "sso"

    def __init__(self, settings: Settings):
        self._settings = settings

    def _oauth(self) -> OAuth:
        oauth = OAuth()
        oauth.register(
            name="keycloak",
            client_id=self._settings.keycloak_client_id,
            client_secret=self._settings.keycloak_client_secret,
            server_metadata_url=(
                f"{self._settings.keycloak_url}/realms/{self._settings.keycloak_realm}"
                "/.well-known/openid-configuration"
            ),
            client_kwargs={"scope": "openid profile email"},
        )
        return oauth

    def _redirect_uri(self, request: Request) -> str:
        return self._settings.sso_redirect_uri or str(request.url_for("auth_callback"))

    async def start_login(self, request: Request):
        return await self._oauth().keycloak.authorize_redirect(
            request, self._redirect_uri(request)
        )

    async def handle_callback(self, request: Request) -> AuthenticatedIdentity:
        # authlib сам формирует redirect_uri для code-обмена из request
        # (Host: localhost:3000 сохраняется Next.js-прокси) — вручную не передаём.
        token = await self._oauth().keycloak.authorize_access_token(request)
        userinfo = token.get("userinfo") or {}
        if not userinfo:
            userinfo = await self._oauth().keycloak.userinfo(token=token)

        identity = AuthenticatedIdentity.from_mapping(
            userinfo,
            field_mapping=self._settings.keycloak_field_mapping,
            provider=self.key,
            default_username=str(userinfo.get("sub") or ""),
            group_separator=self._settings.keycloak_group_separator,
        )
        identity.groups = self._normalize_paths(identity.groups)
        return identity

    def _normalize_paths(self, groups: list[str]) -> list[str]:
        """leaf: берём текст после последнего '/'; full_path: как есть."""
        if self._settings.keycloak_group_path_mode != "leaf":
            return groups
        return [g.rsplit("/", 1)[-1].strip() for g in groups if g]
