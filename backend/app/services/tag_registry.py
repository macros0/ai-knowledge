"""Глобальный справочник тегов на реляционной БД (замена data/tags.json).

`tags` — таблица известных имён тегов (для автодополнения); счётчик использования
вычисляется на чтение агрегатом по `document_tags` (каноническая связь документ→тег,
которую поддерживает DocumentRegistry). Хранить денормализованный count не нужно —
нет триггера и дрейфа между «именем тега» и «числом документов с тегом».
"""
from __future__ import annotations

from sqlalchemy import delete, func, select

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


class TagInUseError(Exception):
    """Тег используется документами — удаление из справочника запрещено."""


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

    def delete(self, name: str) -> bool:
        """Удаляет имя тега из пула автодополнения (таблица `tags`), если оно не
        используется документами.

        Возвращает False, если имени нет в пуле. Бросает TagInUseError, если тег
        используется хотя бы одним документом (document_tags) — удалять такой тег
        нельзя, это сломало бы фильтрацию.
        """
        with session_scope() as s:
            used = s.execute(
                select(func.count())
                .select_from(DocumentTag)
                .where(DocumentTag.tag == name)
            ).scalar_one()
            if used:
                raise TagInUseError(f"Тег «{name}» используется {used} документ(ами)")
            if s.get(Tag, name) is None:
                return False
            s.execute(delete(Tag).where(Tag.name == name))
            return True

    def delete_unused(self) -> list[str]:
        """Удаляет из пула все имена со счётчиком использования 0 (мусор).

        Возвращает список удалённых имён. Связи документов (document_tags) не
        затрагиваются — удаляются только подсказки автодополнения.
        """
        with session_scope() as s:
            used = {n for (n,) in s.execute(select(DocumentTag.tag).distinct()).all()}
            names = [n for (n,) in s.execute(select(Tag.name)).all() if n not in used]
            if names:
                s.execute(delete(Tag).where(Tag.name.in_(names)))
            return names
