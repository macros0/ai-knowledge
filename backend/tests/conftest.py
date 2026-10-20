"""Общие фикстуры для тестов бэкенда.

Настройки: изолирует auth/Keycloak, LLM/чат, транспорт аудита и блокировки
редактирования перед каждым тестом. Без этого pydantic-settings (читает env-переменные с приоритетом над
dotenv) подхватывает AUTH_PROVIDER/KEYCLOAK из окружения pytest-процесса, и старые
тесты, строящие Settings без явного auth_provider, случайно получают 'keycloak_oidc' → 401.

БД: каждый тест работает с изолированной SQLite-БД (временный файл). Файловая
(не in-memory), чтобы фоновые потоки пайплайна и request-потоки делили один
движок без рассинхронизации.
"""

import os

import pytest


# Relative to this conftest, so collection also works from the repository root.
# This directory contains artifacts from old local runs, not maintained tests.
collect_ignore = ["tmp"]


@pytest.fixture(autouse=True)
def _flush_auth_env(monkeypatch):
    # LiteLLM can load the development .env into os.environ during collection.
    # Settings(_env_file=None) still reads environment variables: isolate LLM
    # and chat defaults just as auth defaults, without changing the live .env.
    # Local durable/Logstash audit settings also change auth events and payloads.
    runtime_vars = [
        k
        for k in os.environ
        if k.startswith(("AUTH_", "KEYC", "SSO_", "LLM_", "CHAT_", "SOURCE_ASSESSMENT_", "EMBEDDING_"))
        or k in {"AUDIT_STDOUT_MODE", "AUDIT_STDOUT_FORMAT"}
    ]
    for var in runtime_vars:
        monkeypatch.delenv(var, raising=False)
    # Явно development: fail-fast проверки config.Settings активны только в
    # production, тесты не должны зависеть от дефолта класса или env процесса.
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("SOURCE_ASSESSMENT_ENABLED", "false")
    # The live flag is enabled locally; legacy unit fixtures must be explicit.
    # Scope tests opt in on their isolated Settings instance or via monkeypatch.
    monkeypatch.setenv("ATTACHMENT_REVIEW_ENABLED", "false")
    # Local edit-lock rollout must not change legacy API test preconditions.
    # Lock protocol tests enable it explicitly on their isolated Settings.
    monkeypatch.setenv("REFERENCE_EDIT_LOCKS_ENABLED", "false")
    from app.config import get_settings
    get_settings.cache_clear()
    # Integration tests explicitly replace this boundary with a typed provider.
    # Other tests must never silently start a real provider call.
    from app.api import chat
    def unexpected_assessment(*args):
        raise AssertionError("Real assessment provider is forbidden in unit tests")
    monkeypatch.setattr(chat, "get_assessor", unexpected_assessment)
    yield
    get_settings.cache_clear()


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


@pytest.fixture(autouse=True)
def _isolated_rate_limits():
    # Keep real limits and windows within each test, including admission tests.
    from app.services.rate_limiter import get_rate_limiter
    get_rate_limiter().reset()
