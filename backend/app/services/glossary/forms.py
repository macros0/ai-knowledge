"""Query-only resolved forms for explicit aliases and infotype rules."""
from __future__ import annotations

from app.services.glossary.normalization import normalize_alias, validate_alias_options, GlossaryValidationError
from dataclasses import replace
from functools import lru_cache
from app.services.glossary.rules import validate_rule
from app.services.glossary.types import (
    FormSource,
    InfotypeRuleSnapshot,
    ResolvedForm,
    GlossaryTermSnapshot,
)
from app.services.glossary.normalization import literal_matches, normalize_query_with_mapping


def forms_for_number(
    number: str,
    rules: tuple[InfotypeRuleSnapshot, ...],
) -> tuple[ResolvedForm, ...]:
    """Build only forms explicitly admitted by enabled rules for ``number``."""
    if not isinstance(number, str) or len(number) != 4 or not number.isascii() or not number.isdigit():
        return ()
    # Bound both entry count and entry size. Large multi-rule namespaces still
    # work, but must not fill a global cache with thousands of forms per number.
    relevant = tuple(rule for rule in rules if rule.enabled and rule.number_from <= int(number) <= rule.number_to)
    if sum(len(rule.prefixes) for rule in relevant) <= 50:
        return _cached_number_forms(number, relevant)
    return _build_number_forms(number, relevant)


@lru_cache(maxsize=128)
def _cached_number_forms(number, rules):
    return _build_number_forms(number, rules)


def _build_number_forms(number, rules):
    numeric = int(number)
    result: list[ResolvedForm] = []
    seen: set[str] = set()
    for rule in rules:
        if not rule.enabled or not rule.number_from <= numeric <= rule.number_to:
            continue
        prefixes = validate_rule(
            rule.number_from,
            rule.number_to,
            rule.prefixes,
            name=rule.name,
        )
        source = FormSource(kind="rule_alias", rule_id=rule.rule_id, rule_version=rule.version)
        for prefix in prefixes:
            text = f"{prefix}{number}"
            normalized = normalize_alias(text)
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append(
                ResolvedForm(
                    text=text,
                    normalized=normalized,
                    identity_key=f"infotype:{number}",
                    boundary_mode="identifier",
                    sources=(source,),
                    can_trigger=True,
                    can_search=True,
                )
            )
    return tuple(result)


@lru_cache(maxsize=2048)
def name_is_allowed(name: str, kind: str) -> bool:
    try:
        validate_alias_options(name, kind=kind, auto_expand=True, search_enabled=True)
        return True
    except GlossaryValidationError:
        return False


def resolve_term_forms(
    term: GlossaryTermSnapshot,
    rules: tuple[InfotypeRuleSnapshot, ...],
) -> tuple[ResolvedForm, ...]:
    """Resolve the exact source name, explicit aliases, and virtual rule forms."""
    result: list[ResolvedForm] = []
    positions: dict[str, int] = {}
    boundary = "identifier" if term.kind.startswith("sap_") else "phrase"

    def add(text: str, source: FormSource, *, can_trigger: bool, can_search: bool, identity: str, boundary: str = boundary):
        normalized = normalize_alias(text)
        if not normalized:
            return
        if normalized in positions:
            index = positions[normalized]
            old = result[index]
            result[index] = replace(old, sources=tuple(dict.fromkeys(old.sources + (source,))),
                                    can_trigger=old.can_trigger or can_trigger,
                                    can_search=old.can_search or can_search)
            return
        positions[normalized] = len(result)
        result.append(ResolvedForm(text, normalized, identity, boundary, (source,), can_trigger, can_search))

    name_allowed = name_is_allowed(term.original_name, term.kind)
    add(term.original_name, FormSource(kind="name"), can_trigger=name_allowed, can_search=name_allowed, identity=f"term:{term.term_id}")
    for alias in term.aliases:
        if alias.is_conflicting:
            continue
        add(alias.alias, FormSource(kind="explicit_alias", alias_id=alias.alias_id), can_trigger=alias.auto_expand, can_search=alias.search_enabled, identity=f"term:{term.term_id}")
    rule_forms = forms_for_number(term.infotype_number, rules) if term.infotype_number else ()
    if rule_forms:
        for form in rule_forms:
            for source in form.sources:
                add(form.text, source, can_trigger=form.can_trigger, can_search=form.can_search,
                    identity=form.identity_key, boundary=form.boundary_mode)
        rule_order = {form.normalized: index for index, form in enumerate(rule_forms)}
        result.sort(key=lambda form: (0, rule_order[form.normalized]) if form.normalized in rule_order else (1, 0))
    return tuple(result)


def find_form_spans(text: str, form: ResolvedForm) -> tuple[tuple[int, int], ...]:
    normalized = normalize_query_with_mapping(text)
    spans = literal_matches(normalized, form.normalized, boundary_mode=form.boundary_mode)
    return tuple(normalized.source_span(start, end) for start, end in spans)
