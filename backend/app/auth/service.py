"""Тонкий слой: текущий пользователь + FastAPI-зависимость.

Вся логика вынесена в провайдеры (аутентификация) и authorizer (авторизация).
Здесь только:
  - чтение AuthenticatedIdentity из сессии;
  - require_user / current_user — перевод identity → User с пересчётом роли.

Сессия хранит AuthenticatedIdentity (groups), роль НЕ кэшируется — вычисляется
через authorizer.resolve_role() на каждом запросе.
"""
import logging

from typing import NoReturn

from app.api import errors
from app.api.errors import ApiError
from fastapi import HTTPException, Request

from app import config as config_mod
from app.auth.factory import build_auth_provider, build_authorizer
from app.auth.identity import AuthenticatedIdentity
from app.auth.models import User

logger = logging.getLogger(__name__)

_SESSION_KEY = "identity"


def public_user() -> User:
    """Пользователь для режима disabled (аноним)."""
    return User(user_id="anonymous", username="anonymous", groups=[], roles=[])


def identity_from_session(request: Request) -> AuthenticatedIdentity | None:
    """Читает AuthenticatedIdentity, записанную при входе /simulate или /callback."""
    raw = request.session.get(_SESSION_KEY)
    if not raw:
        return None
    try:
        return AuthenticatedIdentity(**raw)
    except Exception:
        logger.warning("Некорректная identity в сессии: %r", raw)
        return None


def store_identity(request: Request, identity: AuthenticatedIdentity) -> None:
    request.session[_SESSION_KEY] = identity.model_dump()


def clear_identity(request: Request) -> None:
    request.session.pop(_SESSION_KEY, None)


def _unauthorized() -> NoReturn:
    raise ApiError(
            status_code=401,
            code=errors.AUTH_REQUIRED,
            detail="Требуется авторизация",
        headers={"WWW-Authenticate": "Bearer"},
        )


def _forbidden() -> NoReturn:
    raise ApiError(
            status_code=403,
            code=errors.NO_ROLE,
            detail="Пользователю не назначена роль — доступ запрещён",
        )


def _blocked_user() -> NoReturn:
    raise ApiError(
            status_code=403,
            code=errors.USER_BLOCKED,
            detail="Пользователь заблокирован. Обратитесь к администратору безопасности.",
        )


def _resolve(identity: AuthenticatedIdentity) -> User | None:
    """identity → User; None при fail-closed (роль не опознана)."""
    settings = config_mod.get_settings()
    authorizer = build_authorizer(settings)
    role = authorizer.resolve_role(identity.groups)
    if role is None:
        return None
    return identity.to_user(authorizer)


def _blocked(identity: AuthenticatedIdentity) -> bool:
    """Проверка блоклиста (user_blocks) — единственное активное действие Security."""
    if not identity.external_id:
        return False
    from app.services.blocklist import get_blocklist

    return get_blocklist().is_blocked(identity.external_id)


def require_user(request: Request) -> User:
    """FastAPI-зависимость: текущий пользователь либо 401/403.

    В disabled-режиме провайдер is_disabled() → аноним. Иначе:
    - нет сессии → 401;
    - роль не опознана и fail-closed (auth_default_role=None) → 403;
    - пользователь в активном блоклисте → 403 (заблокирован Security).
    """
    settings = config_mod.get_settings()
    provider = build_auth_provider(settings)
    if provider.is_disabled():
        return public_user()

    identity = identity_from_session(request)
    if identity is None:
        _unauthorized()

    if _blocked(identity):
        _blocked_user()

    user = _resolve(identity)
    if user is None:
        _forbidden()
    return user


def current_user(request: Request) -> User:
    """Мягче: не бросает исключений, возвращает анонима при отсутствии сессии/роли."""
    settings = config_mod.get_settings()
    provider = build_auth_provider(settings)
    if provider.is_disabled():
        return public_user()

    identity = identity_from_session(request)
    if identity is None:
        return public_user()
    if _blocked(identity):
        return public_user()
    user = _resolve(identity)
    return user if user is not None else public_user()


def require_role(*roles: str):
    """FastAPI-зависимость: пользователь должен обладать хотя бы одной из ролей.

    В disabled-режиме пропускает всех (локальная разработка). Иначе делегирует
    require_user (401/403/блоклист) и дополнительно проверяет роль → 403.
    """

    def dependency(request: Request) -> User:
        settings = config_mod.get_settings()
        provider = build_auth_provider(settings)
        if provider.is_disabled():
            return public_user()
        user = require_user(request)
        if not (set(roles) & set(user.roles)):
            raise ApiError(
            status_code=403,
            code=errors.FORBIDDEN,
            detail="Недостаточно прав для выполнения операции",
        )
        return user

    return dependency
