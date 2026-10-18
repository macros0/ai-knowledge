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
from sqlalchemy.orm import object_session

from app.db.models import AttributeValue, AttributeValueTranslation, Development
from app.db.session import session_scope
from app.services import audit
from app.services.reference_resources import ReferenceKey, require_access
from app.services.reference_concurrency import (ReferenceError, lock_reference_resources,
    current_reference_revision, bump_reference_revision)
from app.services.reference_mutation import guard_reference_write


def catalog_key(key, org_id=None):
    try:
        return ReferenceKey("attribute_catalog", key, org_id=org_id)
    except ValueError as exc:
        raise ReferenceError("invalid_request", 422) from exc


def catalog_keys(key, org_id=None):
    # The global row also serializes membership/soft module-reference decisions.
    return list({catalog_key(key), catalog_key(key, org_id)})


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
        "reference_revision": current_reference_revision(object_session(a), catalog_key(a.attribute_key, a.org_id)),
        "attribute_key": a.attribute_key,
        "value": a.value,
        "label": a.label,
        "canonical_locale": a.canonical_locale,
        "sort_order": a.sort_order,
        "org_id": a.org_id,
        "created_by": a.created_by,
    }


class AttributeRegistry:
    def list(self, key: str, org_id: int | None = None) -> list[dict]:
        """Значения атрибута. org_id=None → все значения ключа (global ∪ scoped)."""
        return self.snapshot(key, org_id)["values"]

    def snapshot(self, key, org_id=None, locale=None):
        with session_scope() as s:
            states = lock_reference_resources(s, catalog_keys(key, org_id))
            stmt = select(AttributeValue).where(AttributeValue.attribute_key == key)
            if org_id is not None:
                stmt = stmt.where(AttributeValue.org_id == org_id)
            stmt = stmt.order_by(AttributeValue.sort_order, AttributeValue.value)
            values = [_to_dict(a) for a in s.execute(stmt).scalars().all()]
            self._display_labels(s, values, locale)
            return {"values": values, "reference_revision": states[catalog_key(key, org_id)].revision}

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
        canonical_locale: str = "und",
        *, expected_reference_revision=None, actor=None, ip_address=None,
    ) -> dict:
        """Идемпотентно добавляет значение (если уже есть — возвращает существующее)."""
        with session_scope() as s:
            states = lock_reference_resources(s, catalog_keys(key, org_id))
            resource = catalog_key(key, org_id)
            if actor is not None:
                require_access(resource, actor)
                if type(expected_reference_revision) is not int or expected_reference_revision < 1:
                    raise ReferenceError("reference_precondition_required", 428)
            existing = s.execute(
                select(AttributeValue).where(
                    AttributeValue.attribute_key == key,
                    AttributeValue.value == value,
                    AttributeValue.org_id == org_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return _to_dict(existing)
            state = guard_reference_write(s, resource, expected_revision=expected_reference_revision, actor=actor)
            item = AttributeValue(
                attribute_key=key,
                value=value,
                label=label,
                canonical_locale=canonical_locale,
                sort_order=sort_order,
                org_id=org_id,
                created_by=created_by,
            )
            s.add(item)
            s.flush()
            bump_reference_revision(state)
            if org_id is not None:
                bump_reference_revision(states[catalog_key(key)])
            if actor is not None:
                audit.record_in_session(s, action_type=audit.ATTRIBUTE_CREATE, user_id=actor.user_id, username=actor.username,
                    target_type=audit.TARGET_ATTRIBUTE, target_id=f"{key}:{value}",
                    new_value={"attribute_key": key, "value": value, "org_id": org_id}, ip_address=ip_address)
            return _to_dict(item)

    def remove(self, key: str, value: str, org_id: int | None = None, *, expected_reference_revision=None, actor=None, ip_address=None) -> bool:
        with session_scope() as s:
            states = lock_reference_resources(s, catalog_keys(key, org_id))
            state = guard_reference_write(s, catalog_key(key, org_id), expected_revision=expected_reference_revision, actor=actor)
            if key == "module":
                count = (
                    s.query(Development).filter(Development.module == value).count()
                )
                if count:
                    raise AttributeValueInUseError(key, value, count)
            stmt = s.query(AttributeValue).filter(
                AttributeValue.attribute_key == key,
                AttributeValue.value == value,
                AttributeValue.org_id == org_id,
            )
            deleted = stmt.delete(synchronize_session=False)
            if deleted:
                bump_reference_revision(state)
                if org_id is not None:
                    bump_reference_revision(states[catalog_key(key)])
                if actor is not None:
                    audit.record_in_session(s, action_type=audit.ATTRIBUTE_DELETE, user_id=actor.user_id, username=actor.username,
                        target_type=audit.TARGET_ATTRIBUTE, target_id=f"{key}:{value}",
                        old_value={"attribute_key": key, "value": value, "org_id": org_id}, ip_address=ip_address)
            return bool(deleted)

    def add_display_labels(self, items: list[dict], locale: str | None) -> None:
        with session_scope() as s:
            self._display_labels(s, items, locale)

    @staticmethod
    def _display_labels(s, items, locale):
        """Заполняет `display_label` (перевод label для locale). None — без перевода."""
        for it in items:
            it["display_label"] = None
        if not items or not locale:
            return
        ids = [it["id"] for it in items]
        rows = s.execute(
                select(
                    AttributeValueTranslation.attribute_value_id,
                    AttributeValueTranslation.label,
                ).where(
                    AttributeValueTranslation.attribute_value_id.in_(ids),
                    AttributeValueTranslation.locale == locale,
                )
        ).all()
        trs = dict(rows)
        for it in items:
            if locale != it.get("canonical_locale"):
                it["display_label"] = trs.get(it["id"])


_INSTANCE: AttributeRegistry | None = None


def get_attribute_registry() -> AttributeRegistry:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = AttributeRegistry()
    return _INSTANCE
