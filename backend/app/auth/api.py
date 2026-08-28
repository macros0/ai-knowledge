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
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth.factory import build_auth_provider, build_authorizer
from app.auth.identity import AuthenticatedIdentity
from app.auth.models import AuthMeOut, SimulateLoginIn
from app.auth.service import (
    clear_identity,
    identity_from_session,
    public_user,
    store_identity,
)
from app.auth.providers.simulation import SimulationProvider
from app.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _resolve_to_user(identity: AuthenticatedIdentity):
    """identity → User с пересчитанной ролью; None при fail-closed."""
    from app.auth.models import User

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
        raise HTTPException(status_code=400, detail="simulation недоступен для " + provider.key)

    identity = provider.resolve(body.username)
    if identity is None:
        raise HTTPException(status_code=400, detail="Неизвестный демо-пользователь")

    user = _resolve_to_user(identity)
    if user is None:
        raise HTTPException(status_code=403, detail="Пользователю не назначена роль — доступ запрещён")

    store_identity(request, identity)
    return {
        "mode": provider.mode,
        "user": user,
        "sim_users": None,
    }


@router.post("/logout")
async def logout(request: Request):
    settings = get_settings()
    provider = build_auth_provider(settings)
    await provider.logout(request)
    clear_identity(request)
    return {"ok": True}


@router.get("/login", name="auth_login")
async def login(request: Request):
    settings = get_settings()
    provider = build_auth_provider(settings)
    if provider.is_disabled():
        raise HTTPException(status_code=400, detail="Авторизация отключена")
    return await provider.start_login(request)


@router.get("/callback", name="auth_callback")
async def auth_callback(request: Request):
    settings = get_settings()
    provider = build_auth_provider(settings)
    if provider.is_disabled():
        raise HTTPException(status_code=400, detail="Авторизация отключена")

    identity = await provider.handle_callback(request)

    user = _resolve_to_user(identity)
    if user is None:
        raise HTTPException(status_code=403, detail="Пользователю не назначена роль — доступ запрещён")

    store_identity(request, identity)
    return RedirectResponse(url="/", status_code=303)
