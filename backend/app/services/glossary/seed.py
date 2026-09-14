"""Validated, idempotent transport for the initial glossary entries."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable
from dataclasses import replace
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import GlossaryInfotypeRule, GlossaryInfotypePrefix, GlossaryState
from app.db.session import session_scope
from app.services.glossary.rule_registry import GlossaryRuleRegistry
from app.services.glossary.rules import validate_rule
from app.models.glossary import GlossaryTermCreate
from app.services.glossary.identity import validate_namespace

from app.services.glossary.normalization import (
    GlossaryValidationError,
    normalize_canonical,
    normalize_alias,
    validate_alias_options,
    validate_locale,
)
from app.services.glossary.registry import GlossaryRegistry, _reject_redundant_aliases
from app.services.glossary.mutation import glossary_write_session
from app.services.glossary.types import GlossaryAliasInput, InfotypeRuleSnapshot

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
    identities: set[str] = set()
    for index, entry in enumerate(rows):
        if not isinstance(entry, dict):
            raise GlossaryValidationError(f"Запись seed #{index + 1} должна быть объектом")
        for field in ("canonical", "kind", "original_name", "canonical_locale", "aliases"):
            if field not in entry:
                raise GlossaryValidationError(f"В записи seed #{index + 1} нет поля {field}")
        try:
            GlossaryTermCreate.model_validate(entry)
        except ValueError as exc:
            raise GlossaryValidationError(f'Некорректная запись seed #{index + 1}: {exc}') from exc
        canonical = normalize_canonical(entry["canonical"], entry["kind"])
        if canonical in canonicals:
            raise GlossaryValidationError(f"Дублирующийся canonical: {canonical}")
        canonicals.add(canonical)
        normalized_name = normalize_alias(entry["original_name"])
        if not normalized_name:
            raise GlossaryValidationError('Имя термина не может быть пустым')
        if normalized_name in identities:
            raise GlossaryValidationError(f"Коллизия имени в seed: {entry['original_name']}")
        identities.add(normalized_name)
        validate_locale(entry["canonical_locale"])
        if not isinstance(entry["aliases"], list):
            raise GlossaryValidationError(f"aliases записи #{index + 1} должен быть массивом")
        local_aliases = set()
        for alias in entry["aliases"]:
            if not isinstance(alias, dict) or "alias" not in alias:
                raise GlossaryValidationError(f"Некорректный alias в записи #{index + 1}")
            if alias.get('locale') is not None:
                validate_locale(alias['locale'], allow_und=False)
            normalized = validate_alias_options(
                alias["alias"],
                kind=entry["kind"],
                auto_expand=bool(alias.get("auto_expand", False)),
                search_enabled=bool(alias.get("search_enabled", False)),
            )
            if normalized in local_aliases or normalized in identities:
                raise GlossaryValidationError(f"Коллизия alias в seed: {alias['alias']}")
            local_aliases.add(normalized)
            identities.add(normalized)


def seed_glossary(registry: GlossaryRegistry, entries: Iterable[dict], *, apply: bool = False,
                  rules: Iterable[InfotypeRuleSnapshot] = ()) -> dict:
    """Validate and apply a seed without overwriting or re-enabling records.

    Existing records are classified before any write.  A source conflict aborts
    the whole apply, while exact matches are safe no-ops.  This makes a small
    seed repeatable and prevents a mixed seed from leaving a half-applied set.
    """
    rows = list(entries)
    validate_seed(rows)
    configured = tuple(rules)
    if not apply:
        with session_scope() as session:
            return _seed_in_session(registry, rows, apply=False, session=session,
                                    state=session.get(GlossaryState, 1), configured=configured)
    with glossary_write_session() as (session, state):
        return _seed_in_session(registry, rows, apply=True, session=session, state=state, configured=configured)


def _seed_in_session(registry, rows, *, apply, session, state, configured):
    existing = {row["canonical"]: row for row in registry.list(_session=session)}
    report = {"created": 0, "would_create": 0, "unchanged": 0, "conflict": 0,
              "rules_created": 0, "rules_would_create": 0, "rules_unchanged": 0}
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
            "infotype_number": _seed_number(entry),
        }
        if any(current.get(key) != value for key, value in expected.items()) or _alias_values(current) != _alias_values(entry):
            report["conflict"] += 1
            conflicts.append(entry["canonical"])
        else:
            report["unchanged"] += 1
    if conflicts:
        return report

    rules = tuple(InfotypeRuleSnapshot(rule_id=rule.id, name=rule.name,
        number_from=rule.number_from, number_to=rule.number_to, enabled=rule.enabled,
        prefixes=tuple(item.prefix for item in rule.prefixes_rel), version=rule.version)
        for rule in session.scalars(select(GlossaryInfotypeRule).options(
            selectinload(GlossaryInfotypeRule.prefixes_rel))))
    candidates = list(existing.values())
    try:
        pending = []
        for rule in configured:
            prefixes = validate_rule(rule.number_from, rule.number_to, rule.prefixes, name=rule.name)
            proposed = replace(rule, prefixes=prefixes, rule_id=-(len(pending) + 1))
            if any((item.name, item.number_from, item.number_to, item.prefixes) ==
                   (proposed.name, proposed.number_from, proposed.number_to, proposed.prefixes)
                   for item in rules + tuple(pending)):
                report['rules_unchanged'] += 1
                continue
            pending.append(proposed)
        rules += tuple(pending)
        report['rules_would_create'] = len(pending)
        rule_rows = [GlossaryInfotypeRule(number_from=rule.number_from, number_to=rule.number_to,
            prefixes_rel=[GlossaryInfotypePrefix(prefix=prefix) for prefix in rule.prefixes]) for rule in rules]
        for index, entry in enumerate(rows):
            if normalize_canonical(entry['canonical'], entry['kind']) in existing:
                continue
            number = _seed_number(entry)
            _reject_redundant_aliases(session, kind=entry['kind'], number=number,
                name=entry['original_name'], aliases=[item['alias'] for item in entry['aliases']], rules=rule_rows)
            candidates.append({**entry, 'id': -(index + 1), 'infotype_number': number})
        validate_namespace(tuple(candidates), rules)
    except GlossaryValidationError:
        if apply:
            raise
        report['conflict'] += 1
        report['would_create'] = 0
        report['rules_would_create'] = 0
        return report
    if not apply:
        return report

    for rule in pending:
        GlossaryRuleRegistry().create(name=rule.name, number_from=rule.number_from,
            number_to=rule.number_to, prefixes=rule.prefixes, enabled=rule.enabled,
            actor_id='system:glossary_seed', _session=session, _state=state)
        report['rules_created'] += 1
    report['rules_would_create'] = 0

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
            infotype_number=entry.get("infotype_number"),
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
            _session=session,
            _state=state,
        )
        report["created"] += 1
    report["would_create"] = 0
    return report


def _seed_number(entry):
    number = entry.get('infotype_number')
    canonical = normalize_canonical(entry['canonical'], entry['kind'])
    if number is None and entry['kind'] == 'sap_infotype' and re.fullmatch(r'IT[0-9]{4}', canonical):
        number = canonical[2:]
    return number


def _alias_values(entry):
    return {(normalize_alias(item['alias']), item.get('locale'),
             bool(item.get('auto_expand', False)), bool(item.get('search_enabled', False)))
            for item in entry.get('aliases', [])}
