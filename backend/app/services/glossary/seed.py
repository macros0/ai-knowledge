"""Validated, idempotent transport for the initial glossary entries."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from app.services.glossary.normalization import (
    GlossaryValidationError,
    normalize_canonical,
    validate_alias_options,
    validate_locale,
)
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.types import GlossaryAliasInput

DEFAULT_SEED_PATH = Path(__file__).resolve().parents[3] / "seeds" / "glossary.json"


def load_seed(path: str | Path = DEFAULT_SEED_PATH) -> list[dict]:
    with Path(path).open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, list):
        raise GlossaryValidationError("Seed глоссария должен быть массивом записей")
    validate_seed(value)
    return value


def validate_seed(entries: Iterable[dict]) -> None:
    rows = list(entries)
    canonicals: set[str] = set()
    aliases: set[str] = set()
    for index, entry in enumerate(rows):
        if not isinstance(entry, dict):
            raise GlossaryValidationError(f"Запись seed #{index + 1} должна быть объектом")
        for field in ("canonical", "kind", "original_name", "canonical_locale", "aliases"):
            if field not in entry:
                raise GlossaryValidationError(f"В записи seed #{index + 1} нет поля {field}")
        canonical = normalize_canonical(entry["canonical"], entry["kind"])
        if canonical in canonicals:
            raise GlossaryValidationError(f"Дублирующийся canonical: {canonical}")
        canonicals.add(canonical)
        validate_locale(entry["canonical_locale"])
        if not isinstance(entry["aliases"], list):
            raise GlossaryValidationError(f"aliases записи #{index + 1} должен быть массивом")
        local_aliases = set()
        for alias in entry["aliases"]:
            if not isinstance(alias, dict) or "alias" not in alias:
                raise GlossaryValidationError(f"Некорректный alias в записи #{index + 1}")
            normalized = validate_alias_options(
                alias["alias"],
                kind=entry["kind"],
                auto_expand=bool(alias.get("auto_expand", False)),
                search_enabled=bool(alias.get("search_enabled", False)),
            )
            if normalized in local_aliases or normalized in aliases:
                raise GlossaryValidationError(f"Коллизия alias в seed: {alias['alias']}")
            local_aliases.add(normalized)
            aliases.add(normalized)


def seed_glossary(registry: GlossaryRegistry, entries: Iterable[dict], *, apply: bool = False) -> dict:
    """Validate and apply a seed without overwriting or re-enabling records.

    Existing records are classified before any write.  A source conflict aborts
    the whole apply, while exact matches are safe no-ops.  This makes a small
    seed repeatable and prevents a mixed seed from leaving a half-applied set.
    """
    rows = list(entries)
    validate_seed(rows)
    existing = {row["canonical"]: row for row in registry.list()}
    report = {"created": 0, "would_create": 0, "unchanged": 0, "conflict": 0}
    conflicts = []
    for entry in rows:
        current = existing.get(normalize_canonical(entry["canonical"], entry["kind"]))
        if current is None:
            report["would_create"] += 1
            continue
        expected = {
            "kind": entry["kind"],
            "original_name": entry["original_name"],
            "original_description": entry.get("original_description"),
            "canonical_locale": entry["canonical_locale"],
        }
        if any(current.get(key) != value for key, value in expected.items()):
            report["conflict"] += 1
            conflicts.append(entry["canonical"])
        else:
            report["unchanged"] += 1
    if conflicts or not apply:
        return report

    for entry in rows:
        canonical = normalize_canonical(entry["canonical"], entry["kind"])
        if canonical in existing:
            continue
        registry.create(
            canonical,
            entry["kind"],
            entry["original_name"],
            entry.get("original_description"),
            entry["canonical_locale"],
            "system:glossary_seed",
            aliases=[
                GlossaryAliasInput(
                    alias=item["alias"],
                    locale=item.get("locale"),
                    auto_expand=bool(item.get("auto_expand", False)),
                    search_enabled=bool(item.get("search_enabled", False)),
                    created_by="system:glossary_seed",
                )
                for item in entry["aliases"]
            ],
            audit_username="system:glossary_seed",
        )
        report["created"] += 1
    report["would_create"] = 0
    return report
