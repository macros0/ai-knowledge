# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Пересборка коллекции Qdrant v2 из PostgreSQL (Этап 2b, Фаза 4).

Строит НОВУЮ коллекцию `okf_knowledge_base_v2` из canonical-БД (okf_concepts +
document_chunks) с логическими point_id (uuid5("okf:concept:{doc_id}:{slug}") /
uuid5("okf:chunk:{doc_id}:{chunk_index}")) и slim payload (без content/filepath/
section_title). Это одновременно:
  - переключение формулы point_id (отвязка от абсолютного пути data_dir);
  - доказательство восстановимости индекса целиком из PostgreSQL.

Старая коллекция НЕ трогается — она остаётся рабочей до переключения
QDRANT_COLLECTION и удаляется только после приёмки (Фаза 5).

Выполняйте build/parity при остановленных writers приложения.

Порядок:
  1. python scripts/rebuild_qdrant_v2.py            # build v2
  2. python scripts/rebuild_qdrant_v2.py --parity   # сверка точек vs старая
  3. переключить QDRANT_COLLECTION=okf_knowledge_base_v2 + рестарт backend
  4. (после приёмки) удалить старую коллекцию вручную

Требует доступный Qdrant и embedding-сервер (или EMBEDDING_PROVIDER=fake).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db.session import session_scope
from app.services.canonical_reindex import published_document_ids, reindex_published_document
from app.services.embedder import Embedder
from app.services.generation_store import lock_generation_read
from app.services.vector_store import VectorStore

V2_COLLECTION = "okf_knowledge_base_v2"


def build_v2(concepts_only: bool = False) -> dict:
    settings = get_settings()
    if settings.qdrant_collection == V2_COLLECTION:
        raise ValueError("Refusing to delete the active collection; use reindex.py for an offline active-index rebuild")

    vs = VectorStore()
    vs.settings = settings.model_copy(update={"qdrant_collection": V2_COLLECTION})
    if vs.client.collection_exists(V2_COLLECTION):
        vs.client.delete_collection(V2_COLLECTION)
        print(f"Удалена существующая коллекция: {V2_COLLECTION}")
    vs.ensure_collection()
    print(f"Создана коллекция: {V2_COLLECTION}")

    embedder = Embedder()
    stats = {"docs": 0, "concepts": 0, "chunks": 0}

    for doc_id in published_document_ids():
        counts = reindex_published_document(doc_id, settings, vs, embedder, concepts_only=concepts_only)
        for key in stats:
            stats[key] += counts[key]
        print(f"[{doc_id}] концептов: {counts['concepts']}, чанков: {counts['chunks']}")

    points = vs.client.count(collection_name=V2_COLLECTION, exact=True).count
    stats["points"] = points
    print(
        f"Готово v2: документов {stats['docs']}, концептов {stats['concepts']}, "
        f"чанков {stats['chunks']}, точек {points}"
    )
    return stats


def parity() -> None:
    """Compare published point identities and source links, ignoring old versions."""
    settings = get_settings()
    old_name = settings.qdrant_collection
    old = VectorStore()

    from qdrant_client.http import models as qm

    def identities(collection: str, doc_id: str, generation_id: str | None) -> set[tuple]:
        generation = (
            qm.IsEmptyCondition(is_empty=qm.PayloadField(key="generation_id"))
            if generation_id is None else
            qm.FieldCondition(key="generation_id", match=qm.MatchValue(value=generation_id))
        )
        flt = qm.Filter(
            must=[
                qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
                generation,
            ]
        )
        found = set()
        offset = None
        while True:
            points, offset = old.client.scroll(
                collection_name=collection, scroll_filter=flt, offset=offset, limit=256,
                with_payload=["point_type", "slug", "chunk_index", "source_id"], with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                found.add((str(point.id), *(payload.get(key) for key in
                                          ("point_type", "slug", "chunk_index", "source_id"))))
            if offset is None:
                return found

    mismatches = 0
    for doc_id in published_document_ids():
        with session_scope() as session:
            generation_id = lock_generation_read(session, [doc_id]).get(doc_id)
            a = identities(old_name, doc_id, generation_id)
            b = identities(V2_COLLECTION, doc_id, generation_id)
            if a != b:
                mismatches += 1
                print(f"[{doc_id}] опубликованные точки: старая={len(a)} v2={len(b)}, отличаются ID/источники")
    if mismatches:
        print(f"\nРасхождений: {mismatches}")
        sys.exit(1)
    print("\nПаритет: ID опубликованных точек и ссылки на источники совпадают.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Пересборка Qdrant v2 из PostgreSQL (Фаза 4).")
    parser.add_argument("--concepts-only", action="store_true", help="Только концепты (без чанков)")
    parser.add_argument("--parity", action="store_true", help="Сверка точек со старой коллекцией")
    args = parser.parse_args()

    if args.parity:
        parity()
    else:
        build_v2(concepts_only=args.concepts_only)


if __name__ == "__main__":
    main()
