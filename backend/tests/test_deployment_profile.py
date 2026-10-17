"""Environment identity is separate from the application's production protections."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_existing_local_defaults_unchanged():
    settings = Settings(_env_file=None)
    assert settings.deployment_tier == "local"
    assert settings.model_mode == "live"
    assert settings.environment == "development"


@pytest.mark.parametrize("mode", ["stub", "unavailable"])
def test_prod_rejects_test_model_modes(mode):
    with pytest.raises(ValidationError, match="MODEL_MODE"):
        Settings(_env_file=None, deployment_tier="prod", model_mode=mode)


def test_prod_rejects_fake_embeddings():
    with pytest.raises(ValidationError, match="EMBEDDING_PROVIDER"):
        Settings(_env_file=None, deployment_tier="prod", embedding_provider="fake")


@pytest.mark.parametrize("tier", ["dev", "test", "home", "ci"])
def test_test_environments_allow_explicit_stub(tier):
    settings = Settings(_env_file=None, deployment_tier=tier, model_mode="stub", embedding_provider="fake")
    assert settings.deployment_tier == tier
    assert settings.model_mode == "stub"


@pytest.mark.parametrize("field,value", [("deployment_tier", "unknown"), ("model_mode", "unknown")])
def test_invalid_profile_is_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
