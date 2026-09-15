# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""CLI-обёртка перевода справочников (headless/runbook путь, Этап 7).

Поверх единого сервисного метода `backfill_reference_data` — та же бизнес-логика,
что и admin-эндпоинт POST /api/tags/translations/backfill (идемпотентность,
ручные переводы не перезаписываются, audit). Требует доступной БД (см. AGENTS.md:
коннект через session_scope).

Перед запуском печатает фактический маршрут (provider + model) — оператор обязан
видеть, уходит ли перевод во внешний LLM (в dev .env — OpenRouter), прежде чем
отправить справочники модели.

Примеры:
  python scripts/backfill_translations.py --locale en
  python scripts/backfill_translations.py --locale en --entities tags,developments
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.services.translation import backfill_reference_data


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Автоперевод справочников (теги/разработки/атрибуты)")
    p.add_argument("--locale", required=True, help="Целевая локаль (например, en)")
    p.add_argument(
        "--entities",
        default="tags,developments,attributes",
        help="Сущности через запятую: tags,developments,attributes,glossary",
    )
    p.add_argument("--user", default="cli", help="Имя оператора для audit_log (username)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    entities = [e.strip() for e in args.entities.split(",") if e.strip()]
    unknown = [e for e in entities if e not in ("tags", "developments", "attributes", "glossary")]
    if unknown:
        print(f"Неизвестные сущности: {', '.join(unknown)}", file=sys.stderr)
        return 2
    if not entities:
        print("Пустой список сущностей", file=sys.stderr)
        return 2

    provider = settings.translation_provider
    model = settings.translation_model or settings.llm_model
    print("Translation provider:", provider)
    print("Translation model:", model)
    print("Locale:", args.locale)
    print("Entities:", ", ".join(entities))
    if provider == "off":
        print("⚠ Провайдер перевода отключён (translation_provider=off): бэкфилл "
              "ничего не создаст — используйте ручные переводы / translations-словарь.",
              file=sys.stderr)

    operator = SimpleNamespace(user_id=f"cli:{args.user}", username=args.user)
    result = backfill_reference_data(
        args.locale, entities, user=operator, ip_address=None
    )

    print("Результат:")
    failed_total = 0
    for ent in entities:
        r = result.get(ent, {"created": 0, "failed": 0})
        created, failed = r.get("created", 0), r.get("failed", 0)
        print(f"  {ent}: created={created} failed={failed}")
        failed_total += failed
    if failed_total:
        print(f"⚠ {failed_total} переводов не удалось", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
