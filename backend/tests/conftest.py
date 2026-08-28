"""Общие фикстуры для тестов бэкенда.

Авторизация: убирает из окружения переменные auth/Keycloak перед каждым
тестом. Без этого pydantic-settings (читает env-переменные с приоритетом над
dotenv) подхватывает AUTH_PROVIDER/KEYCLOAK из окружения pytest-процесса, и старые
тесты, строящие Settings без явного auth_provider, случайно получают 'keycloak_oidc' → 401.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _flush_auth_env(monkeypatch):
    auth_vars = [
        k
        for k in os.environ
        if k.startswith(("AUTH_", "KEYC", "SSO_"))
    ]
    for var in auth_vars:
        monkeypatch.delenv(var, raising=False)
    yield