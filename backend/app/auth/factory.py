"""Фабрика + конфигурация вместо хардкода выбора провайдера (принцип 3).

build_auth_provider() читает settings.auth_provider и создаёт нужную реализацию.
Переключение клиента (Keycloak → прямой LDAP → своя система) — правка одного
поля конфига, без изменения существующего кода.
"""
from __future__ import annotations

from app.auth.authorizer import GroupRoleAuthorizer
from app.auth.providers.base import AuthProvider
from app.config import Settings

# Реестр: строка auth_provider → класс провайдера. Новый провайдер = новый класс
# + запись здесь. У класса с конструктором (settings) должен быть единый интерфейс.
_PROVIDER_CLASSES: dict[str, type[AuthProvider]] = {}


def _registry() -> dict[str, type[AuthProvider]]:
    if _PROVIDER_CLASSES:
        return _PROVIDER_CLASSES
    from app.auth.providers import (
        CustomClientProvider,
        DirectLdapProvider,
        DisabledProvider,
        KeycloakOidcProvider,
        SimulationProvider,
    )

    _PROVIDER_CLASSES.update(
        {
            "disabled": DisabledProvider,
            "simulation": SimulationProvider,
            "keycloak_oidc": KeycloakOidcProvider,
            "direct_ldap": DirectLdapProvider,
            "custom_client": CustomClientProvider,
        }
    )
    return _PROVIDER_CLASSES


def build_authorizer(settings: Settings) -> GroupRoleAuthorizer:
    return GroupRoleAuthorizer(settings.auth_role_groups, settings.auth_default_role)


def build_auth_provider(settings: Settings) -> AuthProvider:
    cls = _registry().get(settings.auth_provider)
    if cls is None:
        raise ValueError(
            f"Неизвестный auth_provider: '{settings.auth_provider}'. "
            f"Доступно: {sorted(_registry())}"
        )
    return cls(settings)
