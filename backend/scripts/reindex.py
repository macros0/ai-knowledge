# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

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

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Document, DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.models.schemas import OkfDocument
from app.services.development_registry import get_development_registry
from app.services.embedder import Embedder
from app.services.vector_store import VectorStore


def _iter_done_docs() -> list[tuple[str, int | None, str]]:
    """(doc_id, development_id, filename) активных done-документов."""
    with session_scope() as s:
        rows = s.execute(
            select(Document.id, Document.development_id, Document.filename).where(
                Document.deleted_at.is_(None), Document.status == "done"
            )
        ).all()
    return [(r.id, r.development_id, r.filename) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="Пересобрать коллекцию Qdrant из PostgreSQL.")
    parser.add_argument("--concepts-only", action="store_true", help="Только концепты (без чанков)")
    args = parser.parse_args()

    settings = get_settings()
    dev_reg = get_development_registry()

    vs = VectorStore()
    if vs.client.collection_exists(vs.collection):
        vs.client.delete_collection(vs.collection)
        print(f"Удалена старая коллекция: {vs.collection}")
    vs.ensure_collection()
    print(f"Создана коллекция: {vs.collection}")

    embedder = Embedder()
    okf_dir = settings.okf_dir
    total_concepts = 0
    total_chunks = 0
    for doc_id, dev_id, filename in _iter_done_docs():
        dev_tags = dev_reg.dev_tags(dev_id) if dev_id else []

        with session_scope() as s:
            concept_rows = s.query(OkfConcept).filter(OkfConcept.doc_id == doc_id).all()
            chunk_rows = (
                s.query(DocumentChunk)
                .filter(DocumentChunk.doc_id == doc_id)
                .order_by(DocumentChunk.chunk_index)
                .all()
            )
            doc = s.get(Document, doc_id)
            global_tags = [t.tag for t in (doc.tags_rel if doc else [])]
            source_locale = doc.source_locale if doc else None

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
            # title + "\n" + content[:okf_max_concept_chars] — иначе reindex из БД
            # дал бы другой вектор, чем свежая индексация.
            cap = settings.okf_max_concept_chars
            vectors = embedder.embed_texts(
                [f"{d.metadata.get('title', '')}\n{d.content[:cap]}" for d in okf_docs]
            )
            vs.index_concepts(doc_id, okf_docs, vectors, dev_tags=dev_tags, source_locale=source_locale)
            total_concepts += len(okf_docs)
            print(f"[{doc_id}] индексировано концептов: {len(okf_docs)}")
        else:
            print(f"[{doc_id}] пропущены концепты: okf_concepts пусто")

        if not args.concepts_only and chunk_rows and settings.search_index_chunks_enabled:
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
            total_chunks += len(chunk_rows)
            print(f"[{doc_id}] индексировано чанков: {len(chunk_rows)}")

    points = vs.client.count(collection_name=vs.collection, exact=True).count
    print(
        f"Готово: документов {len(_iter_done_docs())}, концептов {total_concepts}, "
        f"чанков {total_chunks}, точек в коллекции {points}"
    )


if __name__ == "__main__":
    main()
