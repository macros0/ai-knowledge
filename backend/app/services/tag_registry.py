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

from sqlalchemy import String, cast, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import joinedload, selectinload

from app.db.models import Document, DocumentTag, Tag, TagTranslation, ReferenceResourceState
from app.db.session import session_scope
from app import error_codes as codes
from app.services.errors import DomainError
from app.services.reference_resources import ReferenceKey
from app.services.reference_concurrency import lock_reference_resources, bump_reference_revision, ReferenceError
from app.services.reference_mutation import guard_reference_write, guard_reference_writes
from app.services.reference_edit_lock import invalidate_reference_lease


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


def ensure_assigned_tags(session, identifiers):
    """Coordinate a document link/restore with cleanup in the same transaction."""
    keys = [ReferenceKey("tag", str(identifier)) for identifier in set(identifiers)]
    states = lock_reference_resources(session, keys)
    for key in sorted(keys, key=lambda item: item.canonical):
        tag = session.get(Tag, int(key.resource_id), populate_existing=True)
        if tag is None:
            raise ReferenceError("tag_not_found", 404)
        if tag.deleted_at is not None:
            tag.deleted_at = None
            bump_reference_revision(states[key])


class TagInUseError(Exception):
    """Тег используется документами — удаление из справочника запрещено."""


class TagRegistry:
    # --- Разрешение / создание ---

    def get_or_create_ids(
        self, tags: list[str] | None, created_by: str | None = None, *, canonical_locale: str = "und"
    ) -> list[int]:
        """Резолвит канонические тексты в tag_id, создавая отсутствующие.

        Возвращает список id в порядке входного списка (нормализованного). Это
        единая точка создания тегов — её используют registry.create/update при
        записи document_tags и upload при регистрации пула.
        """
        normalized = normalize_tags(tags)
        if not normalized:
            return []
        with session_scope() as s:
            insert = pg_insert if s.get_bind().dialect.name == 'postgresql' else sqlite_insert
            existing_names = set(s.scalars(select(Tag.canonical_text).where(Tag.canonical_text.in_(normalized))).all())
            for name in sorted(set(normalized) - existing_names):
                s.execute(insert(Tag).values(canonical_text=name, created_by=created_by,
                    canonical_locale=canonical_locale).on_conflict_do_nothing(index_elements=['canonical_text']))
            rows = s.execute(select(Tag).where(Tag.canonical_text.in_(normalized))).scalars().all()
            by_name = {tag.canonical_text: tag for tag in rows}
            ensure_assigned_tags(s, [tag.id for tag in rows])
            return [by_name[name].id for name in normalized]

    def add(self, tags: list[str] | None, created_by: str | None = None, *, canonical_locale: str = "und") -> None:
        """Регистрирует теги (создаёт строки `tags`; идемпотентно)."""
        self.get_or_create_ids(tags, created_by=created_by, canonical_locale=canonical_locale)

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
            counts = (select(DocumentTag.tag_id, func.count().label("used_count"))
                .join(Document, Document.id == DocumentTag.doc_id)
                .where(Document.deleted_at.is_(None)).group_by(DocumentTag.tag_id).subquery())
            key = literal('["tag","') + cast(Tag.id, String) + literal('",null,null]')
            # One MVCC statement binds every translation to the same revision.
            rows = s.execute(select(Tag, func.coalesce(counts.c.used_count, 0),
                    func.coalesce(ReferenceResourceState.revision, 1))
                .outerjoin(counts, counts.c.tag_id == Tag.id)
                .outerjoin(ReferenceResourceState, ReferenceResourceState.resource_key == key)
                .options(joinedload(Tag.translations))).unique().all()
        items: list[dict] = []
        for t, used_count, reference_revision in rows:
            if t.deleted_at is not None and not used_count:
                continue
            translation = (
                next((tr for tr in t.translations if tr.locale == locale), None)
                if locale and locale != t.canonical_locale
                else None
            )
            items.append(
                {
                    "id": t.id,
                    "reference_revision": reference_revision,
                    "name": t.canonical_text,
                    "canonical_locale": t.canonical_locale,
                    "display": translation.text if translation else t.canonical_text,
                    "count": used_count,
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

    def delete(self, name: str, *, expected_reference_revision=None, actor=None, lease=None, ip_address=None) -> bool:
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
            state = guard_reference_write(s, ReferenceKey("tag", str(tag.id)),
                expected_revision=expected_reference_revision, actor=actor, lease=lease)
            s.refresh(tag)
            if tag.deleted_at is not None:
                return False
            used = self._used_count(s, tag.id)
            if used:
                raise TagInUseError(f"Тег «{name}» используется {used} документ(ами)")
            tag.deleted_at = _now()
            bump_reference_revision(state)
            invalidate_reference_lease(state)
            if actor is not None:
                from app.services import audit
                audit.record_in_session(s, action_type=audit.TAG_DELETE,
                    user_id=actor.user_id, username=actor.username, target_type=audit.TARGET_TAG,
                    target_id=name, old_value={"name": name}, ip_address=ip_address)
            return True

    def delete_unused(self) -> list[str]:
        """Legacy maintenance writer; occupied resources abort the transaction.

        HTTP cleanup uses explicit previews and per-card results instead.
        """
        from app.services import audit
        with session_scope() as session:
            identifiers = list(session.scalars(select(Tag.id).where(Tag.deleted_at.is_(None))))
            keys = [ReferenceKey("tag", identifier) for identifier in identifiers]
            states = guard_reference_writes(session, keys)
            tags = session.scalars(select(Tag).where(Tag.id.in_(identifiers), Tag.deleted_at.is_(None))
                .execution_options(populate_existing=True)).all()
            names = []
            for tag in tags:
                if self._used_count(session, tag.id):
                    continue
                tag.deleted_at = _now()
                state = states[ReferenceKey("tag", tag.id)]
                bump_reference_revision(state)
                invalidate_reference_lease(state)
                audit.record_in_session(session, action_type=audit.TAG_DELETE, username="system",
                    target_type=audit.TARGET_TAG, target_id=tag.canonical_text, old_value={"name": tag.canonical_text})
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
        expected_reference_revision: int | None = None,
        actor=None,
        lease=None,
    ) -> dict:
        """Записывает/обновляет перевод имени тега для локали. 404-семантика через
        ValueError при отсутствии тега."""
        text = (text or "").strip()
        if not text:
            raise DomainError("Перевод не может быть пустым", code=codes.TRANSLATION_EMPTY)
        with session_scope() as s:
            state = guard_reference_write(s, ReferenceKey("tag", str(tag_id)),
                expected_revision=expected_reference_revision, actor=actor, lease=lease)
            tag = s.get(Tag, tag_id)
            if tag is None or tag.deleted_at is not None:
                raise ValueError(f"Тег {tag_id} не найден")
            tr = next((t for t in tag.translations if t.locale == locale), None)
            if tr is not None and (tr.text, tr.is_machine_translated, tr.reviewed_by) == (text, is_machine, reviewed_by):
                return {"tag_id": tag_id, "locale": locale, "text": text, "reference_revision": state.revision}
            if tr is None:
                tr = TagTranslation(tag_id=tag_id, locale=locale, text=text)
                s.add(tr)
            else:
                tr.text = text
            tr.is_machine_translated = is_machine
            tr.reviewed_by = reviewed_by
            tr.translated_at = _now() if reviewed_by else None
            bump_reference_revision(state)
            if actor is not None:
                from app.services import audit
                audit.record_in_session(s, action_type=audit.TAG_TRANSLATION_UPDATE,
                    user_id=actor.user_id, username=actor.username, target_type=audit.TARGET_TAG,
                    target_id=str(tag_id), new_value={"locale": locale, "text": text, "reference_revision": state.revision})
            s.flush()
            return {"tag_id": tag_id, "locale": locale, "text": text, "reference_revision": state.revision}

    def bulk_review(self, tag_ids: list[int], reviewed_by: str | None) -> int:
        """Помечает машинные переводы выбранных тегов как подтверждённые человеком.

        Возвращает число подтверждённых переводов. Затрагиваются все машинные
        переводы (is_machine_translated=True) без reviewed_by.
        """
        reviewed_by = reviewed_by or "anonymous"
        count = 0
        with session_scope() as s:
            identifiers = list(s.scalars(select(Tag.id).where(Tag.id.in_(tag_ids), Tag.deleted_at.is_(None))))
            keys = [ReferenceKey("tag", identifier) for identifier in identifiers]
            states = guard_reference_writes(s, keys)
            tags = s.execute(
                select(Tag).options(selectinload(Tag.translations)).where(Tag.id.in_(identifiers), Tag.deleted_at.is_(None))
            ).scalars().all()
            for tag in tags:
                state = states[ReferenceKey("tag", tag.id)]
                changed = False
                for tr in tag.translations:
                    if tr.is_machine_translated and not tr.reviewed_by:
                        tr.reviewed_by = reviewed_by
                        tr.translated_at = _now()
                        count += 1
                        changed = True
                if changed:
                    bump_reference_revision(state)
        return count
