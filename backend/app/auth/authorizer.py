"""Изолированная авторизация (принцип 4): маппинг групп → роли.

GroupRoleAuthorizer работает только со списком строк groups и ничего не знает
о том, откуда эти группы взялись (OIDC-claim, LDAP memberOf, API клиента).
Это тот разрыв связи, который позволяет переключать провайдера аутентификации,
не трогая авторизацию.

Fail-closed (принцип 5): если default_role is None и ни одна группа не совпала —
resolve_role возвращает None, что на уровне require_user даёт 403.
"""
from __future__ import annotations

# Приоритет ролей: чем раньше, тем выше. При пересечении групп побеждает старшая.
ROLE_PRIORITY: tuple[str, ...] = ("security", "admin", "editor", "viewer")


class GroupRoleAuthorizer:
    def __init__(self, role_groups: dict[str, str], default_role: str | None):
        self._mapping = role_groups or {}
        self._default_role = default_role

    def resolve_role(self, groups: list[str]) -> str | None:
        """Возвращает роль по группам и маппингу, default или None (fail-closed)."""
        matched: set[str] = set()
        for g in groups:
            role = self._mapping.get(g)
            if role:
                matched.add(role)
        if matched:
            for r in ROLE_PRIORITY:
                if r in matched:
                    return r
        return self._default_role
