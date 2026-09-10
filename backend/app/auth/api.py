"""Роуты авторизации: /api/auth/*.

Все эндпоинты открытые; защита навешивается на documents/search/chat
отдельным APIRouter-ом с Depends(require_user).

Роуты делегируют конкретному AuthProvider (по auth_provider) — без ветвлений
по типу провайдера.

  GET   /auth/me       — режим + текущий пользователь (в simulation — ещё и демо-юзеры).
  POST  /auth/simulate — выбрать демо-юзера, записать сессию (только simulation).
  GET   /auth/login    — начать вход (для OIDC — редирект на Keycloak).
  GET   /auth/callback — завершить вход → identity → сессия → редирект на /.
  POST  /auth/logout   — очистить сессию.
"""
import logging

import httpx
from authlib.integrations.base_client.errors import OAuthError
from app.api import errors
from app.api.errors import ApiError
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.auth.factory import build_auth_provider, build_authorizer
from app.auth.identity import AuthenticatedIdentity
from app.auth.models import AuthMeOut, SimulateLoginIn
from app.auth.service import (
    identity_from_session,
    public_user,
    store_identity,
)
from app.auth.providers.simulation import SimulationProvider
from app.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)


def _resolve_to_user(identity: AuthenticatedIdentity):
    """identity → User с пересчитанной ролью; None при fail-closed."""

    settings = get_settings()
    role = build_authorizer(settings).resolve_role(identity.groups)
    if role is None:
        return None
    return identity.to_user(build_authorizer(settings))


@router.get("/me", response_model=AuthMeOut)
def auth_me(request: Request):
    settings = get_settings()
    provider = build_auth_provider(settings)
    identity = identity_from_session(request)

    if identity is None:
        return AuthMeOut(mode=provider.mode, user=public_user(), sim_users=None)

    user = _resolve_to_user(identity)
    if user is None:
        return AuthMeOut(mode=provider.mode, user=public_user(), sim_users=None)

    sim_users = None
    identities = provider.list_identities()
    if identities is not None:
        sim_users = [
            u for u in (_resolve_to_user(i) for i in identities) if u is not None
        ]

    return AuthMeOut(
        mode=provider.mode,
        user=user,
        sim_users=sim_users,
    )


@router.post("/simulate", response_model=AuthMeOut)
def auth_simulate(body: SimulateLoginIn, request: Request):
    """Подобрать демо-юзера из предзаданного списка и открыть сессию."""
    settings = get_settings()
    provider = build_auth_provider(settings)

    if not isinstance(provider, SimulationProvider):
        raise ApiError(
            status_code=400,
            code=errors.AUTH_DISABLED,
            detail="simulation недоступен для " + provider.key,
        )

    identity = provider.resolve(body.username)
    if identity is None:
        raise ApiError(
            status_code=400,
            code=errors.USER_NOT_FOUND,
            detail="Неизвестный демо-пользователь",
        )

    user = _resolve_to_user(identity)
    if user is None:
        raise ApiError(
            status_code=403,
            code=errors.NO_ROLE,
            detail="Пользователю не назначена роль — доступ запрещён",
        )

    store_identity(request, identity)
    return {
        "mode": provider.mode,
        "user": user,
        "sim_users": None,
    }


@router.post("/logout", name="auth_logout")
async def logout(request: Request):
    settings = get_settings()
    provider = build_auth_provider(settings)
    resp = await provider.logout(request)
    if resp is not None:
        # OIDC: RP-Initiated Logout — редирект на end_session_endpoint Keycloak.
        return {"redirect_url": resp.headers.get("location", "/")}
    # simulation/disabled: сессия уже очищена — редирект на корень (login-gate).
    return {"redirect_url": "/"}


@router.get("/login", name="auth_login")
async def login(request: Request):
    settings = get_settings()
    provider = build_auth_provider(settings)
    if provider.is_disabled():
        raise ApiError(
            status_code=400,
            code=errors.AUTH_DISABLED,
            detail="Авторизация отключена",
        )
    try:
        return await provider.start_login(request)
    except httpx.HTTPError as exc:
        # Keycloak недоступен при старте входа (браузерный переход) —
        # редирект с сообщением, а не 500.
        logger.warning("Keycloak недоступен при старте входа: %s", exc)
        return RedirectResponse(url="/?auth_error=unavailable", status_code=303)


@router.get("/callback", name="auth_callback")
async def auth_callback(request: Request):
    settings = get_settings()
    provider = build_auth_provider(settings)
    if provider.is_disabled():
        raise ApiError(
            status_code=400,
            code=errors.AUTH_DISABLED,
            detail="Авторизация отключена",
        )

    try:
        identity = await provider.handle_callback(request)
    except OAuthError as exc:
        # Keycloak вернул error/error_description (например authentication_expired).
        # Не 500, а понятное сообщение с возможностью войти заново.
        logger.warning(
            "OAuth callback: Keycloak вернул ошибку (%s %s)",
            getattr(exc, "error", None),
            getattr(exc, "description", None),
        )
        return RedirectResponse(url="/?auth_error=session_expired", status_code=303)
    except httpx.HTTPError as exc:
        # Token/userinfo endpoints are network calls too; a transient IdP outage
        # should be recoverable from the login page instead of becoming a 500.
        logger.warning("Keycloak недоступен во время callback: %s", exc)
        return RedirectResponse(url="/?auth_error=unavailable", status_code=303)

    user = _resolve_to_user(identity)
    if user is None:
        raise ApiError(
            status_code=403,
            code=errors.NO_ROLE,
            detail="Пользователю не назначена роль — доступ запрещён",
        )

    store_identity(request, identity)
    return RedirectResponse(url="/", status_code=303)
