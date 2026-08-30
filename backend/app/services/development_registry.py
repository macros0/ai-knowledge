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

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.db.models import Development, Document
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


def _to_dict(dev: Development, documents_count: int = 0) -> dict:
    return {
        "id": dev.id,
        "number": dev.number,
        "name": dev.name,
        "module": dev.module,
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
            dev = Development(number=number, name=name, module=module, created_by=created_by)
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
                .where(Document.development_id == Development.id)
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

    def update(self, dev_id: int, number: str | None = None, name: str | None = None, module: str | None = None) -> dict:
        changed = False
        with session_scope() as s:
            dev = s.get(Development, dev_id)
            if dev is None:
                raise ValueError("Разработка не найдена")
            if number is not None and number.strip() and number.strip() != dev.number:
                dev.number = number.strip()
                changed = True
            if name is not None and name.strip() and name.strip() != dev.name:
                dev.name = name.strip()
                changed = True
            if module is not None:
                module = self._validate_module(module)
                if module != dev.module:
                    dev.module = module
                    changed = True
            try:
                s.flush()
            except IntegrityError as exc:
                raise DevelopmentNumberExistsError(
                    f"Разработка с номером '{number}' уже существует"
                ) from exc
            result = _to_dict(dev, self._count(s, dev_id))
        if changed:
            self._schedule_sync(dev_id)
        return result

    def delete(self, dev_id: int) -> bool:
        """Удаляет разработку, отвязывая её документы (SET NULL) и запуская реиндекс dev_tags."""
        with session_scope() as s:
            dev = s.get(Development, dev_id)
            if dev is None:
                return False
            # Отвязать документы, чтобы FK не завис.
            s.query(Document).filter(Document.development_id == dev_id).update(
                {"development_id": None, "development_confidence": None},
                synchronize_session=False,
            )
            s.delete(dev)
        self._schedule_sync(dev_id)
        return True

    def documents_for(self, dev_id: int) -> list[dict]:
        from app.services.registry import get_registry

        with session_scope() as s:
            ids = s.execute(
                select(Document.id).where(Document.development_id == dev_id)
            ).scalars().all()
        reg = get_registry()
        result: list[dict] = []
        for doc_id in ids:
            doc = reg.get(doc_id)
            if doc:
                result.append(doc)
        return result

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
                select(func.count(Document.id)).where(Document.development_id == dev_id)
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


_INSTANCE: DevelopmentRegistry | None = None


def get_development_registry() -> DevelopmentRegistry:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = DevelopmentRegistry()
    return _INSTANCE
