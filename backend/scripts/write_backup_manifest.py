"""Write a complete, secret-free manifest for a bundled backup directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db.session import get_engine
from docparser.pdf_provider import get_pdf_provider_metadata


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archived_files(data_dir: Path) -> list[dict[str, str]]:
    """Return checksums for uploaded originals and extracted attachments only."""
    uploads_dir = data_dir / "uploads"
    if not uploads_dir.is_dir():
        return []
    result: list[dict[str, str]] = []
    for path in sorted(path for path in uploads_dir.rglob("*") if path.is_file()):
        if path == uploads_dir / ".gitkeep":
            continue
        relative = path.relative_to(data_dir).as_posix()
        result.append({"path": relative, "sha256": sha256_file(path)})
    return result


def schema_revision() -> str | None:
    with get_engine().connect() as connection:
        try:
            value = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        except Exception as exc:
            raise RuntimeError("cannot determine database schema revision") from exc
    return str(value) if value is not None else None


def compose_images(path: Path) -> list[dict]:
    """Read Docker Compose JSON or JSON-lines inventory captured by the host script."""
    if not path.is_file():
        raise RuntimeError("compose image inventory is missing")
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise RuntimeError("compose image inventory is empty")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list) or not parsed or not all(
        isinstance(item, dict) for item in parsed
    ):
        raise RuntimeError("compose image inventory has an invalid format")
    return parsed


def artifact_entries(backup_dir: Path) -> list[dict[str, str]]:
    paths = [
        backup_dir / "postgres.dump",
        backup_dir / "postgres.list",
        backup_dir / "data.tar",
        backup_dir / "totals.json",
        backup_dir / "compose-images.json",
    ]
    paths.extend(sorted(path for path in (backup_dir / "qdrant").glob("*") if path.is_file()))
    return [
        {"path": path.relative_to(backup_dir).as_posix(), "sha256": sha256_file(path)}
        for path in paths
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, required=True)
    args = parser.parse_args()
    backup_dir = args.backup_dir.resolve()
    totals_path = backup_dir / "totals.json"
    totals = json.loads(totals_path.read_text(encoding="utf-8"))
    if not isinstance(totals, dict):
        raise RuntimeError("totals.json must be a JSON object")

    settings = get_settings()
    manifest = {
        "format": 2,
        "collection": settings.qdrant_collection,
        "schema_revision": schema_revision(),
        "pdf_provider": get_pdf_provider_metadata(),
        "compose_images": compose_images(backup_dir / "compose-images.json"),
        "totals": totals,
        "files": archived_files(settings.data_dir),
        "artifacts": artifact_entries(backup_dir),
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
