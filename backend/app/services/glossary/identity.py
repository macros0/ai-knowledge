"""Literal identity keys and conflict detection for glossary records."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from app.services.glossary.forms import forms_for_number
from app.services.glossary.normalization import (
    GlossaryValidationError, SUPPORTED_KINDS, normalize_alias, validate_infotype_number,
)
from app.services.glossary.rules import validate_rule
from app.services.glossary.types import InfotypeRuleSnapshot


def identity_keys_for_term(
    term: dict,
    rules: tuple[InfotypeRuleSnapshot, ...] = (),
) -> frozenset[tuple[str, str]]:
    """Return literal and structural keys owned by one detached term dict."""
    result: set[tuple[str, str]] = set()
    name = term.get("original_name")
    if isinstance(name, str) and normalize_alias(name):
        result.add(("literal", normalize_alias(name)))
    for alias in term.get("aliases", ()) or ():
        value = alias.get("alias") if isinstance(alias, dict) else getattr(alias, "alias", None)
        if isinstance(value, str) and normalize_alias(value):
            result.add(("literal", normalize_alias(value)))
    number = term.get("infotype_number")
    if term.get("kind") == "sap_infotype" and isinstance(number, str):
        if len(number) == 4 and number.isascii() and number.isdigit():
            result.add(("infotype", number))
            # Disabled rules reserve the same namespace; only query matching
            # depends on enabled. Expand existing card numbers, never a range.
            result.update(("literal", form.normalized) for form in forms_for_number(
                number, tuple(replace(rule, enabled=True) for rule in rules)))
    return frozenset(result)


def collect_identity_conflicts(
    terms: tuple[dict, ...],
    rules: tuple[InfotypeRuleSnapshot, ...] = (),
) -> tuple[dict, ...]:
    """Return every pair of different terms sharing an exact identity key."""
    owners: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for term in terms:
        for key in identity_keys_for_term(term, rules):
            owners[key].append(term)
    conflicts: list[dict] = []
    for (key_kind, key_value), rows in owners.items():
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                if left.get("id") == right.get("id"):
                    continue
                conflicts.append(
                    {
                        "key_kind": key_kind,
                        "key_value": key_value,
                        "left_term_id": left.get("id"),
                        "right_term_id": right.get("id"),
                    }
                )
    # Rules reserve their whole namespace, even before a numbered card exists.
    # Match only actual literals; do not expand the 0000-9999 range into rows.
    for term in terms:
        literals = sorted(value for kind, value in identity_keys_for_term(term) if kind == "literal")
        for rule in rules:
            prefixes = validate_rule(rule.number_from, rule.number_to, rule.prefixes, name=rule.name)
            for literal in literals:
                for prefix in prefixes:
                    if not literal.startswith(prefix):
                        continue
                    suffix = literal[len(prefix):]
                    if len(suffix) != 4 or not suffix.isascii() or not suffix.isdigit():
                        continue
                    if not rule.number_from <= int(suffix) <= rule.number_to:
                        continue
                    if term.get("kind") == "sap_infotype" and term.get("infotype_number") == suffix:
                        continue
                    conflicts.append({"key_kind": "infotype_rule", "key_value": literal,
                                      "term_id": term.get("id"), "rule_id": rule.rule_id, "number": suffix})
                    break
    return tuple(conflicts)


class IdentityNamespaceConflictError(GlossaryValidationError):
    def __init__(self, conflicts: tuple[dict, ...]):
        self.conflicts = conflicts
        super().__init__("Пространство идентичностей глоссария содержит конфликты")


def validate_namespace(terms: tuple[dict, ...], rules: tuple[InfotypeRuleSnapshot, ...] = ()) -> None:
    """Validate complete detached metadata before declaring identities ready.

    Includes disabled terms/rules. Raises GlossaryValidationError for invalid
    metadata or IdentityNamespaceConflictError (with .conflicts) for collisions.
    No identifiers are inferred, assigned, or expanded across a number range.
    """
    for term in terms:
        if term.get("kind") not in SUPPORTED_KINDS:
            raise GlossaryValidationError(f"Неизвестный вид термина: {term.get('kind')}")
        validate_infotype_number(term["kind"], term.get("infotype_number"))
    if len(rules) > 200:
        raise GlossaryValidationError("Допускается не более 200 правил инфотипов")
    prefixes = [set(validate_rule(rule.number_from, rule.number_to, rule.prefixes, name=rule.name))
                for rule in rules]
    conflicts = list(collect_identity_conflicts(terms, rules))
    for index, left in enumerate(rules):
        for other_index in range(index + 1, len(rules)):
            right = rules[other_index]
            common = prefixes[index] & prefixes[other_index]
            if common and max(left.number_from, right.number_from) <= min(left.number_to, right.number_to):
                conflicts.append({"key_kind": "rule_overlap", "left_rule_id": left.rule_id,
                                  "right_rule_id": right.rule_id, "prefixes": sorted(common)})
    if conflicts:
        raise IdentityNamespaceConflictError(tuple(conflicts))
