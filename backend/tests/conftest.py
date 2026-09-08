"""Общие фикстуры для тестов бэкенда.

Авторизация: убирает из окружения переменные auth/Keycloak перед каждым
тестом. Без этого pydantic-settings (читает env-переменные с приоритетом над
dotenv) подхватывает AUTH_PROVIDER/KEYCLOAK из окружения pytest-процесса, и старые
тесты, строящие Settings без явного auth_provider, случайно получают 'keycloak_oidc' → 401.

БД: каждый тест работает с изолированной SQLite-БД (временный файл). Файловая
(не in-memory), чтобы фоновые потоки пайплайна и request-потоки делили один
движок без рассинхронизации.
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
    # Явно development: fail-fast проверки config.Settings активны только в
    # production, тесты не должны зависеть от дефолта класса или env процесса.
    monkeypatch.setenv("ENVIRONMENT", "development")
    yield


@pytest.fixture(autouse=True)
def _db(tmp_path):
    """Изолированная SQLite-БД на каждый тест (см. app.db.session.configure_for_tests)."""
    from app.db.session import configure_for_tests, init_db
    from app.services.pipeline import reset_pipeline

    configure_for_tests(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    init_db()
    # Синглтон пайплайна держит settings и состояние обработки: без сброса он
    # пережил бы тест вместе с чужими tmp_path (см. services/pipeline.py).
    reset_pipeline()
    yield
    reset_pipeline()
