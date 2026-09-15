"""Create and download a checksum-protected snapshot of the configured collection."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import httpx
from qdrant_client import QdrantClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings


def _safe_name(name: str) -> str:
    if Path(name).name != name or not name:
        raise RuntimeError("Qdrant returned an unsafe snapshot name")
    return name


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_snapshot(output_dir: Path) -> Path:
    settings = get_settings()
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    snapshot = client.create_snapshot(settings.qdrant_collection, wait=True)
    if snapshot is None:
        raise RuntimeError("Qdrant did not return a snapshot")
    name = _safe_name(snapshot.name)
    target = output_dir / name
    url = f"{settings.qdrant_url.rstrip('/')}/collections/{settings.qdrant_collection}/snapshots/{name}"
    headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
    with httpx.stream("GET", url, headers=headers, timeout=60) as response:
        response.raise_for_status()
        with target.open("xb") as destination:
            for block in response.iter_bytes():
                destination.write(block)
    target.with_suffix(target.suffix + ".sha256").write_text(
        sha256_file(target) + "  " + target.name + "\n", encoding="utf-8"
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        path = create_snapshot(args.output_dir)
    except Exception as exc:
        print(f"Qdrant snapshot failed: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
