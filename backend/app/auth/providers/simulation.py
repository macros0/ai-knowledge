"""Провайдер «simulation»: фиксированные демо-пользователи из конфига.

Вход через выбор пользователя (/auth/simulate) без внешнего IdP.
Идентичности строятся из settings.auth_sim_users.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from app.auth.identity import AuthenticatedIdentity
from app.auth.providers.base import AuthProvider

if TYPE_CHECKING:
    from app.config import Settings


class SimulationProvider(AuthProvider):
    key = "simulation"
    mode = "simulation"

    def __init__(self, settings: "Settings"):
        self._settings = settings

    def list_identities(self) -> list[AuthenticatedIdentity]:
        return [
            AuthenticatedIdentity(
                external_id=raw.get("user_id") or raw.get("username", ""),
                username=raw.get("username", ""),
                email=raw.get("email"),
                groups=list(raw.get("groups") or []),
                provider=self.key,
            )
            for raw in self._settings.auth_sim_users
        ]

    async def start_login(self, request: Request):
        raise HTTPException(
            status_code=400,
            detail="simulation использует POST /auth/simulate, а не redirect",
        )

    async def handle_callback(self, request: Request) -> AuthenticatedIdentity:
        raise HTTPException(status_code=400, detail="simulation не имеет callback")

    def resolve(self, username: str) -> AuthenticatedIdentity | None:
        for identity in self.list_identities():
            if identity.username == username:
                return identity
        return None
