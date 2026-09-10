# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Пересборка коллекции Qdrant v2 из PostgreSQL (Этап 2b, Фаза 4).

Строит НОВУЮ коллекцию `okf_knowledge_base_v2` из canonical-БД (okf_concepts +
document_chunks) с логическими point_id (uuid5("okf:concept:{doc_id}:{slug}") /
uuid5("okf:chunk:{doc_id}:{chunk_index}")) и slim payload (без content/filepath/
section_title). Это одновременно:
  - переключение формулы point_id (отвязка от абсолютного пути data_dir);
  - доказательство восстановимости индекса целиком из PostgreSQL.

Старая коллекция НЕ трогается — она остаётся рабочей до переключения
QDRANT_COLLECTION и удаляется только после приёмки (Фаза 5).

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

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Document, DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.models.schemas import OkfDocument
from app.services.development_registry import get_development_registry
from app.services.embedder import Embedder
from app.services.vector_store import VectorStore

V2_COLLECTION = "okf_knowledge_base_v2"


def _iter_done_docs() -> list[tuple[str, int | None, str]]:
    with session_scope() as s:
        rows = s.execute(
            select(Document.id, Document.development_id, Document.filename).where(
                Document.deleted_at.is_(None), Document.status == "done"
            )
        ).all()
    return [(r.id, r.development_id, r.filename) for r in rows]


def _target_collection(settings, name: str) -> None:
    """Направляет VectorStore на указанную коллекцию (мутация settings)."""
    settings.qdrant_collection = name


def build_v2(concepts_only: bool = False) -> dict:
    settings = get_settings()
    dev_reg = get_development_registry()
    _target_collection(settings, V2_COLLECTION)

    vs = VectorStore()
    if vs.client.collection_exists(V2_COLLECTION):
        vs.client.delete_collection(V2_COLLECTION)
        print(f"Удалена существующая коллекция: {V2_COLLECTION}")
    vs.ensure_collection()
    print(f"Создана коллекция: {V2_COLLECTION}")

    embedder = Embedder()
    okf_dir = settings.okf_dir
    stats = {"docs": 0, "concepts": 0, "chunks": 0}

    for doc_id, dev_id, filename in _iter_done_docs():
        dev_tags = dev_reg.dev_tags(dev_id) if dev_id else []

        with session_scope() as s:
            doc = s.get(Document, doc_id)
            global_tags = [t.tag for t in (doc.tags_rel if doc else [])]
            source_locale = doc.source_locale if doc else None
            concept_rows = s.query(OkfConcept).filter(OkfConcept.doc_id == doc_id).all()
            chunk_rows = (
                s.query(DocumentChunk)
                .filter(DocumentChunk.doc_id == doc_id)
                .order_by(DocumentChunk.chunk_index)
                .all()
            )

        if concept_rows:
            okf_docs = [
                OkfDocument(
                    filepath=str(okf_dir / doc_id / f"{c.slug}.md"),
                    metadata={
                        "title": c.title,
                        "type": c.type,
                        "tags": list(c.tags or []),
                        "relations": list(c.relations or []),
                        "chunk_index": c.chunk_index,
                    },
                    content=c.content or "",
                    markdown="",
                )
                for c in concept_rows
            ]
            # Единая формула dense-эмбеддинга концепта (как в pipeline._finalize):
            # title + "\n" + content[:okf_max_concept_chars] — иначе rebuild из БД
            # дал бы другой вектор, чем свежая индексация (title содержит коды
            # разделов, которые иначе не попадают в вектор).
            cap = settings.okf_max_concept_chars
            vectors = embedder.embed_texts(
                [f"{d.metadata.get('title', '')}\n{d.content[:cap]}" for d in okf_docs]
            )
            vs.index_concepts(doc_id, okf_docs, vectors, dev_tags=dev_tags, source_locale=source_locale)
            stats["concepts"] += len(okf_docs)
            print(f"[{doc_id}] концептов: {len(okf_docs)}")
        else:
            print(f"[{doc_id}] концептов нет — пропуск")

        if not concepts_only and chunk_rows and settings.search_index_chunks_enabled:
            chunk_texts = [c.content or "" for c in chunk_rows]
            section_titles = [c.section_title or "" for c in chunk_rows]
            cap = settings.okf_max_chunk_index_chars
            embed_inputs = [
                f"{st}\n{t[:cap]}" if st else t[:cap]
                for st, t in zip(section_titles, chunk_texts)
            ]
            chunk_vectors = embedder.embed_texts(embed_inputs)
            vs.index_chunks(
                doc_id, filename, chunk_texts, global_tags, chunk_vectors,
                section_titles=section_titles, dev_tags=dev_tags,
                source_locale=source_locale,
            )
            stats["chunks"] += len(chunk_rows)
            print(f"[{doc_id}] чанков: {len(chunk_rows)}")

        stats["docs"] += 1

    points = vs.client.count(collection_name=V2_COLLECTION, exact=True).count
    stats["points"] = points
    print(
        f"Готово v2: документов {stats['docs']}, концептов {stats['concepts']}, "
        f"чанков {stats['chunks']}, точек {points}"
    )
    return stats


def parity() -> None:
    """Сверка числа точек per-doc между старой коллекцией и v2."""
    settings = get_settings()
    old_name = settings.qdrant_collection
    old = VectorStore()

    from qdrant_client.http import models as qm

    def count(collection: str, doc_id: str, pt: str) -> int:
        flt = qm.Filter(
            must=[
                qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
                qm.FieldCondition(key="point_type", match=qm.MatchValue(value=pt)),
            ]
        )
        return old.client.count(collection_name=collection, count_filter=flt, exact=True).count

    mismatches = 0
    for doc_id, _, _ in _iter_done_docs():
        for pt in ("concept", "chunk"):
            a = count(old_name, doc_id, pt)
            b = count(V2_COLLECTION, doc_id, pt)
            if a != b:
                mismatches += 1
                print(f"[{doc_id}] {pt}: старая={a} v2={b}  <-- РАСХОЖДЕНИЕ")
    if mismatches:
        print(f"\nРасхождений: {mismatches}")
        sys.exit(1)
    print("\nПаритет: число точек per-doc совпадает со старой коллекцией.")


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
