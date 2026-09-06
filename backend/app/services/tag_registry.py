"""Глобальный справочник тегов (id-модель, Этап 7 фаза B).

`tags` — канонический набор тегов: суррогатный `id` + `canonical_text` (unique,
стабильный wire-идентификатор и payload Qdrant) + `canonical_locale`. Связь
документ→тег — `document_tags.tag_id` (FK). Локализованные имена — `translations`
(tag_translations, для не-канонических локалей).

В отличие от прежней текстовой модели здесь НЕТ отдельного «пула автодополнения»:
тег существует ровно когда есть строка в `tags`, а `document_tags` ссылается на
неё по FK — пул и связи согласованы по построению. `all()` показывает канонические
имена + счётчики АКТИВНЫХ документов (корзинные не считаются — баг 06.09.2026).

Удаление тега (delete/delete_unused) — soft-delete (`tags.deleted_at`): связь
document_tags корзинного документа не трогается, после restore тег снова виден
(баг 06.09.2026 «тег ааа не удалялся»). Повторное использование текста тега
возрождает его (deleted_at → NULL) в get_or_create_ids.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.db.models import Document, DocumentTag, Tag, TagTranslation
from app.db.session import session_scope


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
    # --- Разрешение / создание ---

    def get_or_create_ids(
        self, tags: list[str] | None, created_by: str | None = None
    ) -> list[int]:
        """Резолвит канонические тексты в tag_id, создавая отсутствующие.

        Возвращает список id в порядке входного списка (нормализованного). Это
        единая точка создания тегов — её используют registry.create/update при
        записи document_tags и upload при регистрации пула.
        """
        normalized = normalize_tags(tags)
        if not normalized:
            return []
        ids: list[int] = []
        with session_scope() as s:
            existing = {
                t.canonical_text: t
                for t in s.execute(
                    select(Tag).where(Tag.canonical_text.in_(normalized))
                ).scalars().all()
            }
            for name in normalized:
                tag = existing.get(name)
                if tag is None:
                    tag = Tag(canonical_text=name, created_by=created_by)
                    s.add(tag)
                    s.flush()
                    existing[name] = tag
                elif tag.deleted_at is not None:
                    tag.deleted_at = None  # возрождение удалённого из пула
                ids.append(tag.id)
        return ids

    def add(self, tags: list[str] | None, created_by: str | None = None) -> None:
        """Регистрирует теги (создаёт строки `tags`; идемпотентно)."""
        self.get_or_create_ids(tags, created_by=created_by)

    def resolve(self, text: str, locale: str | None = None) -> int | None:
        """Резолвит текст → tag_id: перевод в locale → канонический текст.

        Порядок (зафиксирован в плане Этапа 7): сначала перевод в текущей locale
        пользователя, затем канонический текст; внутри шага коллизия разрешается
        детерминированно по младшему tag_id.
        """
        text = (text or "").strip()
        if not text:
            return None
        with session_scope() as s:
            if locale:
                tid = s.execute(
                    select(TagTranslation.tag_id)
                    .where(TagTranslation.locale == locale, TagTranslation.text == text)
                    .order_by(TagTranslation.tag_id)
                    .limit(1)
                ).scalar_one_or_none()
                if tid is not None:
                    return tid
            return s.execute(
                select(Tag.id).where(Tag.canonical_text == text)
            ).scalar_one_or_none()

    def get_canonical_text(self, tag_id: int) -> str | None:
        with session_scope() as s:
            return s.execute(
                select(Tag.canonical_text).where(Tag.id == tag_id)
            ).scalar_one_or_none()

    # --- Чтение ---

    def all(self, locale: str | None = None) -> list[dict]:
        """Список [{id, name, display, count, needs_review}] по каноническому имени.

        count — число АКТИВНЫХ документов (deleted_at IS NULL); корзинные доки не
        считаются. В выдачу попадает тег, если он в пуле (tags.deleted_at IS NULL)
        ЛИБО на него ссылается хотя бы один АКТИВНЫЙ документ (soft-deleted тег,
        оставшийся на корзинном доке, скрыт до restore). display — перевод в
        `locale` (fallback canonical_text); needs_review — есть машинный перевод
        без подтверждения человеком.
        """
        with session_scope() as s:
            counts = dict(
                s.execute(
                    select(DocumentTag.tag_id, func.count())
                    .join(Document, Document.id == DocumentTag.doc_id)
                    .where(Document.deleted_at.is_(None))
                    .group_by(DocumentTag.tag_id)
                ).all()
            )
            tags = s.execute(
                select(Tag).options(selectinload(Tag.translations))
            ).scalars().all()
        items: list[dict] = []
        for t in tags:
            if t.deleted_at is not None and t.id not in counts:
                continue
            translation = (
                next((tr for tr in t.translations if tr.locale == locale), None)
                if locale
                else None
            )
            items.append(
                {
                    "id": t.id,
                    "name": t.canonical_text,
                    "display": translation.text if translation else t.canonical_text,
                    "count": counts.get(t.id, 0),
                    "needs_review": any(
                        tr.is_machine_translated and not tr.reviewed_by
                        for tr in t.translations
                    ),
                    "translations": [
                        {
                            "locale": tr.locale,
                            "text": tr.text,
                            "is_machine_translated": tr.is_machine_translated,
                            "reviewed_by": tr.reviewed_by,
                            "translated_at": tr.translated_at,
                        }
                        for tr in t.translations
                    ],
                }
            )
        items.sort(key=lambda x: x["name"].lower())
        return items

    # --- Удаление / чистка ---

    def _used_count(self, s, tag_id: int) -> int:
        return s.execute(
            select(func.count())
            .select_from(DocumentTag)
            .join(Document, Document.id == DocumentTag.doc_id)
            .where(DocumentTag.tag_id == tag_id, Document.deleted_at.is_(None))
        ).scalar_one()

    def delete(self, name: str) -> bool:
        """Удаляет тег из пула автодополнения (soft-delete: tags.deleted_at), если
        он не используется АКТИВНЫМИ документами. Связи document_tags НЕ трогаются
        — корзинный документ сохраняет тег, после restore тег снова виден.

        False — тега нет; TagInUseError — используется активным документом.
        """
        with session_scope() as s:
            tag = s.execute(
                select(Tag).where(Tag.canonical_text == name)
            ).scalar_one_or_none()
            if tag is None:
                return False
            used = self._used_count(s, tag.id)
            if used:
                raise TagInUseError(f"Тег «{name}» используется {used} документ(ами)")
            tag.deleted_at = _now()
            return True

    def delete_unused(self) -> list[str]:
        """Помечает удалёнными из пула все теги без АКТИВНЫХ документов (мусор).

        Возвращает имена помеченных в этот прогон. document_tags не затрагиваются.
        """
        with session_scope() as s:
            used_ids = {
                tid
                for (tid,) in s.execute(
                    select(DocumentTag.tag_id)
                    .distinct()
                    .join(Document, Document.id == DocumentTag.doc_id)
                    .where(Document.deleted_at.is_(None))
                ).all()
            }
            tags = s.execute(select(Tag)).scalars().all()
            names: list[str] = []
            for tag in tags:
                if tag.id not in used_ids and tag.deleted_at is None:
                    tag.deleted_at = _now()
                    names.append(tag.canonical_text)
            return sorted(names, key=str.lower)

    # --- Переводы ---

    def set_translation(
        self,
        tag_id: int,
        locale: str,
        text: str,
        *,
        is_machine: bool = False,
        reviewed_by: str | None = None,
    ) -> dict:
        """Записывает/обновляет перевод имени тега для локали. 404-семантика через
        ValueError при отсутствии тега."""
        text = (text or "").strip()
        if not text:
            raise ValueError("Перевод не может быть пустым")
        with session_scope() as s:
            tag = s.get(Tag, tag_id)
            if tag is None:
                raise ValueError(f"Тег {tag_id} не найден")
            tr = next((t for t in tag.translations if t.locale == locale), None)
            if tr is None:
                tr = TagTranslation(tag_id=tag_id, locale=locale, text=text)
                s.add(tr)
            else:
                tr.text = text
            tr.is_machine_translated = is_machine
            tr.reviewed_by = reviewed_by
            tr.translated_at = _now() if reviewed_by else None
            s.flush()
            return {"tag_id": tag_id, "locale": locale, "text": text}

    def bulk_review(self, tag_ids: list[int], reviewed_by: str | None) -> int:
        """Помечает машинные переводы выбранных тегов как подтверждённые человеком.

        Возвращает число подтверждённых переводов. Затрагиваются все машинные
        переводы (is_machine_translated=True) без reviewed_by.
        """
        reviewed_by = reviewed_by or "anonymous"
        count = 0
        with session_scope() as s:
            tags = s.execute(
                select(Tag).options(selectinload(Tag.translations)).where(Tag.id.in_(tag_ids))
            ).scalars().all()
            for tag in tags:
                for tr in tag.translations:
                    if tr.is_machine_translated and not tr.reviewed_by:
                        tr.reviewed_by = reviewed_by
                        tr.translated_at = _now()
                        count += 1
        return count
