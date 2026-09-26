# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Пересборка коллекции Qdrant из PostgreSQL (canonical, Этап 2b).

Векторный индекс — производные данные: первоисточник это `okf_concepts` +
`document_chunks` в БД (полный текст), а не `.md`-бандлы. Если коллекция потеряна
(например, после апгрейда Qdrant) — этот скрипт полностью пересобирает её из БД
(обе ветки dual-index: концепты и чанки), доказывая восстановимость индекса.

Запуск (при остановленном сервисе, из каталога backend):
    python scripts/reindex.py

Требует доступный Qdrant и embedding-сервер (или EMBEDDING_PROVIDER=fake).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.services.canonical_reindex import published_document_ids, reindex_published_document
from app.services.embedder import Embedder
from app.services.vector_store import VectorStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Пересобрать коллекцию Qdrant из PostgreSQL.")
    parser.add_argument("--concepts-only", action="store_true", help="Только концепты (без чанков)")
    args = parser.parse_args()

    settings = get_settings()

    vs = VectorStore()
    if vs.client.collection_exists(vs.collection):
        vs.client.delete_collection(vs.collection)
        print(f"Удалена старая коллекция: {vs.collection}")
    vs.ensure_collection()
    print(f"Создана коллекция: {vs.collection}")

    embedder = Embedder()
    stats = {"docs": 0, "concepts": 0, "chunks": 0}
    for doc_id in published_document_ids():
        counts = reindex_published_document(doc_id, settings, vs, embedder, concepts_only=args.concepts_only)
        for key in stats:
            stats[key] += counts[key]
        print(f"[{doc_id}] концептов: {counts['concepts']}, чанков: {counts['chunks']}")

    points = vs.client.count(collection_name=vs.collection, exact=True).count
    print(
        f"Готово: документов {stats['docs']}, концептов {stats['concepts']}, "
        f"чанков {stats['chunks']}, точек в коллекции {points}"
    )


if __name__ == "__main__":
    main()
