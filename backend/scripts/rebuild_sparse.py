"""Миграция/восстановление: пересборка sparse-векторов (BM25) по БД.

Задачи:
  1. До 2026-09-01 backfill_sparse на каждом рестарте пересчитывал sparse из
     ПОЛНОГО текста .md-бандла (frontmatter + заголовок + тело), тогда как свежая
     индексация строила его из title + content (vector_store._sparse_text). Из-за
     этого в BM25-индекс попадали теги, source_document и имена файлов.
  2. С 2026-09-08 (немецкие ä/ö/ü/ß в TOKEN_RE) — пересборка sparse ПОСЛЕ СМЕНЫ
     ИНДЕКСНОЙ ФОРМУЛЫ: force-прогон обязателен после расширения алфавита
     токенайзера (sparse.TOKEN_EXTRA_LETTERS).

Скрипт принудительно (force=True) пересчитывает sparse КОНЦЕПТОВ И ЧАНКОВ по
канонической формуле из БД (концепт: title+content[:okf_max_concept_chars];
чанк: section_title+content[:okf_max_chunk_index_chars]). Dense и payload не
затрагиваются, эмбеддинги не вызываются. Повторный запуск безопасен
(идемпотентен).

Запуск (требует доступный Qdrant и БД; сервис может быть запущен):
    python scripts/rebuild_sparse.py
    python scripts/rebuild_sparse.py --qdrant-url http://localhost:16333
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.vector_store import VectorStore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Пересборка sparse-векторов концептов и чанков по канонической формуле."
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

    total = vs.backfill_sparse(force=True, include_chunks=True)
    print(f"Пересчитано sparse-векторов (концепты+чанки): {total}")


if __name__ == "__main__":
    main()
