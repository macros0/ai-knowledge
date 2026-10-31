from types import SimpleNamespace

import pytest

from app.services.artifacts import get_blob_store
from app.services.artifacts.local import LocalBlobStore


def settings(tmp_path, **values):
    return SimpleNamespace(data_dir=tmp_path, artifact_backend="local", artifact_store_id="test",
                           artifact_root=None, **values)


def test_legacy_default_factory_is_local(tmp_path):
    store = get_blob_store(settings(tmp_path))
    assert isinstance(store, LocalBlobStore)
    assert store.root == tmp_path / "artifacts"


def test_rwx_factory_never_creates_missing_shared_root(tmp_path):
    value = settings(tmp_path)
    value.artifact_backend = "rwx"
    value.artifact_root = tmp_path / "missing-mount"
    with pytest.raises(ValueError):
        get_blob_store(value)
    assert not value.artifact_root.exists()


def test_s3_factory_requires_explicit_credentials_before_sdk_access(tmp_path):
    value = settings(tmp_path)
    value.artifact_backend = "s3"
    value.artifact_s3_endpoint = "https://s3.invalid"
    value.artifact_s3_bucket = "test-bucket"
    with pytest.raises(ValueError, match="credentials"):
        get_blob_store(value)


def test_s3_credentials_are_excluded_from_settings_serialization():
    from app.config import Settings
    value = Settings(_env_file=None, artifact_s3_access_key="private-access",
                     artifact_s3_secret_key="private-secret", artifact_s3_session_token="private-session")
    assert value.artifact_s3_access_key == "private-access"
    assert "private-access" not in repr(value)
    assert "private-secret" not in repr(value)
    assert "private-session" not in value.model_dump_json()
