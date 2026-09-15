"""Pure validation for supported production storage topologies."""

from __future__ import annotations

from urllib.parse import urlparse


class StorageModeError(ValueError):
    """A storage configuration does not match a supported deployment mode."""


def _endpoint(value: str | None, variable: str):
    if not value:
        raise StorageModeError(f"{variable} must be set")
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.hostname:
        raise StorageModeError(f"{variable} must be an absolute URL")
    return parsed


def _port(parsed, variable: str) -> int | None:
    try:
        return parsed.port
    except ValueError as exc:
        raise StorageModeError(f"{variable} has an invalid port") from exc


def validate_storage_mode(
    mode: str,
    database_url: str | None,
    qdrant_url: str | None,
    postgres_password: str | None,
) -> None:
    """Reject every topology other than fully external or fully bundled."""
    database = _endpoint(database_url, "DATABASE_URL")
    qdrant = _endpoint(qdrant_url, "QDRANT_URL")

    if mode == "bundled":
        if not postgres_password:
            raise StorageModeError("POSTGRES_PASSWORD must be set for bundled storage")
        if database.hostname != "postgres" or _port(database, "DATABASE_URL") != 5432:
            raise StorageModeError("bundled DATABASE_URL must target postgres:5432")
        if qdrant.hostname != "qdrant" or _port(qdrant, "QDRANT_URL") != 6333:
            raise StorageModeError("bundled QDRANT_URL must target qdrant:6333")
        return

    if mode == "external":
        if database.hostname in {"postgres", "qdrant"} or qdrant.hostname in {"postgres", "qdrant"}:
            raise StorageModeError("external storage must not target compose service hosts")
        return

    raise StorageModeError("STORAGE_MODE must be either external or bundled")
