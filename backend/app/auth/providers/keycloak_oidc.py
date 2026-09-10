"""Провайдер Keycloak OIDC (Authorization Code Flow) через Authlib.

Перенос логики из oidc.py в класс провайдера. Занимается только аутентификацией:
authorize_redirect → callback → token → userinfo → AuthenticatedIdentity.
Роли здесь НЕ вычисляются — их считает GroupRoleAuthorizer.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse
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
        # (Host: localhost:16300 сохраняется Next.js-прокси) — вручную не передаём.
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

        # id_token нужен для RP-Initiated Logout (id_token_hint). Переданный в
        # identity атрибут временно переносится в server-side auth_sessions при
        # store_identity(); в браузерную cookie сам токен не попадает.
        id_token = token.get("id_token")
        if id_token:
            attrs = dict(identity.attributes or {})
            attrs["id_token"] = id_token
            identity.attributes = attrs
        return identity

    async def logout(self, request: Request):
        """RP-Initiated Logout: завершаем и SSO-сессию Keycloak, а не только нашу.

        Возвращает RedirectResponse на end_session_endpoint Keycloak (браузерный
        flow: приложение → Keycloak logout → post_logout_redirect_uri). Если в
        сессии нет id_token (сессия создана до сохранения id_token, либо IdP его
        не выдал) — SSO-logout невозможен: logout без id_token_hint Keycloak
        принимает только при живой SSO-сессии, иначе отдаёт error-page
        «Missing parameters: id_token_hint». Возвращаем None → endpoint делает
        локальный logout и редиректит на "/" (логин-гейт), не отправляя браузер
        на заведомо ошибочный URL.
        """
        from app.auth.service import clear_identity, session_id_token

        id_token = session_id_token(request)
        clear_identity(request)
        if not id_token:
            logger.info(
                "Logout без id_token в сессии — только локальное завершение "
                "(RP-Initiated Logout в Keycloak пропущен)"
            )
            return None
        return RedirectResponse(url=self._end_session_url(id_token), status_code=303)

    def _end_session_url(self, id_token: str) -> str:
        base = (
            (self._settings.keycloak_url or "").rstrip("/")
            + "/realms/"
            + (self._settings.keycloak_realm or "")
            + "/protocol/openid-connect/logout"
        )
        params: dict[str, str] = {
            "post_logout_redirect_uri": self._settings.sso_post_logout_redirect_uri,
            "id_token_hint": id_token,
        }
        return base + "?" + urlencode(params)

    def _normalize_paths(self, groups: list[str]) -> list[str]:
        """leaf: берём текст после последнего '/'; full_path: как есть."""
        if self._settings.keycloak_group_path_mode != "leaf":
            return groups
        return [g.rsplit("/", 1)[-1].strip() for g in groups if g]
