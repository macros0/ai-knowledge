"""Миграция payload Qdrant в slim-форму (MIGRATION_PLAN.md §7, §8.6).

Концепт-точки теряют тяжёлые поля, которые теперь canonical хранятся в БД:
content, global_tags, source_document, attachments — удаляются одним bulk-вызовом.

Поле slug (для graph expansion) оставляем как есть: у новых точек index_concepts
пишет stem, у старых может быть filename с .md. Загрузка полного текста после
поиска НЕ зависит от поля slug — она идёт по (doc_id, stem(filepath)) из БД
(app.services.concept_store.enrich_concept_hits). Полную согласованность slug
по всем точкам даёт reindex.py (пересборка из .md-бандлов), либо флаг --fix-slugs
(поточечный set_payload, медленный из-за payload-индекса по slug).

Чанковые точки (point_type="chunk") не трогаются — content остаётся в Qdrant.

Запуск (требует доступный Qdrant; сервис может быть запущен):
    python scripts/migrate_payload.py
    python scripts/migrate_payload.py --qdrant-url http://localhost:6333
    python scripts/migrate_payload.py --fix-slugs    # полное согласование slug

Идемпотентно: повторный запуск не меняет уже slim-точки.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.vector_store import VectorStore

DROPPED_KEYS = ["content", "global_tags", "source_document", "attachments"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Slim-payload миграция концептов в Qdrant.")
    parser.add_argument("--qdrant-url", type=str, default=None, help="URL Qdrant (по умолчанию из .env)")
    parser.add_argument("--dry-run", action="store_true", help="Только посчитать, ничего не менять")
    parser.add_argument("--fix-slugs", action="store_true", help="Дополнительно привести slug к stem (поточечно)")
    args = parser.parse_args()

    vs = VectorStore()
    if args.qdrant_url:
        from qdrant_client import QdrantClient

        vs.client = QdrantClient(url=args.qdrant_url, timeout=10)

    if not vs.client.collection_exists(vs.collection):
        print(f"Коллекция {vs.collection} не найдена — нечего мигрировать.")
        raise SystemExit(1)

    concept_ids: list[str] = []
    slug_updates: list[tuple[str, str]] = []  # (point_id, stem)
    next_offset = None
    while True:
        batch, next_offset = vs.client.scroll(
            collection_name=vs.collection,
            limit=1000,
            with_payload=True,
            with_vectors=False,
            offset=next_offset,
        )
        for rec in batch:
            payload = rec.payload or {}
            if payload.get("point_type") == "chunk":
                continue
            point_id = str(rec.id)
            concept_ids.append(point_id)
            filepath = payload.get("filepath", "")
            stem = Path(filepath).stem if filepath else ""
            if stem and payload.get("slug") != stem:
                slug_updates.append((point_id, stem))
        if next_offset is None:
            break

    if args.dry_run:
        print(f"Dry-run: концепт-точек {len(concept_ids)}, несовпадений slug {len(slug_updates)}, удаляемых ключей {DROPPED_KEYS}")
        return

    if concept_ids:
        vs.client.delete_payload(
            collection_name=vs.collection,
            keys=DROPPED_KEYS,
            points=concept_ids,
        )

    if args.fix_slugs:
        for i, (point_id, stem) in enumerate(slug_updates, start=1):
            vs.client.set_payload(
                collection_name=vs.collection,
                payload={"slug": stem},
                points=[point_id],
            )
            if i % 100 == 0:
                print(f"  slug обновлено: {i}/{len(slug_updates)}", flush=True)

    print(f"Готово: концепт-точек {len(concept_ids)}, удалены ключи {DROPPED_KEYS}, "
          f"несовпадений slug {len(slug_updates)}{' (исправлены)' if args.fix_slugs else ''}")


if __name__ == "__main__":
    main()
