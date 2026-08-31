"""Тесты настроек чата: парсинг пресетов top_k, валидация границ и HTTP-контракт."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


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

    def test_disabled_allowed_in_development(self):
        s = Settings(_env_file=None, environment="development", auth_provider="disabled")
        assert s.auth_provider == "disabled"

    def test_simulation_allowed_in_development(self):
        s = Settings(_env_file=None, environment="development", auth_provider="simulation")
        assert s.auth_provider == "simulation"