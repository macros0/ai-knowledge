"""Глобальный справочник тегов на реляционной БД (замена data/tags.json).

`tags` — таблица известных имён тегов (для автодополнения); счётчик использования
вычисляется на чтение агрегатом по `document_tags` (каноническая связь документ→тег,
которую поддерживает DocumentRegistry). Хранить денормализованный count не нужно —
нет триггера и дрейфа между «именем тега» и «числом документов с тегом».
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.db.models import DocumentTag, Tag
from app.db.session import session_scope


def normalize_tags(tags: list[str] | None) -> list[str]:
    """Обрезка пробелов, отсев пустых и дубликатов (порядок сохранён)."""
    if not tags:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for t in tags:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return result


class TagRegistry:
    def add(self, tags: list[str] | None) -> None:
        """Регистрирует имена тегов в общем пуле (upsert, без счётчика)."""
        normalized = normalize_tags(tags)
        if not normalized:
            return
        with session_scope() as s:
            existing = {
                name
                for (name,) in s.execute(
                    select(Tag.name).where(Tag.name.in_(normalized))
                ).all()
            }
            for name in normalized:
                if name not in existing:
                    s.add(Tag(name=name))

    def all(self) -> list[dict]:
        """Список {name, count}, отсортированный по имени. Счётчик — по document_tags."""
        with session_scope() as s:
            counts = dict(
                s.execute(
                    select(DocumentTag.tag, func.count(DocumentTag.doc_id)).group_by(
                        DocumentTag.tag
                    )
                ).all()
            )
            names = [n for (n,) in s.execute(select(Tag.name)).all()]
        items = [
            {"name": name, "count": counts.get(name, 0)}
            for name in sorted({*names, *counts.keys()}, key=str.lower)
        ]
        return items
