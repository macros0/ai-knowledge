"""Справочник номеров разработки и связь документа с разработкой (Этап 4).

Каноническая связь — documents.development_id (FK). Проекция для поиска —
payload Qdrant `dev_tags = [number, name, module]`, обновляется через
services.dev_sync (reindex без пере-эмбеддинга).

`module` мягко валидируется против attribute_values('module'): значение вне
справочника отклоняется (DevelopmentModuleError → 422 в API). Орг-фильтрация
отложена — membership проверяется по всем значениям ключа.
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import Development, DevelopmentTranslation, Document
from app.db.session import session_scope
from app.services.attribute_registry import get_attribute_registry

logger = logging.getLogger(__name__)

# Сентинел фильтра «без модуля» в GET /developments?module=__none__ (часть API-контракта).
# Отличается от «фильтр не передан» (module=None → без фильтра).
MODULE_NONE = "__none__"


class DevelopmentNumberExistsError(ValueError):
    """Номер разработки уже занят."""


class DevelopmentModuleError(ValueError):
    """module не найден в справочнике attribute_values('module')."""


class DevelopmentConflictError(ValueError):
    """Запись изменена другим пользователем (оптимистическая блокировка по version).

    `current` — актуальное состояние записи (DevelopmentOut-совместимый dict),
    чтобы API мог вернуть его клиенту в теле 409 (version_conflict) и фронт
    обновил версию для повторной отправки без перепечатывания ввода.
    """

    def __init__(self, current: dict):
        self.current = current
        super().__init__("Разработка изменена другим пользователем")


def _to_dict(dev: Development, documents_count: int = 0) -> dict:
    return {
        "id": dev.id,
        "number": dev.number,
        "name": dev.name,
        "module": dev.module,
        "version": dev.version,
        "created_at": dev.created_at,
        "created_by": dev.created_by,
        "documents_count": documents_count,
    }


class DevelopmentRegistry:
    def __init__(self) -> None:
        self._attr = get_attribute_registry()

    # --- Валидация module ---

    def _validate_module(self, module: str | None) -> str | None:
        if module is None or module == "":
            return module
        value = module.strip()
        if not self._attr.exists("module", value):
            raise DevelopmentModuleError(
                f"Модуль '{value}' отсутствует в справочнике. "
                "Сначала добавьте его через /api/attributes/module."
            )
        return value

    # --- CRUD ---

    def create(self, number: str, name: str, module: str | None = None, created_by: str | None = None) -> dict:
        number = number.strip()
        name = name.strip()
        if not number:
            raise ValueError("Номер разработки не может быть пустым")
        if not name:
            raise ValueError("Название разработки не может быть пустым")
        module = self._validate_module(module)
        with session_scope() as s:
            dev = Development(number=number, name=name, module=module, created_by=created_by, version=1)
            s.add(dev)
            try:
                s.flush()
            except IntegrityError as exc:
                raise DevelopmentNumberExistsError(
                    f"Разработка с номером '{number}' уже существует"
                ) from exc
            return _to_dict(dev, 0)

    def get(self, dev_id: int) -> dict | None:
        with session_scope() as s:
            dev = s.get(Development, dev_id)
            if dev is None:
                return None
            count = self._count(s, dev_id)
            return _to_dict(dev, count)

    def find_by_number(self, number: str) -> dict | None:
        with session_scope() as s:
            dev = s.execute(
                select(Development).where(Development.number == number)
            ).scalar_one_or_none()
            if dev is None:
                return None
            return _to_dict(dev, self._count(s, dev.id))

    def list(self) -> list[dict]:
        """Все разработки (без пагинации) — обратная совместимость для
        dev_detector.match_reference и фронтенд-пикера (который остаётся
        клиентским). Сортировка по умолчанию: number asc."""
        items, _ = self.query()
        return items

    def query(
        self,
        search: str | None = None,
        module: str | None = None,
        sort_by: str = "number",
        order: str = "asc",
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Фильтрация + сортировка + пагинация справочника разработок.

        Возвращает (items, total). Фильтры (search/module) собираются ОДИН раз и
        применяются и к items-запросу, и к count-запросу — total всегда
        соответствует числу отфильтрованных записей.

        - search: префикс по number ИЛИ подстрока (ILIKE) по name;
        - module: точное значение; MODULE_NONE (__none__) → module IS NULL;
          None → фильтр не задан;
        - sort_by ∈ {number, name, module, documents_count}; order ∈ {asc, desc}.
        """
        with session_scope() as s:
            count_subq = (
                select(func.count(Document.id))
                .where(
                    Document.development_id == Development.id,
                    Document.deleted_at.is_(None),
                )
                .correlate(Development)
                .scalar_subquery()
                .label("documents_count")
            )

            conditions: list = []
            if search:
                conditions.append(
                    or_(
                        Development.number.like(f"{search}%"),
                        Development.name.ilike(f"%{search}%"),
                    )
                )
            if module is not None:
                if module == MODULE_NONE:
                    conditions.append(Development.module.is_(None))
                else:
                    conditions.append(Development.module == module)

            sort_map = {
                "number": Development.number,
                "name": Development.name,
                "module": Development.module,
                "documents_count": count_subq,
            }
            sort_col = sort_map.get(sort_by, Development.number)
            order_expr = sort_col.desc() if order == "desc" else sort_col.asc()

            stmt = select(Development, count_subq)
            if conditions:
                stmt = stmt.where(*conditions)
            stmt = stmt.order_by(order_expr).offset(offset)
            if limit is not None:
                stmt = stmt.limit(limit)

            total_stmt = select(func.count()).select_from(Development)
            if conditions:
                total_stmt = total_stmt.where(*conditions)
            total = s.execute(total_stmt).scalar_one()

            rows = s.execute(stmt).all()
            return [_to_dict(dev, count) for dev, count in rows], total

    def update(self, dev_id: int, version: int, number: str | None = None, name: str | None = None, module: str | None = None) -> dict:
        with session_scope() as s:
            dev = s.get(Development, dev_id)
            if dev is None:
                raise ValueError("Разработка не найдена")
            if dev.version != version:
                raise DevelopmentConflictError(_to_dict(dev, self._count(s, dev_id)))

            # Считаем, какие поля реально меняются (для no-op и dev_sync).
            updates: dict = {}
            new_number = dev.number
            new_name = dev.name
            new_module = dev.module

            if number is not None and number.strip() and number.strip() != dev.number:
                new_number = number.strip()
                updates["number"] = new_number
            if name is not None and name.strip() and name.strip() != dev.name:
                new_name = name.strip()
                updates["name"] = new_name
            if module is not None:
                module = self._validate_module(module)
                if module != dev.module:
                    new_module = module
                    updates["module"] = new_module

            if not updates:
                result = _to_dict(dev, self._count(s, dev_id))
                changed = False
            else:
                # Атомарный conditional-update: version сверяется в самом SQL
                # (WHERE id = :id AND version = :version), а не read-then-write в
                # Python — гонка двух одновременных коммитов детектится rowcount.
                updates["version"] = Development.version + 1
                try:
                    outcome = s.execute(
                        update(Development)
                        .where(Development.id == dev_id, Development.version == version)
                        .values(**updates)
                        .execution_options(synchronize_session=False)
                    )
                except IntegrityError as exc:
                    raise DevelopmentNumberExistsError(
                        f"Разработка с номером '{number}' уже существует"
                    ) from exc
                if outcome.rowcount == 0:
                    # Между чтением и UPDATE кто-то успел изменить версию.
                    cur = s.get(Development, dev_id, populate_existing=True)
                    if cur is None:
                        raise ValueError("Разработка не найдена")
                    raise DevelopmentConflictError(_to_dict(cur, self._count(s, dev_id)))
                result = {
                    "id": dev_id,
                    "number": new_number,
                    "name": new_name,
                    "module": new_module,
                    "version": version + 1,
                    "created_at": dev.created_at,
                    "created_by": dev.created_by,
                    "documents_count": self._count(s, dev_id),
                }
                changed = True
        if changed:
            self._schedule_sync(dev_id)
        return result

    def delete(self, dev_id: int, version: int) -> bool:
        """Удаляет разработку, отвязывая её документы (SET NULL) и очищая их dev_tags.

        Версия сверяется атомарно: условный UPDATE по `version` резервирует строку
        (rowcount == 0 — гонка/не найдено), и только затем выполняется отвязка
        документов и удаление. FK к `documents.development_id` требует отвязки
        документов ДО удаления строки, поэтому версия сначала «заявляется»
        инкрементом, а не проверяется на самом DELETE.

        Целостность: id документов захватываются ДО отвязки, чтобы после удаления
        очистить их проекцию dev_tags в Qdrant (иначе _schedule_sync(dev_id) после
        удаления не найдёт ни записи, ни привязанных документов — dev_tags останутся
        протухшими). Поля отвязки — полный набор (как в ручной отвязке документа).
        """
        with session_scope() as s:
            claimed = s.execute(
                update(Development)
                .where(Development.id == dev_id, Development.version == version)
                .values(version=Development.version + 1)
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount == 0:
                cur = s.get(Development, dev_id)
                if cur is None:
                    return False
                raise DevelopmentConflictError(_to_dict(cur, self._count(s, dev_id)))
            # Захватить документы ДО отвязки — их dev_tags нужно очистить в Qdrant.
            doc_ids = list(
                s.execute(
                    select(Document.id).where(Document.development_id == dev_id)
                ).scalars().all()
            )
            # Отвязать документы, чтобы FK не завис.
            s.query(Document).filter(Document.development_id == dev_id).update(
                {
                    "development_id": None,
                    "development_confidence": None,
                    "development_confirmed_by": None,
                    "development_suggestion": None,
                },
                synchronize_session=False,
            )
            s.execute(
                delete(Development)
                .where(Development.id == dev_id)
                .execution_options(synchronize_session=False)
            )
        self._schedule_sync_many(doc_ids)
        return True

    def documents_for(self, dev_id: int) -> list[dict]:
        """Документы разработки (без пагинации) — тонкая обёртка над list_page.

        Возвращает все документы с development_id == dev_id в порядке date_desc.
        Обратная совместимость: прежний N+1 (select id + reg.get на каждый) убран.
        """
        from app.services.registry import get_registry

        docs, _ = get_registry().list_page(development_id=dev_id)
        return docs

    # --- Проекция dev_tags ---

    def dev_tags(self, dev_id: int) -> list[str]:
        with session_scope() as s:
            dev = s.get(Development, dev_id)
            if dev is None:
                return []
            tags = [dev.number, dev.name]
            if dev.module:
                tags.append(dev.module)
            return tags

    @staticmethod
    def _count(s, dev_id: int) -> int:
        return (
            s.execute(
                select(func.count(Document.id)).where(
                    Document.development_id == dev_id, Document.deleted_at.is_(None)
                )
            ).scalar_one()
            or 0
        )

    def _schedule_sync(self, dev_id: int) -> None:
        """Запускает фоновую реиндексацию dev_tags документов разработки.

        Реализация — services.dev_sync (Этап 4, реиндекс без пере-эмбеддинга).
        Ленивый импорт: сервис не падает, если Qdrant/модуль недоступны.
        """
        try:
            from app.services.dev_sync import schedule_dev_sync

            schedule_dev_sync(dev_id)
        except Exception:
            logger.warning(
                "Не удалось запустить фоновую реиндексацию разработки %d",
                dev_id,
                exc_info=True,
            )

    def _schedule_sync_many(self, doc_ids: list[str]) -> None:
        """Фоновая очистка dev_tags списка документов (после удаления разработки)."""
        if not doc_ids:
            return
        try:
            from app.services.dev_sync import schedule_dev_tags_sync_many

            schedule_dev_tags_sync_many(doc_ids)
        except Exception:
            logger.warning(
                "Не удалось запустить очистку dev_tags для %d документов",
                len(doc_ids),
                exc_info=True,
            )

    def add_display_names(self, items: list[dict], locale: str | None) -> None:
        """Заполняет `display_name` (перевод названия для locale) на списке dict.

        display_name=None при отсутствии перевода (фронт фолбэчит на `name`).
        """
        for it in items:
            it["display_name"] = None
        if not items or not locale or locale == "ru":
            return
        ids = [it["id"] for it in items]
        with session_scope() as s:
            rows = s.execute(
                select(
                    DevelopmentTranslation.development_id, DevelopmentTranslation.name
                ).where(
                    DevelopmentTranslation.development_id.in_(ids),
                    DevelopmentTranslation.locale == locale,
                )
            ).all()
        trs = dict(rows)
        for it in items:
            it["display_name"] = trs.get(it["id"])


_INSTANCE: DevelopmentRegistry | None = None


def get_development_registry() -> DevelopmentRegistry:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = DevelopmentRegistry()
    return _INSTANCE
