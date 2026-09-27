"""Изолированная авторизация (принцип 4): маппинг групп → роли.

GroupRoleAuthorizer работает только со списком строк groups и ничего не знает
о том, откуда эти группы взялись (OIDC-claim, LDAP memberOf, API клиента).
Это тот разрыв связи, который позволяет переключать провайдера аутентификации,
не трогая авторизацию.

Fail-closed (принцип 5): если default_role is None и ни одна группа не совпала —
resolve_role возвращает None, что на уровне require_user даёт 403.

Пользователь получает ВСЕ роли своих групп (resolve_roles). До 27.09.2026 роль
была одна — старшая по приоритету, и участник KB_Security + KB_Admin молча
терял права admin. Разделение обязанностей обеспечивается членством в группах
IdP, а не тем, что приложение отбрасывает часть ролей.
"""
from __future__ import annotations

# Порядок ролей: старшая первой. Определяет основную роль для отображения
# (resolve_role) и порядок списка ролей; права — объединение всех ролей.
ROLE_PRIORITY: tuple[str, ...] = ("security", "admin", "editor", "viewer")


class GroupRoleAuthorizer:
    def __init__(self, role_groups: dict[str, str], default_role: str | None):
        self._mapping = role_groups or {}
        self._default_role = default_role

    def resolve_roles(self, groups: list[str]) -> list[str]:
        """Все известные роли групп (старшая первой); иначе [default] или [].

        Роль вне ROLE_PRIORITY (опечатка в AUTH_ROLE_GROUPS) не считается
        совпадением — как и раньше, иначе она пропускала бы require_user.
        """
        matched = {self._mapping.get(g) for g in groups}
        known = [r for r in ROLE_PRIORITY if r in matched]
        if known:
            return known
        return [self._default_role] if self._default_role else []

    def resolve_role(self, groups: list[str]) -> str | None:
        """Основная (старшая) роль или None (fail-closed)."""
        roles = self.resolve_roles(groups)
        return roles[0] if roles else None
