"""Тонкий слой: текущий пользователь + FastAPI-зависимость.

Вся логика вынесена в провайдеры (аутентификация) и authorizer (авторизация).
Здесь только:
  - чтение AuthenticatedIdentity из сессии;
  - require_user / current_user — перевод identity → User с пересчётом роли.

Сессия хранит AuthenticatedIdentity (groups), роль НЕ кэшируется — вычисляется
через authorizer.resolve_role() на каждом запросе.
"""
import logging
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from typing import NoReturn

from app.api import errors
from app.api.errors import ApiError
from fastapi import Request

from app import config as config_mod
from app.auth.factory import build_auth_provider, build_authorizer
from app.auth.identity import AuthenticatedIdentity
from app.auth.models import User
from app.db.models import AuthSession
from app.db.session import session_scope

logger = logging.getLogger(__name__)

_SESSION_KEY = "identity"


def public_user() -> User:
    """Пользователь для режима disabled (аноним)."""
    return User(user_id="anonymous", username="anonymous", groups=[], roles=[])


def identity_from_session(request: Request) -> AuthenticatedIdentity | None:
    """Загружает identity по opaque session-id из серверного хранилища."""
    raw = request.session.get(_SESSION_KEY) or {}
    session_id = raw.get("session_id") if isinstance(raw, dict) else None
    if not isinstance(session_id, str) or not session_id:
        return None
    try:
        key = hashlib.sha256(session_id.encode("ascii")).hexdigest()
        with session_scope() as db:
            row = db.get(AuthSession, key)
            expires_at = row.expires_at if row is not None else None
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if row is None or expires_at <= datetime.now(timezone.utc):
                if row is not None:
                    db.delete(row)
                request.session.clear()
                return None
            return AuthenticatedIdentity(**row.identity)
    except Exception:
        logger.warning("Некорректная server-side session")
        return None


def store_identity(request: Request, identity: AuthenticatedIdentity) -> None:
    session_id = secrets.token_urlsafe(32)
    key = hashlib.sha256(session_id.encode("ascii")).hexdigest()
    settings = config_mod.get_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.auth_session_ttl_seconds)
    payload = identity.model_dump(mode="json")
    id_token = (payload.get("attributes") or {}).get("id_token")
    with session_scope() as db:
        db.add(AuthSession(
            id=key,
            external_id=identity.external_id,
            identity=payload,
            id_token=id_token,
            expires_at=expires_at,
        ))
    request.session.clear()
    request.session[_SESSION_KEY] = {"session_id": session_id}


def clear_identity(request: Request) -> None:
    raw = request.session.get(_SESSION_KEY) or {}
    session_id = raw.get("session_id") if isinstance(raw, dict) else None
    if isinstance(session_id, str) and session_id:
        try:
            key = hashlib.sha256(session_id.encode("ascii")).hexdigest()
            with session_scope() as db:
                row = db.get(AuthSession, key)
                if row is not None:
                    db.delete(row)
        except Exception:
            logger.warning("Не удалось удалить server-side session", exc_info=True)
    request.session.clear()


def session_id_token(request: Request) -> str | None:
    """Возвращает id_token только из server-side записи для OIDC logout."""
    raw = request.session.get(_SESSION_KEY) or {}
    session_id = raw.get("session_id") if isinstance(raw, dict) else None
    if not isinstance(session_id, str) or not session_id:
        return None
    try:
        key = hashlib.sha256(session_id.encode("ascii")).hexdigest()
        with session_scope() as db:
            row = db.get(AuthSession, key)
            return row.id_token if row is not None else None
    except Exception:
        logger.warning("Не удалось прочитать id_token server-side session", exc_info=True)
        return None


def purge_expired_sessions() -> int:
    """Delete expired server-side sessions and return the number removed."""
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        rows = db.query(AuthSession).filter(AuthSession.expires_at <= now).all()
        for row in rows:
            db.delete(row)
        return len(rows)


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
