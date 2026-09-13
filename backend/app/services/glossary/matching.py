"""Equivalence-aware matching for already-built glossary query plans."""
from __future__ import annotations

from collections.abc import Iterable

from app.services.glossary.normalization import (
    NormalizedText,
    literal_matches,
    normalize_alias,
    normalize_query_with_mapping,
)
from app.services.glossary.types import MatchGroup

DomainMatchCache = dict[tuple[str, tuple], list[str]]


def _unique_forms(group: MatchGroup) -> tuple[str, ...]:
    """Return only complete, approved forms for one matched term."""
    forms = list(group.matched_forms)
    # The query span is a complete structural/alias form even when it was not
    # copied into ``matched_forms`` by an older QueryPlan producer.
    forms.extend(span.matched_text for span in group.spans)
    result: list[str] = []
    seen: set[str] = set()
    for form in forms:
        normalized = normalize_query_with_mapping(form).text
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(form)
    return tuple(result)


def _contains_complete_form(text: str, forms: Iterable[str]) -> bool:
    # Context/title matching never exposes offsets. Avoid constructing the
    # per-character source mapping used only for user-query spans.
    normalized = NormalizedText(normalize_alias(text), (), ())
    return any(literal_matches(normalized, form) for form in forms)


def _via_form(group: MatchGroup) -> str:
    if group.spans:
        return group.spans[0].matched_text
    return group.original_name


def _matched_domain_terms(text: str, match_groups: tuple[MatchGroup, ...]) -> list[str]:
    result: list[str] = []
    for group in match_groups:
        if _contains_complete_form(text, _unique_forms(group)):
            result.append(f"{group.original_name} via {_via_form(group)}")
    return result


def _domain_cache_key(text: str, match_groups: tuple[MatchGroup, ...]) -> tuple[str, tuple]:
    return text, tuple(
        (
            group.term_id,
            group.canonical,
            group.matched_forms,
            tuple((span.matched_text, span.match_type) for span in group.spans),
        )
        for group in match_groups
    )


def _cached_domain_terms(
    text: str,
    match_groups: tuple[MatchGroup, ...],
    cache: DomainMatchCache | None,
) -> list[str]:
    if cache is None:
        return _matched_domain_terms(text, match_groups)
    key = _domain_cache_key(text, match_groups)
    if key not in cache:
        cache[key] = _matched_domain_terms(text, match_groups)
    return cache[key]


def matched_domain_terms(
    item: dict,
    match_groups: Iterable[MatchGroup] = (),
    *,
    cache: DomainMatchCache | None = None,
) -> list[str]:
    """Return domain matches found in a block's title or content.

    A generic word such as ``инфотип`` or a bare number is deliberately not a
    match: only a complete form from the immutable query-plan group qualifies.
    """
    groups = tuple(match_groups)
    if not groups:
        return []
    text = f"{item.get('title', '')}\n{item.get('content', '')}"
    return _cached_domain_terms(text, groups, cache)


def title_matched_domain_terms(
    item: dict,
    match_groups: Iterable[MatchGroup] = (),
    *,
    cache: DomainMatchCache | None = None,
) -> list[str]:
    """Return complete glossary forms found in the block title only."""
    groups = tuple(match_groups)
    if not groups:
        return []
    return _cached_domain_terms(item.get("title", ""), groups, cache)


def group_form_matches(text: str, group: MatchGroup) -> bool:
    """Whether ``text`` contains one complete form of ``group``."""
    return _contains_complete_form(text, _unique_forms(group))
