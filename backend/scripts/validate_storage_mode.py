"""Fail before migrations when the selected storage topology is unsupported."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.deployment.storage_mode import StorageModeError, validate_storage_mode


def main() -> int:
    mode = os.environ.get("STORAGE_MODE", "")
    try:
        validate_storage_mode(
            mode,
            os.environ.get("DATABASE_URL"),
            os.environ.get("QDRANT_URL"),
            os.environ.get("POSTGRES_PASSWORD"),
        )
    except StorageModeError as exc:
        print(f"Invalid storage topology: {exc}", file=sys.stderr)
        return 1
    print(f"Storage topology validated: {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
