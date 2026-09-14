"""Equivalence-aware matching for already-built glossary query plans."""
from __future__ import annotations

from collections.abc import Iterable
from collections import OrderedDict
from functools import lru_cache
import re
from threading import RLock

from app.services.glossary.normalization import (
    normalize_alias,
    normalize_query_with_mapping,
    technical_span_boundary,
)
from app.services.glossary.types import MatchGroup
from app.services.fusion import Hit

DomainMatchCache = dict[tuple[str, tuple], list[str]]
_GROUP_FORMS: OrderedDict[int, tuple[MatchGroup, tuple[str, ...]]] = OrderedDict()
_GROUP_FORMS_LOCK = RLock()


def _unique_forms(group: MatchGroup) -> tuple[str, ...]:
    """Return only complete, approved forms for one matched term."""
    # MatchGroup and its forms are frozen. Keep the object alongside its id so
    # ids cannot be reused while cached, without recursively hashing hundreds
    # of dataclass fields on every hydration/merge/excerpt check. A newly built
    # query group gets its own entry even when its term id has not changed.
    key = id(group)
    with _GROUP_FORMS_LOCK:
        if key in _GROUP_FORMS:
            _GROUP_FORMS.move_to_end(key)
            return _GROUP_FORMS[key][1]
    forms = list(group.matched_forms)
    forms.extend(form.text for form in group.resolved_forms if form.can_search)
    # The query span is a complete structural/alias form even when it was not
    # copied into ``matched_forms`` by an older QueryPlan producer.
    forms.extend(span.matched_text for span in group.spans)
    # Hash only strings: a MatchGroup contains hundreds of nested source
    # records, making its recursive dataclass hash more costly than matching.
    result = _dedupe_forms(tuple(forms))
    with _GROUP_FORMS_LOCK:
        _GROUP_FORMS[key] = (group, result)
        _GROUP_FORMS.move_to_end(key)
        if len(_GROUP_FORMS) > 128:
            _GROUP_FORMS.popitem(last=False)
    return result


@lru_cache(maxsize=128)
def _dedupe_forms(forms: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for form in forms:
        normalized = normalize_alias(form)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(form)
    return tuple(result)


@lru_cache(maxsize=64)
def _cached_text(text: str) -> str:
    return normalize_alias(text)


def _normalized_text(text: str) -> str:
    # Bound retained text to at most 64 * 16K characters across requests.
    return _cached_text(text) if len(text) <= 16384 else normalize_alias(text)


@lru_cache(maxsize=128)
def _form_pattern(forms: tuple[str, ...], boundary_mode: str) -> re.Pattern:
    # Keep literal prefixes visible to the regex engine's fast search. The
    # iterator advances by one after a match to retain overlapping forms.
    alternatives = sorted({normalize_alias(form) for form in forms if normalize_alias(form)}, key=len, reverse=True)
    branches = []
    for form in alternatives:
        if boundary_mode == "identifier":
            branches.append(re.escape(form) + r"(?![.:-]*[\w/])")
        else:
            after = r"(?!\w)" if form[-1].isalnum() or form[-1] == "_" else ""
            branches.append(re.escape(form) + after)
    return re.compile("(" + "|".join(branches) + ")" if branches else r"(?!)")


def _contains_complete_form(text: str, forms: Iterable[str], *, boundary_mode: str = "phrase") -> bool:
    # Context/title matching never exposes offsets. Avoid constructing the
    # per-character source mapping used only for user-query spans.
    normalized = _normalized_text(text)
    return next(_normalized_spans(normalized, tuple(forms), boundary_mode), None) is not None


def _normalized_spans(normalized: str, forms: tuple[str, ...], boundary_mode: str):
    # Full canonical texts are checked again during merge and excerpt creation.
    # Cache only normalized offsets and immutable literal-form keys; changed
    # aliases, search rights and boundary modes cannot reuse an old decision.
    # Bound both text retention (256 * 16K chars) and stored spans (64 per key).
    if len(normalized) <= 16384:
        spans = _cached_spans(normalized, forms, boundary_mode)
        if spans is not None:
            yield from spans
            return
    yield from _scan_normalized_spans(normalized, forms, boundary_mode)


@lru_cache(maxsize=256)
def _cached_spans(normalized: str, forms: tuple[str, ...], boundary_mode: str):
    result = []
    for span in _scan_normalized_spans(normalized, forms, boundary_mode):
        if len(result) == 64:
            return None
        result.append(span)
    return tuple(result)


def _scan_normalized_spans(normalized: str, forms: tuple[str, ...], boundary_mode: str):
    pattern = _form_pattern(forms, boundary_mode)
    position = 0
    while (match := pattern.search(normalized, position)) is not None:
        start, end = match.span(1)
        position = start + 1
        if boundary_mode == "identifier":
            valid = technical_span_boundary(normalized, start, end)
        else:
            valid = not (start and (normalized[start - 1].isalnum() or normalized[start - 1] == "_")
                         and (normalized[start].isalnum() or normalized[start] == "_"))
        if valid:
            yield start, end


def _contains_system_infotype(text: str, group: MatchGroup) -> bool:
    if group.system_rule != "sap_infotype":
        return False
    return group_form_matches(text, group)


def _via_form(group: MatchGroup) -> str:
    if group.spans:
        return group.spans[0].matched_text
    return group.original_name


def _matched_domain_terms(text: str, match_groups: tuple[MatchGroup, ...]) -> list[str]:
    result: list[str] = []
    for group in match_groups:
        if group_form_matches(text, group):
            result.append(f"{group.original_name} via {_via_form(group)}")
    return result


def _domain_cache_key(text: str, match_groups: tuple[MatchGroup, ...]) -> tuple[str, tuple]:
    return text, tuple(
        (
            group.term_id,
            group.canonical,
            group.system_rule,
            group.structural_code,
            group.matched_forms,
            group.resolved_forms,
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
    return _contains_complete_form(
        text, _unique_forms(group),
        boundary_mode="identifier" if group.kind.startswith("sap_") else "phrase",
    )


def exact_excerpt(text: str, max_chars: int, groups: Iterable[MatchGroup] = ()) -> str:
    """Clip only after matching; keep a complete admitted form in the excerpt."""
    groups = tuple(groups)
    head = text[:max_chars]
    if len(text) <= max_chars or not groups:
        return head
    safe_end = max_chars
    while safe_end > 0 and not technical_span_boundary(text, 0, safe_end):
        safe_end -= 1
    head = text[:safe_end]
    if any(group_form_matches(head, group) for group in groups):
        return head
    normalized = _normalized_text(text)
    lowered = text.lower()
    # Common NFC text has one normalized character per source character.
    # Only build the expensive source map if normalization actually changes
    # offsets (collapsed whitespace, combining marks, etc.).
    same_offsets = len(lowered) == len(text) and (
        normalized == lowered or normalized == re.sub(r"\s", " ", lowered)
    )
    mapped = None if same_offsets else normalize_query_with_mapping(text)
    spans = []
    for group in groups:
        boundary = 'identifier' if group.kind.startswith('sap_') else 'phrase'
        for start, end in _normalized_spans(normalized, _unique_forms(group), boundary):
            source_start, source_end = (start, end) if mapped is None else mapped.source_span(start, end)
            if source_end - source_start <= max_chars:
                spans.append((source_start, source_end))
    # Boundaries belong to the original text. A clipped PA3000 must never
    # become an apparent PA30 at the end of a snippet.
    left = 0
    if spans:
        start, end = min(spans)
        if end > max_chars:
            left = max(0, start - min(80, max_chars - (end - start)))
            while left < start and not technical_span_boundary(text, left, len(text)):
                left += 1
    right = min(len(text), left + max_chars)
    while right > left and not technical_span_boundary(text, 0, right):
        right -= 1
    return text[left:right]


def matching_group_keys(text: str, groups: Iterable[MatchGroup] = ()) -> tuple[str, ...]:
    """Return canonical identity keys whose exact forms occur in ``text``."""
    result = []
    for group in groups:
        if group_form_matches(text, group):
            result.append(group.canonical)
    return tuple(dict.fromkeys(result))


def _structured_identifier_forms(group: MatchGroup) -> tuple[str, ...]:
    """Return only identifier-like forms actually admitted to expansion."""
    return tuple(
        form
        for form in group.matched_forms
        if any(char.isalpha() for char in normalize_alias(form))
        and any(char.isdigit() for char in normalize_alias(form))
    )


def _identifier_group_matches(
    text: str,
    group: MatchGroup,
    identifier_forms: tuple[str, ...],
) -> bool:
    if group.system_rule == "sap_infotype":
        return _contains_system_infotype(text, group)
    return _contains_complete_form(text, identifier_forms, boundary_mode="identifier")


def promote_glossary_identifier_hits(
    hits: list[Hit], match_groups: Iterable[MatchGroup] = ()
) -> list[Hit]:
    """Promote exact added glossary identifiers after ordinary RRF ranking.

    Empty groups are the disabled/no-match path and return the original list
    unchanged.  Complete-form matching retains identifier boundaries, so an
    ``IT0003`` expansion never promotes a block containing only ``IT00037``.
    """
    groups_and_forms = tuple(
        (group, forms)
        for group in match_groups
        if (forms := _structured_identifier_forms(group))
    )
    if not hits or not groups_and_forms:
        return hits

    exact: list[bool] = []
    for hit in hits:
        text = f"{hit.payload.get('title', '')}\n{hit.payload.get('content', '')}"
        exact.append(
            any(
                _identifier_group_matches(text, group, forms)
                for group, forms in groups_and_forms
            )
        )
    if not any(exact):
        return hits

    max_score = max(hit.score for hit in hits)
    promoted = [
        Hit(
            point_id=hit.point_id,
            score=max_score + hit.score if is_exact else hit.score,
            payload=hit.payload,
            rank=hit.rank,
        )
        for hit, is_exact in zip(hits, exact)
    ]
    promoted.sort(key=lambda hit: hit.score, reverse=True)
    return promoted
