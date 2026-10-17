"""Migrate a stopped Kubernetes installation and persist its vector profile."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def check_profile(marker: Path, desired: dict, *, nonempty: bool) -> None:
    if marker.exists():
        if json.loads(marker.read_text(encoding="utf-8")) != desired:
            raise RuntimeError("Vector profile differs; use new storage and reindex explicitly")
    elif nonempty:
        raise RuntimeError("Refusing unmarked existing corpus; explicit migration is required")


def has_existing_files(root: Path) -> bool:
    return any((p.is_file() or p.is_symlink()) and p.relative_to(root).parts[0] != "lost+found"
               and p.name != ".kubernetes-model-profile.json" for p in root.rglob("*"))


def profile_for(settings) -> dict:
    return {"mode": settings.model_mode, "dimensions": settings.embedding_dimensions,
            "provider": settings.embedding_provider, "model": settings.embedding_model,
            "collection": settings.qdrant_collection}


def main() -> int:
    from app.config import get_settings
    from qdrant_client import QdrantClient

    settings = get_settings()
    if settings.model_mode == "unavailable":
        raise RuntimeError("Use the saved live/stub profile for maintenance, not unavailable")
    marker = settings.data_dir / ".kubernetes-model-profile.json"
    desired = profile_for(settings)
    vectors = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    nonempty = bool(vectors.get_collections().collections)
    # A DB-only or file-only reused volume must also be rejected before migration.
    from sqlalchemy import inspect, text
    from app.db.session import get_engine
    engine = get_engine()
    if "documents" in inspect(engine).get_table_names():
        with engine.connect() as connection:
            nonempty = nonempty or bool(connection.execute(text("SELECT count(*) FROM documents")).scalar())
    nonempty = nonempty or has_existing_files(settings.data_dir)
    check_profile(marker, desired, nonempty=nonempty)
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    marker.write_text(json.dumps(desired) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
