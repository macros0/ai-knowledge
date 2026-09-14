"""Pure matching and validation for user-configured infotype rules."""
from __future__ import annotations

import re
import unicodedata

from app.services.glossary.normalization import (
    GlossaryInvalidRuleError,
    NormalizedText,
    technical_span_boundary,
    normalize_query_with_mapping,
)
from app.services.glossary.types import InfotypeRuleSnapshot, RuleMatch

_MAX_NUMBER = 9999
_PREFIX_LIMIT = 64
_RULE_NAME_LIMIT = 128
_PREFIX_COUNT_LIMIT = 50


def normalize_rule_prefix(prefix: str) -> str:
    if not isinstance(prefix, str):
        raise GlossaryInvalidRuleError("Префикс должен быть строкой")
    # Rule prefixes are exact literals.  Unlike ordinary aliases, a trailing
    # space is meaningful because it distinguishes e.g. ``IT0003`` from
    # ``IT 0003``.  Collapse repeated whitespace to the same representation
    # used by query normalization, but do not strip the ends.
    normalized = unicodedata.normalize("NFC", prefix).lower().replace("\u0307", "")
    normalized = re.sub(r"\s+", " ", normalized)
    if not normalized or not normalized.strip() or normalized.startswith(" ") or len(normalized) > _PREFIX_LIMIT:
        raise GlossaryInvalidRuleError("Префикс должен быть непустым и не длиннее 64 символов")
    if any(unicodedata.category(char).startswith("C") for char in normalized):
        raise GlossaryInvalidRuleError("Префикс не должен содержать управляющие символы")
    if not any(char.isalpha() for char in normalized):
        raise GlossaryInvalidRuleError("Префикс должен содержать хотя бы одну букву")
    return normalized


def _validate_prefix(prefix: str) -> str:
    return normalize_rule_prefix(prefix)


def validate_rule(
    number_from: int,
    number_to: int,
    prefixes: tuple[str, ...],
    *,
    name: str = "Правило инфотипов",
) -> tuple[str, ...]:
    """Validate a rule and return normalized, order-preserving prefixes."""
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > _RULE_NAME_LIMIT:
        raise GlossaryInvalidRuleError("Название правила должно быть непустым и не длиннее 128 символов")
    if not isinstance(number_from, int) or not isinstance(number_to, int):
        raise GlossaryInvalidRuleError("Границы диапазона должны быть целыми числами")
    if not 0 <= number_from <= number_to <= _MAX_NUMBER:
        raise GlossaryInvalidRuleError("Диапазон должен находиться внутри 0000–9999")
    if not isinstance(prefixes, (tuple, list)) or not 1 <= len(prefixes) <= _PREFIX_COUNT_LIMIT:
        raise GlossaryInvalidRuleError("Правило должно содержать от 1 до 50 префиксов")
    result: list[str] = []
    seen: set[str] = set()
    for prefix in prefixes:
        normalized = _validate_prefix(prefix)
        if normalized in seen:
            raise GlossaryInvalidRuleError("Префиксы правила дублируются после нормализации")
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _span_boundary(text: str, start: int, end: int) -> bool:
    """Require complete identifier boundaries around a generated form."""
    return technical_span_boundary(text, start, end)


def _rule_matches(normalized: NormalizedText, rule: InfotypeRuleSnapshot) -> list[RuleMatch]:
    if not rule.enabled:
        return []
    prefixes = validate_rule(
        rule.number_from,
        rule.number_to,
        rule.prefixes,
        name=rule.name,
    )
    result: list[RuleMatch] = []
    text = normalized.text
    for prefix in prefixes:
        pattern = re.compile(re.escape(prefix) + r"(?P<number>[0-9]{4})")
        for match in pattern.finditer(text):
            start, end = match.span()
            if not _span_boundary(text, start, end):
                continue
            number = match.group("number")
            numeric = int(number)
            if not rule.number_from <= numeric <= rule.number_to:
                continue
            source_start, source_end = normalized.source_span(start, end)
            result.append(
                RuleMatch(
                    start=source_start,
                    end=source_end,
                    matched_text="",
                    number=number,
                    rule_ids=(rule.rule_id,),
                )
            )
    return result


def match_infotypes(
    text: str,
    rules: tuple[InfotypeRuleSnapshot, ...],
) -> tuple[RuleMatch, ...]:
    """Find complete configured prefix+four-digit forms in user text."""
    normalized = normalize_query_with_mapping(text)
    matches: dict[tuple[int, int, str], set[int]] = {}
    for rule in rules:
        for item in _rule_matches(normalized, rule):
            matches.setdefault((item.start, item.end, item.number), set()).update(item.rule_ids)

    result: list[RuleMatch] = []
    for (start, end, number), rule_ids in matches.items():
        result.append(
            RuleMatch(
                start=start,
                end=end,
                matched_text=text[start:end],
                number=number,
                rule_ids=tuple(sorted(rule_ids)),
            )
        )
    return tuple(sorted(result, key=lambda item: (item.start, item.end, item.number)))
