"""Глобальный справочник тегов на реляционной БД (замена data/tags.json).

`tags` — таблица известных имён тегов (для автодополнения); счётчик использования
вычисляется на чтение агрегатом по `document_tags` (каноническая связь документ→тег,
которую поддерживает DocumentRegistry). Хранить денормализованный count не нужно —
нет триггера и дрейфа между «именем тега» и «числом документов с тегом».

«Используется» = тег стоит на АКТИВНОМ документе (`documents.deleted_at IS NULL`).
Документы в корзине (soft delete, Этап 4a.2) не блокируют удаление имени из пула:
их связи document_tags не трогаются, но в счётчиках и guard'ах не участвуют (иначе
тег, оставшийся только на корзинном доке, был бы «залочен» до purge — баг 06.09.2026,
«тег ааа не удаляется»). INNER JOIN к documents также игнорирует orphan-строки
document_tags (док физически удалён — актуально для SQLite dev, где FK-cascade
не включён). После восстановления дока имя снова честно считается используемым:
`all()` объединяет имена пула со счётчиками, а правка тегов вызывает `add()`.
"""
from __future__ import annotations

from sqlalchemy import delete, func, select

from app.db.models import Document, DocumentTag, Tag
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
        """Список {name, count}, отсортированный по имени. Счётчик — по document_tags
        АКТИВНЫХ документов (deleted_at IS NULL; корзинные доки не считаются)."""
        with session_scope() as s:
            counts = dict(
                s.execute(
                    select(DocumentTag.tag, func.count())
                    .join(Document, Document.id == DocumentTag.doc_id)
                    .where(Document.deleted_at.is_(None))
                    .group_by(DocumentTag.tag)
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
        используется активными документами.

        Возвращает False, если имени нет в пуле. Бросает TagInUseError, если тег
        стоит хотя бы на одном АКТИВНОМ документе (document_tags + documents.deleted_at
        IS NULL) — удалять такой тег нельзя, это сломало бы фильтрацию. Тег,
        оставшийся только на доке(ах) в корзине, удалить можно: document_tags не
        трогаются, а при восстановлении дока имя возвращается в выдачу через
        счётчики `all()` (и `_tag_registry.add` при следующей правке тегов).
        """
        with session_scope() as s:
            used = s.execute(
                select(func.count())
                .select_from(DocumentTag)
                .join(Document, Document.id == DocumentTag.doc_id)
                .where(DocumentTag.tag == name, Document.deleted_at.is_(None))
            ).scalar_one()
            if used:
                raise TagInUseError(f"Тег «{name}» используется {used} документ(ами)")
            if s.get(Tag, name) is None:
                return False
            s.execute(delete(Tag).where(Tag.name == name))
            return True

    def delete_unused(self) -> list[str]:
        """Удаляет из пула все имена без АКТИВНЫХ документов (мусор).

        Возвращает список удалённых имён. Связи документов (document_tags) не
        затрагиваются — удаляются только подсказки автодополнения; тег, оставшийся
        только на доке(ах) в корзине, считается неиспользуемым и чистится.
        """
        with session_scope() as s:
            used = {
                n
                for (n,) in s.execute(
                    select(DocumentTag.tag)
                    .distinct()
                    .join(Document, Document.id == DocumentTag.doc_id)
                    .where(Document.deleted_at.is_(None))
                ).all()
            }
            names = [n for (n,) in s.execute(select(Tag.name)).all() if n not in used]
            if names:
                s.execute(delete(Tag).where(Tag.name.in_(names)))
            return names
