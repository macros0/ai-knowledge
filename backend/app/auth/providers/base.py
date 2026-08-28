"""Интерфейс AuthProvider (принцип 2).

Вместо ветвлений if auth_mode == ... по коду — набор классов, реализующих один
контракт. Добавление нового способа входа = новый класс + строка в фабрике,
без изменения существующего кода.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request

    from app.auth.identity import AuthenticatedIdentity


class AuthProvider(ABC):
    # Ключ провайдера: "keycloak_oidc" | "direct_ldap" | "custom_client" | ...
    key: str = ""
    # UI-метка для /api/auth/me: "sso" | "simulation" | "disabled".
    mode: str = ""

    def is_disabled(self) -> bool:
        """Провайдер выключает защиту (все считаются анонимами)."""
        return False

    @abstractmethod
    async def start_login(self, request: "Request"):
        """Начать вход (authorize_redirect для OIDC, ошибка для остальных)."""
        raise NotImplementedError

    @abstractmethod
    async def handle_callback(self, request: "Request") -> "AuthenticatedIdentity":
        """Завершить вход → нормализованная идентичность (или исключение)."""
        raise NotImplementedError

    def list_identities(self) -> list["AuthenticatedIdentity"] | None:
        """Доступные тестовые идентичности (для simulation); по умолчанию None."""
        return None

    async def logout(self, request: "Request") -> None:
        """Очистка локальной сессии (необязательно переопределять)."""
        request.session.clear()
