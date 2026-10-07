"""Connection isolation and deployment-safe public contracts."""

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.api.settings import get_chat_settings



def test_new_installation_disables_assessment(monkeypatch):
    monkeypatch.delenv("SOURCE_ASSESSMENT_ENABLED", raising=False)
    settings = Settings(_env_file=None)
    assert settings.source_assessment_enabled is False
    public = get_chat_settings(settings).model_dump()["source_assessment"]
    assert public["available"] is False
    assert public["default_enabled"] is False


def test_assessment_defaults():
    s = Settings(_env_file=None, source_assessment_enabled=True)
    assert s.source_assessment_enabled is True
    assert s.source_assessment_sample_size == 5


@pytest.mark.parametrize("value", [0, 21, True, 1.5, "five"])
def test_sample_size_strict_1_to_20(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, source_assessment_sample_size=value)


def test_total_budget_consistency():
    with pytest.raises(ValidationError, match="source_assessment_max_total_chars"):
        Settings(
            _env_file=None, source_assessment_sample_size=20, source_assessment_max_total_chars=1000
        )


def test_connection_and_config_are_isolated():
    from app.services.source_assessment.config import (
        resolve_assessment_connection,
        resolve_assessment_config,
    )

    s = Settings(
        _env_file=None,
        llm_model="base",
        llm_chat_model="chat",
        llm_api_key="main-secret",
        llm_profile="local_qwen",
    )
    assert resolve_assessment_config(s).model_id == "chat"
    assert resolve_assessment_connection(s).settings.llm_api_key == "main-secret"
    s2 = s.model_copy(update={"source_assessment_llm_base_url": "http://127.0.0.1:19000/v1"})
    connection = resolve_assessment_connection(s2)
    assert connection.settings.llm_api_key == ""
    assert connection.settings.llm_profile == "standard"
    assert s.llm_api_key == "main-secret"
    assert s.llm_profile == "local_qwen"


def test_key_without_endpoint_rejected():
    with pytest.raises(ValidationError, match="source_assessment_llm_api_key"):
        Settings(_env_file=None, source_assessment_llm_api_key="never-print-this")


def test_unsafe_connection_values_are_rejected_without_echoing_secrets():
    for value in [
        "https://user:secret@host/v1",
        "https://host/v1?key=secret",
        "https://host/v1#secret",
    ]:
        with pytest.raises(ValidationError) as caught:
            Settings(_env_file=None, source_assessment_llm_base_url=value)
        assert "secret" not in str(caught.value)


def test_public_settings_hide_connection():
    s = Settings(
        _env_file=None,
        source_assessment_enabled=True,
        source_assessment_llm_base_url="https://gateway.invalid/v1",
        source_assessment_llm_api_key="hidden-key",
    )
    public = get_chat_settings(s).model_dump()
    assert public["source_assessment"] == {
        "available": True,
        "default_enabled": True,
        "sample_size": 5,
    }
    assert "hidden-key" not in str(public)
    assert "gateway.invalid" not in str(public)


@pytest.mark.parametrize("timeout", [180, 600])
def test_local_assessment_can_wait_for_slow_model(timeout):
    from app.services.source_assessment.config import resolve_assessment_config

    settings = Settings(
        _env_file=None,
        llm_profile="local_qwen",
        source_assessment_timeout_seconds=timeout,
    )
    assert resolve_assessment_config(settings).timeout_seconds == timeout


@pytest.mark.parametrize("timeout", [0, 601])
def test_assessment_timeout_remains_bounded(timeout):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, source_assessment_timeout_seconds=timeout)
