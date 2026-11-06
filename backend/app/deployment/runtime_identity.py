"""Keyed common configuration identity; local paths and role are not shared state."""
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
import hmac
import json
from pathlib import Path
import importlib.metadata
import importlib.util
import platform
import re

SCHEMA_REVISION = "rpl05_maintenance_epoch"
LOCAL_FIELDS = {"app_role", "runtime_identity_file", "data_dir", "diagnostics_dir", "artifact_scratch_root"}
IDENTITY_KEYS = {"version", "deployment_schema", "installation_id", "artifact_backend", "artifact_store_id",
    "storage_receipt_digest", "runtime_fingerprint"}


@dataclass(frozen=True)
class RuntimeIdentity:
    installation_id: str
    fingerprint: str
    storage_receipt_digest: str


@lru_cache(maxsize=1)
def build_fingerprint():
    digest = sha256()
    roots = {"app": Path(__file__).resolve().parents[1]}
    parser = importlib.util.find_spec("docparser")
    if parser is not None and parser.origin:
        roots["docparser"] = Path(parser.origin).parent
    for name, root in sorted(roots.items()):
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".md", ".json", ".yaml", ".yml", ".txt"}:
                digest.update((name + "/" + path.relative_to(root).as_posix()).encode() + b"\0")
                digest.update(sha256(path.read_bytes()).digest())
    packages = sorted((dist.metadata.get("Name", "").lower(), dist.version) for dist in importlib.metadata.distributions())
    digest.update(json.dumps({"python": platform.python_version(), "packages": packages}, sort_keys=True).encode())
    return digest.hexdigest()


def _fingerprint(settings, envelope):
    configuration = settings.model_dump(mode="json", exclude=LOCAL_FIELDS)
    # Fields excluded from public Settings serialization still belong to the
    # common identity. Only the keyed digest leaves this function.
    for name, field in type(settings).model_fields.items():
        if field.exclude is True and name not in LOCAL_FIELDS:
            value = getattr(settings, name)
            configuration[name] = value.get_secret_value() if hasattr(value, "get_secret_value") else value
    trust = {}
    for name in ("runtime_trust_file", "artifact_s3_ca_bundle"):
        path = getattr(settings, name, None)
        if path is not None:
            with Path(path).open("rb") as stream:
                certificates = stream.read(4 * 1024 * 1024 + 1)
            if not certificates or len(certificates) > 4 * 1024 * 1024 or b"PRIVATE KEY" in certificates:
                raise ValueError("Runtime trust file is invalid")
            trust[name] = sha256(certificates).hexdigest()
    raw = json.dumps({"configuration": configuration, "identity": envelope, "build": build_fingerprint(), "trust": trust},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str).encode()
    return hmac.new(settings.app_secret_key.encode(), raw, sha256).hexdigest()


def identity_document(settings, *, installation_id, storage_receipt_digest):
    if (not isinstance(installation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", installation_id)
            or not isinstance(storage_receipt_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", storage_receipt_digest)
            or settings.artifact_backend not in {"rwx", "s3"}
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", settings.artifact_store_id)):
        raise ValueError("Invalid runtime identity inputs")
    envelope = {"version": 1, "deployment_schema": "replicas-v1", "installation_id": installation_id,
        "artifact_backend": settings.artifact_backend, "artifact_store_id": settings.artifact_store_id,
        "storage_receipt_digest": storage_receipt_digest}
    return {**envelope, "runtime_fingerprint": _fingerprint(settings, envelope)}


def validate_identity(settings):
    path = settings.runtime_identity_file
    if path is None:
        raise ValueError("Replica runtime identity file is required")
    try:
        # Kubernetes projects ConfigMaps through operator-owned symlinks. Read a
        # bounded, stable snapshot instead of rejecting that supported transport.
        with Path(path).open("rb") as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError()
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != IDENTITY_KEYS or type(value["version"]) is not int or value["version"] != 1:
            raise ValueError()
        if (value["deployment_schema"] != "replicas-v1" or value["artifact_backend"] != settings.artifact_backend
                or value["artifact_store_id"] != settings.artifact_store_id):
            raise ValueError()
        expected = identity_document(settings, installation_id=value["installation_id"],
            storage_receipt_digest=value["storage_receipt_digest"])
        supplied = value["runtime_fingerprint"]
        if not isinstance(supplied, str) or not re.fullmatch(r"[0-9a-f]{64}", supplied) or not hmac.compare_digest(supplied, expected["runtime_fingerprint"]):
            raise ValueError()
        return RuntimeIdentity(value["installation_id"], supplied, value["storage_receipt_digest"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("Replica runtime identity does not match this configuration/build") from None


def enforce_session_identity(session, settings, *, require_installed=False):
    """Use Core reads: this guard also runs before ORM writes and commits."""
    from sqlalchemy import inspect, select
    from app.db.models import ArtifactStorageControl, ReplicaRuntimeIdentity
    from app.services.artifacts.control import descriptor

    connection = session.connection()
    identity = settings._runtime_identity
    table = ReplicaRuntimeIdentity.__table__
    row = (connection.execute(select(table)).mappings().one_or_none()
           if inspect(connection).has_table(table.name) else None)
    if row is None:
        if require_installed or identity is not None:
            raise ValueError("Replica runtime identity must be installed before startup")
        return None
    if (identity is None or row["installation_id"] != identity.installation_id
            or not hmac.compare_digest(row["fingerprint"], identity.fingerprint)
            or row["storage_receipt_digest"] != identity.storage_receipt_digest
            or row["schema_revision"] != SCHEMA_REVISION):
        raise ValueError("Replica runtime identity differs from the installed identity")
    control = connection.execute(select(ArtifactStorageControl.__table__)).mappings().one_or_none()
    if (control is None or control["installation_id"] != identity.installation_id
            or control["active_store_id"] != settings.artifact_store_id
            or control["active_descriptor"] != descriptor(settings)):
        raise ValueError("Replica runtime identity differs from the installed storage")
    return identity


def enforce_runtime_identity(settings):
    """Entrypoints never bootstrap, create tables, or accept an unstamped DB."""
    from sqlalchemy import inspect, text
    from app.db.session import get_session_factory

    # Re-read mounted identity on every startup; no caller can skip validation by
    # injecting a Settings instance that was mutated after model validation.
    settings._runtime_identity = validate_identity(settings)
    with get_session_factory()() as session:
        connection = session.connection()
        if (not inspect(connection).has_table("alembic_version")
                or connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all() != [SCHEMA_REVISION]):
            raise ValueError("Replica database schema requires an explicit migration")
        identity = enforce_session_identity(session, settings, require_installed=True)
        from app.services.work.maintenance_control import bind_runtime_epoch
        bind_runtime_epoch(session, settings)
        from app.services.artifacts.control import enforce_session
        enforce_session(session)
        return identity
