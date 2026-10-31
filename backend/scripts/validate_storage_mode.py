"""Fail before migrations when the selected storage topology is unsupported."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.deployment.storage_mode import StorageModeError, validate_storage_mode
from app.deployment.replica_profile import ReplicaProfile, ReplicaProfileError, validate_runtime_profile


def main() -> int:
    mode = os.environ.get("STORAGE_MODE", "")
    try:
        schema = os.environ.get("OKF_DEPLOYMENT_SCHEMA")
        profile = ReplicaProfile(
            role=os.environ.get("APP_ROLE", "combined"),
            artifact_backend=os.environ.get("ARTIFACT_BACKEND", "local"),
            postgres_provider=os.environ.get("POSTGRES_PROVIDER", "managed"),
            qdrant_provider=os.environ.get("QDRANT_PROVIDER", "managed"),
            artifact_provider=os.environ.get("ARTIFACT_PROVIDER", "managed"),
        )
        validate_runtime_profile(schema, profile)
        validate_storage_mode(
            mode,
            os.environ.get("DATABASE_URL"),
            os.environ.get("QDRANT_URL"),
            os.environ.get("POSTGRES_PASSWORD"),
        )
    except (StorageModeError, ReplicaProfileError) as exc:
        print(f"Invalid storage topology: {exc}", file=sys.stderr)
        return 1
    print(f"Storage topology validated: {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
