from typing import Any

from pydantic import BaseModel, Field


class User(BaseModel):
    """Пользователь: аноним (mode=disabled) либо из сессии/токена.

    Поля выровнены под будущую схему БД (MIGRATION_PLAN.md): user_id = sub
    (стабильный SSO ID), username = preferred_username. groups — из claim'а
    `group`; roles — итоговые роли, вычисленные из маппинга AUTH_ROLE_GROUPS
    (сейчас 0..1 элемент; в БД — связь через user_roles).
    """

    user_id: str = Field(default="anonymous", description="Стабильный ID (sub)")
    username: str = Field(default="anonymous", description="Логин (preferred_username)")
    email: str | None = None
    groups: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        # Сессия хранит только чистый JSON (Starlette SessionMiddleware).
        return super().model_dump_json(*args, **kwargs)


class SimulateLoginIn(BaseModel):
    username: str


class AuthMeOut(BaseModel):
    mode: str  # disabled | simulation | sso
    user: User
    sim_users: list[User] | None = None