"""Restore the configured Qdrant collection from a verified local snapshot."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from qdrant_client import QdrantClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from create_qdrant_snapshot import sha256_file

from app.config import get_settings


def _expected_checksum(snapshot_file: Path) -> str:
    sidecar = snapshot_file.with_suffix(snapshot_file.suffix + ".sha256")
    fields = sidecar.read_text(encoding="utf-8").strip().split()
    if len(fields) != 2 or fields[1] != snapshot_file.name:
        raise RuntimeError("invalid snapshot checksum sidecar")
    return fields[0]


def restore_snapshot(snapshot_file: Path, *, timeout_seconds: float = 60) -> None:
    if not snapshot_file.is_file():
        raise RuntimeError("snapshot file does not exist")
    if sha256_file(snapshot_file) != _expected_checksum(snapshot_file):
        raise RuntimeError("snapshot checksum mismatch")

    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    with snapshot_file.open("rb") as source:
        client.http.snapshots_api.recover_from_uploaded_snapshot(
            collection_name=settings.qdrant_collection,
            snapshot=source,
            wait=True,
        )
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            client.get_collection(settings.qdrant_collection)
            return
        except Exception as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError("Qdrant collection did not become readable after restore") from exc
            time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        restore_snapshot(args.snapshot_file)
    except Exception as exc:
        parser.exit(1, f"Qdrant restore failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
