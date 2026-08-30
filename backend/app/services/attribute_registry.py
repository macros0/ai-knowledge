"""Generic мини-справочник строковых атрибутов (module, component, ...).

Значения хранятся как данные (attribute_key + value), а не как CHECK/enum в
схеме БД — module-подобные поля живут как данные, не как схема (см. AGENTS.md).

org_id=NULL — общее/системное значение; иначе — привязано к организации.
Орг-фильтрация в API отложена (как и во всей системе до мультитенантности),
поэтому membership-проверки (exists/values) по умолчанию идут по ВСЕМ значениям
ключа (global ∪ scoped), а не только по глобальным.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import AttributeValue, Development
from app.db.session import session_scope


class AttributeValueInUseError(ValueError):
    """Значение атрибута используется в справочнике разработок и не может быть удалено."""

    def __init__(self, key: str, value: str, count: int):
        self.key = key
        self.value = value
        self.count = count
        super().__init__(
            f"Значение «{value}» используется в {count} разработках. "
            "Сначала перепривяжите эти разработки к другому значению."
        )


def _to_dict(a: AttributeValue) -> dict:
    return {
        "id": a.id,
        "attribute_key": a.attribute_key,
        "value": a.value,
        "label": a.label,
        "sort_order": a.sort_order,
        "org_id": a.org_id,
        "created_by": a.created_by,
    }


class AttributeRegistry:
    def list(self, key: str, org_id: int | None = None) -> list[dict]:
        """Значения атрибута. org_id=None → все значения ключа (global ∪ scoped)."""
        with session_scope() as s:
            stmt = select(AttributeValue).where(AttributeValue.attribute_key == key)
            if org_id is not None:
                stmt = stmt.where(AttributeValue.org_id == org_id)
            stmt = stmt.order_by(AttributeValue.sort_order, AttributeValue.value)
            return [_to_dict(a) for a in s.execute(stmt).scalars().all()]

    def values(self, key: str, org_id: int | None = None) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for a in self.list(key, org_id=org_id):
            if a["value"] not in seen:
                seen.add(a["value"])
                out.append(a["value"])
        return out

    def exists(self, key: str, value: str, org_id: int | None = None) -> bool:
        with session_scope() as s:
            stmt = select(AttributeValue.id).where(
                AttributeValue.attribute_key == key,
                AttributeValue.value == value,
            )
            if org_id is not None:
                stmt = stmt.where(AttributeValue.org_id == org_id)
            return s.execute(stmt.limit(1)).scalar_one_or_none() is not None

    def add(
        self,
        key: str,
        value: str,
        label: str | None = None,
        sort_order: int = 0,
        org_id: int | None = None,
        created_by: str | None = None,
    ) -> dict:
        """Идемпотентно добавляет значение (если уже есть — возвращает существующее)."""
        with session_scope() as s:
            existing = s.execute(
                select(AttributeValue).where(
                    AttributeValue.attribute_key == key,
                    AttributeValue.value == value,
                    AttributeValue.org_id == org_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return _to_dict(existing)
            item = AttributeValue(
                attribute_key=key,
                value=value,
                label=label,
                sort_order=sort_order,
                org_id=org_id,
                created_by=created_by,
            )
            s.add(item)
            s.flush()
            return _to_dict(item)

    def remove(self, key: str, value: str, org_id: int | None = None) -> bool:
        # Проверка использования и DELETE выполняются в ОДНОЙ транзакции
        # (session_scope), чтобы исключить гонку «проверили count==0 → создали
        # development → удалили значение». Для module ссылка мягкая (developments.module
        # — строка, не FK), поэтому защита здесь — счётчик + мягкая валидация в
        # DevelopmentRegistry.create; этого достаточно для внутреннего инструмента.
        with session_scope() as s:
            if key == "module":
                count = (
                    s.query(Development).filter(Development.module == value).count()
                )
                if count:
                    raise AttributeValueInUseError(key, value, count)
            stmt = s.query(AttributeValue).filter(
                AttributeValue.attribute_key == key,
                AttributeValue.value == value,
            )
            if org_id is not None:
                stmt = stmt.filter(AttributeValue.org_id == org_id)
            deleted = stmt.delete(synchronize_session=False)
            return bool(deleted)


_INSTANCE: AttributeRegistry | None = None


def get_attribute_registry() -> AttributeRegistry:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = AttributeRegistry()
    return _INSTANCE
