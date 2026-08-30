"""Сид стартовых значений attribute_values для конкретной инсталляции.

НЕ входит в базовую Alembic-миграцию: значения module (PY/PT/OM/PA) логически
принадлежат текущей инсталляции/направлению (SAP HCM), а не являются глобальным
дефолтом системы. При появлении второго направления скрипт запускается заново
с другим --org, и значения не перемешиваются.

Запуск (из backend/):
    python scripts/seed_attribute_values.py                 # org "SAP HCM", module PY/PT/OM/PA
    python scripts/seed_attribute_values.py --org "Другое направление"

Идемпотентно: существующая организация и значения не дублируются.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.models import AttributeValue, Organization
from app.db.session import get_session_factory

DEFAULT_ORG = "SAP HCM"
DEFAULT_KEY = "module"
DEFAULT_VALUES = ["PY", "PT", "OM", "PA"]


def _ensure_org(factory, name: str) -> int:
    with factory() as s:
        org = s.execute(select(Organization).where(Organization.name == name)).scalar_one_or_none()
        if org is not None:
            return org.id
        org = Organization(name=name)
        s.add(org)
        s.commit()
        return org.id


def seed(org_name: str, key: str, values: list[str]) -> dict:
    """Создаёт организацию (если нет) и значения attribute_values, привязанные к ней.

    Возвращает {"org_id": int, "created": int, "existing": int}.
    """
    factory = get_session_factory()
    org_id = _ensure_org(factory, org_name)

    created = 0
    existing = 0
    with factory() as s:
        present = set(
            s.execute(
                select(AttributeValue.value).where(
                    AttributeValue.attribute_key == key,
                    AttributeValue.org_id == org_id,
                )
            ).scalars().all()
        )
        for value in values:
            if value in present:
                existing += 1
                continue
            s.add(
                AttributeValue(
                    attribute_key=key,
                    value=value,
                    org_id=org_id,
                    sort_order=0,
                    created_by="seed",
                )
            )
            created += 1
        s.commit()
    return {"org_id": org_id, "created": created, "existing": existing}


def main() -> None:
    parser = argparse.ArgumentParser(description="Сид attribute_values для инсталляции.")
    parser.add_argument("--org", type=str, default=DEFAULT_ORG, help="Имя организации")
    parser.add_argument("--key", type=str, default=DEFAULT_KEY, help="attribute_key")
    parser.add_argument(
        "--values",
        type=str,
        default=",".join(DEFAULT_VALUES),
        help="Значения через запятую",
    )
    args = parser.parse_args()

    values = [v.strip() for v in args.values.split(",") if v.strip()]
    result = seed(args.org, args.key, values)
    print(
        f"org={args.org!r} (id={result['org_id']}), key={args.key!r}: "
        f"создано {result['created']}, уже было {result['existing']} (значения: {values})"
    )


if __name__ == "__main__":
    main()
