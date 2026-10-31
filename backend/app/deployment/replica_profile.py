"""Structural replica profiles, separate from the shipped runtime capability."""
from dataclasses import dataclass
import re


class ReplicaProfileError(ValueError):
    """Unsupported or unsafe deployment profile (messages contain no secrets)."""


@dataclass(frozen=True)
class ReplicaProfile:
    mode: str = "compact"
    role: str = "combined"
    artifact_backend: str = "local"
    postgres_provider: str = "managed"
    qdrant_provider: str = "managed"
    artifact_provider: str = "managed"
    database_dialect: str = "sqlite"
    shared_artifacts_verified: bool = False
    storage_certification: str | None = None
    resilience: str = "basic"
    frontend_replicas: int = 1
    api_replicas: int = 1
    worker_replicas: int = 0


def validate_replica_profile(profile: ReplicaProfile) -> None:
    enums = {
        "mode": {"compact", "frontend-split", "distributed"},
        "role": {"combined", "api", "worker"},
        "artifact_backend": {"local", "rwx", "s3"},
        "postgres_provider": {"managed", "external"},
        "qdrant_provider": {"managed", "external"},
        "artifact_provider": {"managed", "external"},
        "resilience": {"basic", "resilient"},
        "database_dialect": {"sqlite", "postgresql"},
    }
    for field, allowed in enums.items():
        value = getattr(profile, field)
        if not isinstance(value, str) or value not in allowed:
            raise ReplicaProfileError(f"Invalid {field}")
    for field, minimum in (("frontend_replicas", 1), ("api_replicas", 1), ("worker_replicas", 0)):
        value = getattr(profile, field)
        if type(value) is not int or value < minimum:
            raise ReplicaProfileError(f"Invalid {field}")
    if type(profile.shared_artifacts_verified) is not bool:
        raise ReplicaProfileError("Invalid shared artifact verification")
    if profile.mode != "distributed":
        if profile.role != "combined" or profile.api_replicas != 1 or profile.worker_replicas:
            raise ReplicaProfileError("Compact/frontend-split requires one combined backend")
        if profile.mode == "compact" and profile.frontend_replicas != 1:
            raise ReplicaProfileError("Compact requires one frontend")
    if profile.mode == "distributed" or profile.role != "combined":
        if profile.database_dialect != "postgresql":
            raise ReplicaProfileError("Distributed execution requires PostgreSQL")
        if profile.artifact_backend == "local" or not profile.shared_artifacts_verified:
            raise ReplicaProfileError("Distributed execution requires verified shared artifacts")
        if profile.role == "combined":
            raise ReplicaProfileError("Distributed execution requires an explicit API/worker role")
    if profile.resilience == "resilient":
        # Syntax only. Runtime must also verify topology/provider/version identity.
        if not isinstance(profile.storage_certification, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", profile.storage_certification,
        ):
            raise ReplicaProfileError("Resilient profile requires storage certification")


def validate_runtime_profile(schema: str | None, profile: ReplicaProfile) -> None:
    if schema is None:
        if profile != ReplicaProfile():
            raise ReplicaProfileError("Replica settings require explicit deployment schema")
        return
    if schema != "replicas-v1":
        raise ReplicaProfileError("Unsupported deployment schema")
    validate_replica_profile(profile)
    # Build-controlled gate: no environment variable can assert capability.
    raise ReplicaProfileError("replicas-v1 runtime is not implemented in this build")
