"""Нормализованная модель аутентифицированного пользователя (принцип 1).

Любой провайдер (OIDC, LDAP, проприетарный API) на выходе отдаёт единую
структуру — AuthenticatedIdentity. Это единственная точка «перевода» специфики
провайдера (sub / dn / id, claim group / memberOf / поле API) в общий язык
приложения. Авторизация (роли) сюда НЕ примешивается — она считается отдельным
слоем GroupRoleAuthorizer (см. authorizer.py).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.auth.authorizer import GroupRoleAuthorizer
    from app.auth.models import User

# Стандартный маппинг полей провайдера → поля AuthenticatedIdentity.
# Для custom_client задаётся через конфиг (field_mapping).
DEFAULT_FIELD_MAPPING: dict[str, str] = {
    "external_id": "sub",
    "username": "preferred_username",
    "email": "email",
    "groups": "group",
}


def normalize_groups(raw: Any, separator: str = ",") -> list[str]:
    """Приводит group-claim любой формы к списку строк (с сохранением порядка).

    Обрабатывает все формы, которые может отдать провайдер:
      None → []
      str  → split по separator (strip, отбросить пустые)
      list/tuple/set → flatten рекурсивно
      dict → развернуть известные ключи ("group"/"groups"), иначе рекурсия по
             values (порядок разбора важен: сначала известные ключи, чтобы не
             "расплющить" случайно не те данные глубокой структуры)
      прочее → [str(x)]

    Dedup с сохранением порядка: ["A", "B", "A"] → ["A", "B"]. Порядок важен для
    приоритетной резолюции роли в GroupRoleAuthorizer.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [g.strip() for g in raw.split(separator) if g.strip()]
    if isinstance(raw, (list, tuple, set)):
        out: list[str] = []
        for item in raw:
            out.extend(normalize_groups(item, separator))
        return _dedup(out)
    if isinstance(raw, dict):
        for key in ("group", "groups"):
            if key in raw:
                return normalize_groups(raw[key], separator)
        out = []
        for value in raw.values():
            out.extend(normalize_groups(value, separator))
        return _dedup(out)
    return [str(raw)]


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


class AuthenticatedIdentity(BaseModel):
    """Провайдер-независимая идентичность.

    external_id — стабильный уникальный ID (sub / LDAP dn / id из API клиента).
    attributes — сырые claims/данные провайдера (для custom-логики и аудита).
    """

    external_id: str
    username: str
    email: str | None = None
    groups: list[str] = Field(default_factory=list)
    provider: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        raw: dict[str, Any],
        field_mapping: dict[str, str] | None = None,
        provider: str = "",
        default_username: str = "",
        group_separator: str = ",",
    ) -> "AuthenticatedIdentity":
        """Перевод специфики провайдера в нормализованную модель.

        field_mapping: {поле_идентичности: имя_поля_провайдера}. По умолчанию —
        OIDC-claims (sub / preferred_username / email / group). Заданные ключи
        переопределяют стандарт частично (partial override): незаданные поля
        падают на стандартные OIDC-имена.
        """
        mapping = {**DEFAULT_FIELD_MAPPING, **(field_mapping or {})}

        def pick(field: str) -> Any:
            src = mapping.get(field, field)
            return raw.get(src)

        return cls(
            external_id=str(pick("external_id") or ""),
            username=pick("username") or default_username,
            email=pick("email"),
            groups=normalize_groups(pick("groups"), separator=group_separator),
            provider=provider,
            attributes=raw,
        )

    def to_user(self, authorizer: "GroupRoleAuthorizer") -> "User":
        """Перевод identity → User с вычисленными ролями.

        Единственное место, где identity превращается в доменную модель.
        Роль пересчитывается на каждом запросе (не кэшируется в сессии).
        """
        from app.auth.models import User

        role = authorizer.resolve_role(self.groups)
        return User(
            user_id=self.external_id,
            username=self.username,
            email=self.email,
            groups=self.groups,
            roles=[role] if role else [],
        )
