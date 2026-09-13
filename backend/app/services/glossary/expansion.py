"""Deterministic query-side matching and glossary expansion."""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any

from app.config import get_settings
from app.services.glossary.normalization import (
    literal_matches,
    normalize_alias,
    normalize_query_with_mapping,
)
from app.services.glossary.registry import get_glossary_registry
from app.services.glossary.types import (
    AppliedTerm,
    GlossaryTermSnapshot,
    MatchGroup,
    MatchSpan,
    QueryPlan,
    SkippedReason,
)

_QUERY_RULES_VERSION = "glossary-v2-aliases"
_TOKEN_RE = re.compile(r"[a-zа-яё0-9ßà-öø-ÿā-žșț]+", re.IGNORECASE)


@dataclass(frozen=True)
class _Candidate:
    term: GlossaryTermSnapshot
    start: int
    end: int
    matched_text: str
    match_type: str
    source_form: str


def load_glossary_snapshot() -> tuple[GlossaryTermSnapshot, ...]:
    """Load the one eager registry snapshot used by a query plan."""
    return get_glossary_registry().snapshot()


def _setting(settings: Any, name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _match_text(normalized, start: int, end: int, original: str) -> tuple[int, int, str]:
    source_start, source_end = normalized.source_span(start, end)
    return source_start, source_end, original[source_start:source_end]


def _alias_candidates(
    term: GlossaryTermSnapshot,
    normalized,
    original_query: str,
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for alias in term.aliases:
        if not alias.auto_expand or alias.is_conflicting:
            continue
        # Most aliases cannot occur in a short query.  Avoid compiling and
        # scanning one regex per alias; the exact boundary check below is still
        # the source of truth for the few literal candidates that survive.
        if alias.normalized_alias not in normalized.text:
            continue
        try:
            matches = literal_matches(normalized, alias.normalized_alias)
        except (TypeError, ValueError):
            # A damaged row must not prevent the rest of the active registry
            # from being useful.  Registry writes validate this path, but the
            # guard also covers hand-edited/imported dev databases.
            continue
        for start, end in matches:
            source_start, source_end, matched_text = _match_text(
                normalized, start, end, original_query
            )
            candidates.append(
                _Candidate(
                    term=term,
                    start=source_start,
                    end=source_end,
                    matched_text=matched_text,
                    match_type="alias",
                    source_form=alias.alias,
                )
            )
    return candidates


def _collect_candidates(
    snapshot: tuple[GlossaryTermSnapshot, ...],
    original_query: str,
) -> list[_Candidate]:
    normalized = normalize_query_with_mapping(original_query)
    candidates: list[_Candidate] = []
    for term in snapshot:
        if not term.enabled:
            continue
        candidates.extend(_alias_candidates(term, normalized, original_query))

    # A longer verified literal wins over a structural or shorter overlapping
    # candidate.  Stable term/id ordering makes snapshots deterministic across
    # database row order and Python hash randomization.
    candidates.sort(
        key=lambda item: (
            -(item.end - item.start),
            item.start,
            0 if item.match_type == "alias" else 1,
            item.term.term_id,
            normalize_alias(item.source_form),
        )
    )
    accepted: list[_Candidate] = []
    for candidate in candidates:
        if any(candidate.start < item.end and item.start < candidate.end for item in accepted):
            continue
        accepted.append(candidate)
    return sorted(accepted, key=lambda item: (item.start, item.end, item.term.term_id))


def _group_candidates(candidates: list[_Candidate]) -> list[MatchGroup]:
    grouped: dict[int, list[_Candidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.term.term_id, []).append(candidate)

    groups: list[MatchGroup] = []
    for items in grouped.values():
        items.sort(key=lambda item: (item.start, item.end, item.source_form))
        term = items[0].term
        types = {item.match_type for item in items}
        match_type = "mixed" if len(types) > 1 else next(iter(types))
        forms = tuple(dict.fromkeys(item.source_form for item in items))
        spans = tuple(
            MatchSpan(
                start=item.start,
                end=item.end,
                matched_text=item.matched_text,
                match_type=item.match_type,
                source_form=item.source_form,
            )
            for item in items
        )
        groups.append(
            MatchGroup(
                term_id=term.term_id,
                canonical=term.canonical,
                kind=term.kind,
                canonical_locale=term.canonical_locale,
                original_name=term.original_name,
                term_version=term.version,
                source_revision=term.source_revision,
                spans=spans,
                matched_forms=forms,
                match_type=match_type,
            )
        )
    return sorted(groups, key=lambda group: (group.spans[0].start, group.term_id))


def _display(term: GlossaryTermSnapshot, ui_locale: str) -> tuple[str, str, bool, bool]:
    locale = (ui_locale or "").strip()
    if locale == term.canonical_locale:
        return term.original_name, term.canonical_locale, False, False
    for translation in term.translations:
        if translation.locale == locale and translation.source_revision == term.source_revision:
            return translation.display_name, locale, translation.is_machine_translated, False
    return term.original_name, term.canonical_locale, False, True


def _token_count(text: str) -> int:
    return len(_TOKEN_RE.findall(text.lower()))


def _clean_marker_name(name: str) -> str:
    return "".join(char if char >= " " else " " for char in name).strip()


def _build_plan(
    original_query: str,
    ui_locale: str,
    groups: list[MatchGroup],
    terms_by_id: dict[int, GlossaryTermSnapshot],
    settings: Any,
) -> QueryPlan:
    max_aliases = int(_setting(settings, "glossary_max_added_aliases_per_term", 4))
    max_tokens = int(_setting(settings, "glossary_max_added_tokens", 32))
    max_chars = int(_setting(settings, "glossary_max_added_chars", 768))
    max_query_chars = int(_setting(settings, "glossary_query_text_max_chars", 8192))
    limited = False
    sparse_texts: list[str] = []
    seen_forms: set[str] = set()
    added_tokens = 0
    added_chars = 0
    dense_query = original_query
    applied_groups: list[MatchGroup] = []
    applied_terms: list[AppliedTerm] = []
    skipped_reasons: list[SkippedReason] = []

    for group in groups:
        term = terms_by_id[group.term_id]
        aliases = [
            alias
            for alias in term.aliases
            if alias.search_enabled and not alias.is_conflicting
        ]
        aliases.sort(key=lambda alias: (alias.normalized_alias, alias.alias_id))
        if len(aliases) > max_aliases:
            limited = True
            skipped_reasons.append(
                SkippedReason(term.term_id, term.canonical, "max_added_aliases_per_term")
            )
        aliases = aliases[:max_aliases]
        forms = [alias.alias for alias in aliases]
        accepted_forms: list[str] = []
        for form in forms:
            normalized_form = normalize_alias(form)
            if not normalized_form or normalized_form in seen_forms:
                continue
            form_tokens = _token_count(form)
            form_chars = len(form)
            if added_tokens + form_tokens > max_tokens or added_chars + form_chars > max_chars:
                limited = True
                reason = (
                    "max_added_tokens"
                    if added_tokens + form_tokens > max_tokens
                    else "max_added_chars"
                )
                skipped_reasons.append(
                    SkippedReason(term.term_id, term.canonical, reason, form)
                )
                continue
            accepted_forms.append(form)
            seen_forms.add(normalized_form)
            sparse_texts.append(form)
            added_tokens += form_tokens
            added_chars += form_chars

        marker = f"\n[Domain term: {_clean_marker_name(term.original_name)}]"
        if len(dense_query) + len(marker) <= max_query_chars:
            dense_query += marker
        else:
            limited = True
            skipped_reasons.append(
                SkippedReason(term.term_id, term.canonical, "max_query_text_chars")
            )

        # MatchGroup is also the context-side equivalence contract. Keep the
        # bounded forms that were actually admitted to this plan, while the
        # public AppliedTerm retains the original user spans separately.
        applied_groups.append(replace(group, matched_forms=tuple(accepted_forms)))
        display_name, display_locale, machine, fallback = _display(term, ui_locale)
        matched_texts = tuple(dict.fromkeys(span.matched_text for span in group.spans))
        applied_terms.append(
            AppliedTerm(
                term_id=term.term_id,
                canonical=term.canonical,
                kind=term.kind,
                display_name=display_name,
                display_locale=display_locale,
                display_is_machine_translated=machine,
                display_is_fallback=fallback,
                canonical_locale=term.canonical_locale,
                term_version=term.version,
                source_revision=term.source_revision,
                matched_texts=matched_texts,
                match_type=group.match_type,
                added_forms=tuple(accepted_forms),
            )
        )

    if not applied_terms:
        return QueryPlan(
            original_query=original_query,
            dense_query=original_query,
            added_sparse_texts=(),
            match_groups=(),
            applied_terms=(),
            status="no_match",
            skipped_reasons=tuple(skipped_reasons),
            rules_version=_QUERY_RULES_VERSION,
        )
    return QueryPlan(
        original_query=original_query,
        dense_query=dense_query,
        added_sparse_texts=tuple(sparse_texts),
        match_groups=tuple(applied_groups),
        applied_terms=tuple(applied_terms),
        status="limited" if limited else "applied",
        skipped_reasons=tuple(skipped_reasons),
        rules_version=_QUERY_RULES_VERSION,
    )


def prepare_query(
    query: str,
    *,
    ui_locale: str,
    enabled: bool,
    settings: Any | None = None,
) -> QueryPlan:
    """Build an immutable retrieval plan without recursive or fuzzy matching."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if not enabled:
        return QueryPlan(
            original_query=query,
            dense_query=query,
            added_sparse_texts=(),
            match_groups=(),
            applied_terms=(),
            status="disabled",
            rules_version=_QUERY_RULES_VERSION,
        )

    settings = settings or get_settings()
    try:
        snapshot = load_glossary_snapshot()
    except Exception:
        return QueryPlan(
            original_query=query,
            dense_query=query,
            added_sparse_texts=(),
            match_groups=(),
            applied_terms=(),
            status="unavailable",
            rules_version=_QUERY_RULES_VERSION,
        )

    candidates = _collect_candidates(snapshot, query)
    normalized = normalize_query_with_mapping(query)
    ambiguous = tuple(
        SkippedReason(term.term_id, term.canonical, "ambiguous_alias", alias.alias)
        for term in snapshot if term.enabled
        for alias in term.aliases
        if alias.is_conflicting and alias.auto_expand
        and literal_matches(normalized, alias.normalized_alias)
    )
    groups = _group_candidates(candidates)
    if not groups:
        return QueryPlan(
            original_query=query,
            dense_query=query,
            added_sparse_texts=(),
            match_groups=(),
            applied_terms=(),
            status="no_match",
            skipped_reasons=ambiguous,
            rules_version=_QUERY_RULES_VERSION,
        )

    max_terms = int(_setting(settings, "glossary_max_terms_per_query", 5))
    limited = len(groups) > max_terms
    selected_groups = groups[:max_terms]
    skipped_reasons = ambiguous + tuple(
        SkippedReason(group.term_id, group.canonical, "max_terms_per_query")
        for group in groups[max_terms:]
    )
    plan = _build_plan(
        query,
        ui_locale,
        selected_groups,
        {term.term_id: term for term in snapshot},
        settings,
    )
    if limited and plan.status == "applied":
        return QueryPlan(
            original_query=plan.original_query,
            dense_query=plan.dense_query,
            added_sparse_texts=plan.added_sparse_texts,
            match_groups=plan.match_groups,
            applied_terms=plan.applied_terms,
            status="limited",
            skipped_reasons=skipped_reasons + plan.skipped_reasons,
            rules_version=plan.rules_version,
        )
    if skipped_reasons:
        return QueryPlan(
            original_query=plan.original_query,
            dense_query=plan.dense_query,
            added_sparse_texts=plan.added_sparse_texts,
            match_groups=plan.match_groups,
            applied_terms=plan.applied_terms,
            status=plan.status,
            skipped_reasons=skipped_reasons + plan.skipped_reasons,
            rules_version=plan.rules_version,
        )
    return plan
