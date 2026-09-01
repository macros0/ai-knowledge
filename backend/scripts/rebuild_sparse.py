"""Одноразовая миграция: пересборка sparse-векторов (BM25) концепт-точек.

До 2026-09-01 backfill_sparse на каждом рестарте пересчитывал sparse из
ПОЛНОГО текста .md-бандла (frontmatter + заголовок + тело), тогда как свежая
индексация строила его из title + content (vector_store._sparse_text). Из-за
этого в BM25-индекс попадали теги, source_document и имена файлов, а каждый
рестарт перезаписывал правильные sparse-векторы испорченными.

Скрипт принудительно пересчитывает sparse всех концепт-точек, найденных в
OKF-бандлах, по канонической формуле (force=True backfill_sparse). Dense и
payload не затрагиваются. Повторный запуск безопасен (идемпотентен).

Запуск (требует доступный Qdrant; сервис может быть запущен):
    python scripts/rebuild_sparse.py
    python scripts/rebuild_sparse.py --qdrant-url http://localhost:6333
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.vector_store import VectorStore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Пересборка sparse-векторов концептов по канонической формуле."
    )
    parser.add_argument(
        "--qdrant-url", type=str, default=None, help="URL Qdrant (по умолчанию из .env)"
    )
    args = parser.parse_args()

    vs = VectorStore()
    if args.qdrant_url:
        from qdrant_client import QdrantClient

        vs.client = QdrantClient(url=args.qdrant_url, timeout=10)

    if not vs.client.collection_exists(vs.collection):
        print(f"Коллекция {vs.collection} не найдена — нечего мигрировать.")
        raise SystemExit(1)

    total = vs.backfill_sparse(force=True)
    print(f"Пересчитано sparse-векторов: {total}")


if __name__ == "__main__":
    main()
