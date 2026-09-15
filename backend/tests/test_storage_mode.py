from __future__ import annotations

import pytest

from app.deployment.storage_mode import StorageModeError, validate_storage_mode


@pytest.mark.parametrize(
    ("mode", "database_url", "qdrant_url"),
    [
        (
            "bundled",
            "postgresql+psycopg://okf:secret@postgres:5432/okf_knowledge",
            "http://qdrant:6333",
        ),
        (
            "external",
            "postgresql+psycopg://okf:secret@db.corp.internal:5432/okf_knowledge",
            "https://vectors.corp.internal:6333",
        ),
    ],
)
def test_valid_storage_modes(mode, database_url, qdrant_url):
    validate_storage_mode(mode, database_url, qdrant_url, "test-secret")


def test_bundled_rejects_external_qdrant():
    with pytest.raises(StorageModeError, match="bundled.*qdrant"):
        validate_storage_mode(
            "bundled",
            "postgresql+psycopg://okf:secret@postgres:5432/okf_knowledge",
            "https://vectors.corp.internal:6333",
            "test-secret",
        )


@pytest.mark.parametrize("host", ["postgres", "qdrant"])
def test_external_rejects_compose_service_hosts(host):
    with pytest.raises(StorageModeError, match="external"):
        validate_storage_mode(
            "external",
            f"postgresql+psycopg://okf:secret@{host}:5432/okf_knowledge",
            "https://vectors.corp.internal:6333",
            "test-secret",
        )


def test_invalid_url_port_is_reported_as_storage_error():
    with pytest.raises(StorageModeError, match="DATABASE_URL"):
        validate_storage_mode(
            "bundled",
            "postgresql+psycopg://okf:secret@postgres:not-a-port/okf_knowledge",
            "http://qdrant:6333",
            "test-secret",
        )
