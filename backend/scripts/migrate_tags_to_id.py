# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Одноразовый перенос tags.name → tags.id на dev-БД (обход Alembic-квирка).

На dev-Postgres Alembic отстаёт от create_all (alembic_version=f0a1b2c3d4e5f,
а таблицы locales/stopwords уже созданы create_all) — `alembic upgrade head`
упадёт с DuplicateTable. Поэтому миграция тегов выполняется этим скриптом
напрямую (идемпотентно), а Alembic-миграция 8c3d4e5f6a7b — для prod/чистой БД.

Семантика идентична миграции 8c3d4e5f6a7b:
  - tags.name (PK) → tags.id + canonical_text(unique) + canonical_locale + ...
  - document_tags.tag → document_tags.tag_id (FK)
  - tag_translations (пусто) / development_translations (сид ru) /
    attribute_value_translations (сид ru)
Qdrant payload и okf_concepts.tags НЕ трогаются (канонический текст).

Запуск: python scripts/migrate_tags_to_id.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text

from app.db.session import get_engine


def main() -> None:
    engine = get_engine()
    insp = inspect(engine)
    tags_cols = {c["name"] for c in insp.get_columns("tags")}
    if "canonical_text" in tags_cols:
        print("Уже мигрировано (tags.canonical_text существует) — no-op.")
        return

    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE tags_new ("
                " id SERIAL PRIMARY KEY,"
                " canonical_text VARCHAR(255) NOT NULL UNIQUE,"
                " canonical_locale VARCHAR(16) NOT NULL,"
                " merged_into_id INTEGER NULL,"
                " created_by VARCHAR(255) NULL,"
                " created_at TIMESTAMPTZ NOT NULL,"
                " deleted_at TIMESTAMPTZ NULL"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO tags_new (canonical_text, canonical_locale, created_at) "
                "SELECT name, 'ru', :now FROM tags"
            ),
            {"now": now},
        )
        conn.execute(text("DROP TABLE tags"))
        conn.execute(text("ALTER TABLE tags_new RENAME TO tags"))
        conn.execute(
            text(
                "ALTER TABLE tags ADD CONSTRAINT fk_tags_merged_into "
                "FOREIGN KEY (merged_into_id) REFERENCES tags(id) ON DELETE SET NULL"
            )
        )
        # Орфан-теги: document_tags.tag, отсутствующий в пуле, получает строку tags.
        conn.execute(
            text(
                "INSERT INTO tags (canonical_text, canonical_locale, created_at) "
                "SELECT DISTINCT dt.tag, 'ru', :now FROM document_tags dt "
                "WHERE NOT EXISTS (SELECT 1 FROM tags t WHERE t.canonical_text = dt.tag)"
            ),
            {"now": now},
        )

        conn.execute(
            text(
                "CREATE TABLE tag_translations ("
                " tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,"
                " locale VARCHAR(16) NOT NULL,"
                " text VARCHAR(255) NOT NULL,"
                " is_machine_translated BOOLEAN NOT NULL DEFAULT FALSE,"
                " reviewed_by VARCHAR(255) NULL,"
                " translated_at TIMESTAMPTZ NULL,"
                " PRIMARY KEY (tag_id, locale)"
                ")"
            )
        )

        conn.execute(
            text(
                "CREATE TABLE document_tags_new ("
                " doc_id VARCHAR(16) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,"
                " tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,"
                " PRIMARY KEY (doc_id, tag_id)"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO document_tags_new (doc_id, tag_id) "
                "SELECT dt.doc_id, t.id FROM document_tags dt "
                "JOIN tags t ON t.canonical_text = dt.tag"
            )
        )
        conn.execute(text("DROP TABLE document_tags"))
        conn.execute(text("ALTER TABLE document_tags_new RENAME TO document_tags"))

        conn.execute(
            text(
                "CREATE TABLE development_translations ("
                " development_id INTEGER NOT NULL REFERENCES developments(id) ON DELETE CASCADE,"
                " locale VARCHAR(16) NOT NULL,"
                " name VARCHAR(255) NOT NULL,"
                " is_machine_translated BOOLEAN NOT NULL DEFAULT FALSE,"
                " reviewed_by VARCHAR(255) NULL,"
                " translated_at TIMESTAMPTZ NULL,"
                " PRIMARY KEY (development_id, locale)"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO development_translations (development_id, locale, name, is_machine_translated) "
                "SELECT id, 'ru', name, FALSE FROM developments"
            )
        )

        conn.execute(
            text(
                "CREATE TABLE attribute_value_translations ("
                " attribute_value_id INTEGER NOT NULL REFERENCES attribute_values(id) ON DELETE CASCADE,"
                " locale VARCHAR(16) NOT NULL,"
                " label VARCHAR(255) NOT NULL,"
                " is_machine_translated BOOLEAN NOT NULL DEFAULT FALSE,"
                " reviewed_by VARCHAR(255) NULL,"
                " translated_at TIMESTAMPTZ NULL,"
                " PRIMARY KEY (attribute_value_id, locale)"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO attribute_value_translations (attribute_value_id, locale, label, is_machine_translated) "
                "SELECT id, 'ru', label, FALSE FROM attribute_values WHERE label IS NOT NULL"
            )
        )

    n_tags = engine.connect().execute(text("SELECT COUNT(*) FROM tags")).scalar()
    n_dt = engine.connect().execute(text("SELECT COUNT(*) FROM document_tags")).scalar()
    print(f"Миграция завершена: tags={n_tags}, document_tags={n_dt}")


if __name__ == "__main__":
    main()
