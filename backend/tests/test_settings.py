"""Тесты настроек чата: парсинг пресетов top_k, валидация границ и HTTP-контракт."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


def _app_with(monkeypatch, settings):
    """Приложение, собранное на переданных settings (без реального .env)."""
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    return create_app()


def _middleware_options(app, class_name: str) -> dict:
    """Аргументы, с которыми middleware зарегистрирован в приложении."""
    for mw in app.user_middleware:
        if mw.cls.__name__ == class_name:
            return mw.kwargs
    raise AssertionError(f"{class_name} не зарегистрирован")


class TestTopKPresetsParsing:
    def test_parses_and_sorts_csv(self):
        s = Settings(
            chat_top_k_min=1,
            chat_top_k_max=20,
            chat_top_k_default=5,
            chat_top_k_presets="10, 4, 5, 4",
        )
        assert s.chat_top_k_presets == [4, 5, 10]

    def test_parses_list_and_dedupes(self):
        s = Settings(chat_top_k_presets=[10, 4, 5, 4, 10])
        assert s.chat_top_k_presets == [4, 5, 10]

    def test_defaults(self):
        s = Settings(_env_file=None)
        assert s.chat_top_k_min == 1
        assert s.chat_top_k_max == 30
        assert s.chat_top_k_default == 10
        assert s.chat_top_k_presets == [5, 10, 20]


class TestTopKBounds:
    def test_min_exceeds_max(self):
        with pytest.raises(ValueError):
            Settings(chat_top_k_min=10, chat_top_k_max=1)

    def test_default_out_of_range(self):
        with pytest.raises(ValueError):
            Settings(chat_top_k_min=2, chat_top_k_max=8, chat_top_k_default=9)

    def test_preset_out_of_bounds(self):
        with pytest.raises(ValueError):
            Settings(chat_top_k_min=2, chat_top_k_max=8, chat_top_k_presets=[1, 4, 5])

    def test_valid_edges_pass(self):
        s = Settings(chat_top_k_min=1, chat_top_k_max=10, chat_top_k_default=10, chat_top_k_presets=[1, 10])
        assert s.chat_top_k_presets == [1, 10]


class TestSettingsEndpoint:
    def test_settings_endpoint_returns_valid_schema(self, tmp_path: Path, monkeypatch):
        settings = Settings(_env_file=None, data_dir=tmp_path)
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        monkeypatch.setattr("app.main.get_settings", lambda: settings)

        with TestClient(create_app()) as client:
            resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["top_k_min"] == 1
        assert data["top_k_max"] == 30
        assert data["top_k_default"] == 10
        assert data["top_k_presets"] == [5, 10, 20]


class TestProductionAuthGuard:
    """В production авторизация не может быть отключена или заменена демо-провайдером."""

    def test_disabled_rejected_in_production(self):
        with pytest.raises(ValueError, match="недопустим для production"):
            Settings(_env_file=None, environment="production", auth_provider="disabled")

    def test_simulation_rejected_in_production(self):
        with pytest.raises(ValueError, match="недопустим для production"):
            Settings(_env_file=None, environment="production", auth_provider="simulation")

    def test_multiple_workers_rejected_in_production(self, monkeypatch):
        monkeypatch.setenv("UVICORN_WORKERS", "2")
        with pytest.raises(ValueError, match="single-replica"):
            Settings(
                _env_file=None,
                environment="production",
                auth_provider="keycloak_oidc",
                keycloak_url="https://kc",
                keycloak_realm="r",
                keycloak_client_id="id",
                keycloak_client_secret="secret",
                app_secret_key="x" * 32,
                auth_session_https_only=True,
            )

    def test_disabled_allowed_in_development(self):
        s = Settings(_env_file=None, environment="development", auth_provider="disabled")
        assert s.auth_provider == "disabled"

    def test_simulation_allowed_in_development(self):
        s = Settings(_env_file=None, environment="development", auth_provider="simulation")
        assert s.auth_provider == "simulation"

class TestCorsCredentials:
    """CORS-allow-list должен реально включать кросс-доменную сессию.

    Раньше CORSMiddleware поднимался без allow_credentials, а cookie ставилась
    SameSite=Lax: при непустом CORS_ALLOWED_ORIGINS браузер всё равно не слал
    cookie, и настройка выглядела рабочей, не будучи таковой.
    """

    def _settings(self, tmp_path, **over):
        base = dict(_env_file=None, data_dir=tmp_path)
        base.update(over)
        return Settings(**base)

    def test_empty_list_keeps_lax_and_no_credentials(self, tmp_path, monkeypatch):
        settings = self._settings(tmp_path)
        assert settings.cors_allowed_origins == []
        app = _app_with(monkeypatch, settings)
        cors = _middleware_options(app, "CORSMiddleware")
        session = _middleware_options(app, "SessionMiddleware")
        assert cors["allow_credentials"] is False
        assert session["same_site"] == "lax"

    def test_configured_list_enables_credentials_and_samesite_none(self, tmp_path, monkeypatch):
        settings = self._settings(
            tmp_path,
            cors_allowed_origins=["https://kb.example.com"],
            auth_session_https_only=True,
        )
        app = _app_with(monkeypatch, settings)
        cors = _middleware_options(app, "CORSMiddleware")
        session = _middleware_options(app, "SessionMiddleware")
        assert cors["allow_credentials"] is True
        assert session["same_site"] == "none"

    def test_wildcard_rejected(self, tmp_path):
        with pytest.raises(ValidationError, match="CORS_ALLOWED_ORIGINS"):
            self._settings(tmp_path, cors_allowed_origins=["*"], auth_session_https_only=True)

    def test_origin_without_scheme_rejected(self, tmp_path):
        with pytest.raises(ValidationError, match="со схемой"):
            self._settings(
                tmp_path, cors_allowed_origins=["kb.example.com"], auth_session_https_only=True
            )

    def test_cross_origin_requires_secure_cookie(self, tmp_path):
        """SameSite=None браузер принимает только с Secure."""
        with pytest.raises(ValidationError, match="AUTH_SESSION_HTTPS_ONLY"):
            self._settings(
                tmp_path,
                cors_allowed_origins=["https://kb.example.com"],
                auth_session_https_only=False,
            )
