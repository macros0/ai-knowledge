"""Deterministic query-side matching and glossary expansion."""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any

from app.config import get_settings
from app.services.glossary.normalization import literal_matches, normalize_alias, normalize_query_with_mapping
from app.services.glossary.forms import forms_for_number, resolve_term_forms, name_is_allowed
from app.services.glossary.rules import match_infotypes
from app.services.glossary.snapshot import load_glossary_snapshot as load_revision_snapshot
from app.services.glossary.snapshot import GlossaryMigrationRequiredError
from app.services.glossary.types import (
    AppliedTerm,
    GlossaryTermSnapshot,
    MatchGroup,
    MatchSpan,
    QueryPlan,
    SkippedReason,
)

_QUERY_RULES_VERSION = "glossary-v4-exact"
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
    """Load terms from the same revision-aware snapshot as query rules."""
    return load_revision_snapshot().terms


def _load_query_snapshot():
    # Terms and rules must come from one revision.  Reading terms through the
    # legacy registry cache and rules through the revision loader can combine a
    # new rule with old terms after another worker commits a glossary mutation.
    return load_revision_snapshot()


def _setting(settings: Any, name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _match_text(normalized, start: int, end: int, original: str) -> tuple[int, int, str]:
    source_start, source_end = normalized.source_span(start, end)
    return source_start, source_end, original[source_start:source_end]


def _alias_candidates(
    term: GlossaryTermSnapshot,
    normalized,
    original_query: str,
    rules=(),
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for form in resolve_term_forms(term, rules):
        if not form.can_trigger:
            continue
        # Most aliases cannot occur in a short query.  Avoid compiling and
        # scanning one regex per alias; the exact boundary check below is still
        # the source of truth for the few literal candidates that survive.
        if form.normalized not in normalized.text:
            continue
        try:
            matches = literal_matches(normalized, form.normalized, boundary_mode=form.boundary_mode)
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
                    match_type="structural" if any(source.kind == "rule_alias" for source in form.sources) else "alias",
                    source_form=form.text,
                )
            )
    return candidates


def _collect_candidates(
    snapshot: tuple[GlossaryTermSnapshot, ...],
    original_query: str,
    rules=(),
) -> list[_Candidate]:
    normalized = normalize_query_with_mapping(original_query)
    candidates: list[_Candidate] = []
    for term in snapshot:
        if not term.enabled:
            continue
        # Structural matching below resolves rules only for numbers in the
        # query. Do not expand every saved infotype against all rules here.
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


def _group_candidates(candidates: list[_Candidate], rules=()) -> list[MatchGroup]:
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
                resolved_forms=resolve_term_forms(term, tuple(rules)),
            )
        )
    return sorted(groups, key=lambda group: (group.spans[0].start, group.term_id))


def _with_configured_infotype_groups(
    snapshot: tuple[GlossaryTermSnapshot, ...],
    rules,
    original_query: str,
    groups: list[MatchGroup],
) -> list[MatchGroup]:
    """Attach configured rule matches, creating a virtual term when needed."""
    by_number = {
        term.infotype_number: term
        for term in snapshot
        if term.infotype_number
    }
    enriched = list(groups)
    for match in match_infotypes(original_query, tuple(rules)):
        term = by_number.get(match.number)
        if term is not None and not term.enabled:
            continue
        start, end, matched_text = match.start, match.end, match.matched_text
        span = MatchSpan(start=start, end=end, matched_text=matched_text, match_type="structural", source_form=matched_text)
        target = next((index for index, group in enumerate(enriched) if (
            group.term_id == term.term_id if term is not None
            else group.term_id is None and group.structural_code == f"IT{match.number}"
        )), None)
        if target is None:
            if term is None:
                enriched.append(MatchGroup(
                    term_id=None, canonical=f"IT{match.number}", kind="sap_infotype", canonical_locale="und",
                    original_name=f"IT{match.number}", term_version=0, source_revision=0,
                    spans=(span,), matched_forms=(matched_text,), match_type="structural",
                    system_rule="sap_infotype", structural_code=f"IT{match.number}",
                    resolved_forms=forms_for_number(match.number, tuple(rules)),
                ))
            else:
                enriched.append(MatchGroup(
                    term_id=term.term_id, canonical=term.canonical, kind=term.kind,
                    canonical_locale=term.canonical_locale, original_name=term.original_name,
                    term_version=term.version, source_revision=term.source_revision,
                    spans=(span,), matched_forms=(matched_text,), match_type="structural",
                    system_rule="sap_infotype", structural_code=f"IT{match.number}",
                    resolved_forms=resolve_term_forms(term, tuple(rules)),
                ))
            continue
        group = enriched[target]
        if any(item.start == start and item.end == end for item in group.spans):
            enriched[target] = replace(
                group,
                system_rule="sap_infotype",
                structural_code=f"IT{match.number}",
                resolved_forms=group.resolved_forms,
            )
            continue
        spans = tuple(sorted(group.spans + (span,), key=lambda item: (item.start, item.end, item.match_type)))
        types = {item.match_type for item in spans}
        enriched[target] = replace(
            group,
            spans=spans,
            matched_forms=tuple(dict.fromkeys(group.matched_forms + (matched_text,))),
            match_type="mixed" if len(types) > 1 else "structural",
            system_rule="sap_infotype",
            structural_code=f"IT{match.number}",
            resolved_forms=group.resolved_forms,
        )
    return sorted(enriched, key=lambda group: (group.spans[0].start, group.term_id is None, group.term_id or 0, group.canonical))


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
    normalized_query = normalize_query_with_mapping(original_query)

    for group in groups:
        term = terms_by_id.get(group.term_id) if group.term_id is not None else None
        resolved = group.resolved_forms or (
            resolve_term_forms(term, ()) if term is not None else ()
        )
        searchable = [form for form in resolved if form.can_search
                      and form.normalized not in seen_forms
                      and not literal_matches(normalized_query, form.normalized, boundary_mode=form.boundary_mode)]
        matched_keys = {normalize_alias(value) for value in group.matched_forms}
        searchable.sort(key=lambda form: 0 if form.normalized in matched_keys else 1)
        if len(searchable) > max_aliases:
            limited = True
            skipped_reasons.append(
                SkippedReason(group.term_id, group.canonical, "max_added_aliases_per_term")
            )
        searchable = searchable[:max_aliases]
        forms = [(form.text, form) for form in searchable]
        accepted_forms: list[str] = []
        matched_resolved = [form for form in resolved if form.can_trigger and literal_matches(
            normalized_query, form.normalized, boundary_mode=form.boundary_mode)]
        accepted_alias_forms = [form.text for form in matched_resolved
                                if any(source.kind == 'explicit_alias' for source in form.sources)]
        accepted_sources = [source for form in matched_resolved for source in form.sources]
        for form, resolved_form in forms:
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
                    SkippedReason(group.term_id, group.canonical, reason, form)
                )
                continue
            accepted_forms.append(form)
            if any(source.kind == "explicit_alias" for source in resolved_form.sources):
                accepted_alias_forms.append(form)
            accepted_sources.extend(resolved_form.sources)
            seen_forms.add(normalized_form)
            sparse_texts.append(form)
            added_tokens += form_tokens
            added_chars += form_chars

        for form in accepted_forms:
            addition = "\n" + form
            if len(dense_query) + len(addition) <= max_query_chars:
                dense_query += addition
            else:
                limited = True
                skipped_reasons.append(
                    SkippedReason(group.term_id, group.canonical, "max_query_text_chars", form)
                )

        # MatchGroup is also the context-side equivalence contract. Keep the
        # bounded forms that were actually admitted to this plan, while the
        # public AppliedTerm retains the original user spans separately.
        applied_groups.append(replace(group, matched_forms=tuple(accepted_forms)))
        if term is not None:
            display_name, display_locale, machine, fallback = _display(term, ui_locale)
        else:
            display_name, display_locale, machine, fallback = (
                group.original_name,
                group.canonical_locale,
                False,
                False,
            )
        matched_texts = tuple(dict.fromkeys(span.matched_text for span in group.spans))
        applied_terms.append(
            AppliedTerm(
                term_id=group.term_id,
                canonical=group.canonical,
                kind=group.kind,
                display_name=display_name,
                display_locale=display_locale,
                display_is_machine_translated=machine,
                display_is_fallback=fallback,
                canonical_locale=group.canonical_locale,
                term_version=group.term_version,
                source_revision=group.source_revision,
                matched_texts=matched_texts,
                match_type=group.match_type,
                added_forms=tuple(accepted_forms),
                system_rule=group.system_rule,
                saved_alias_forms=tuple(accepted_alias_forms),
                form_sources=tuple(dict.fromkeys(accepted_sources)),
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
        revision_snapshot = _load_query_snapshot()
        snapshot = revision_snapshot.terms
        rules = revision_snapshot.rules
    except GlossaryMigrationRequiredError:
        raise
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

    candidates = _collect_candidates(snapshot, query, rules)
    normalized = normalize_query_with_mapping(query)
    ambiguous = tuple(
        SkippedReason(term.term_id, term.canonical, "ambiguous_alias", alias.alias)
        for term in snapshot if term.enabled
        for alias in term.aliases
        if alias.is_conflicting and alias.auto_expand
        and literal_matches(normalized, alias.normalized_alias)
    )
    ambiguous += tuple(SkippedReason(term.term_id, term.canonical, 'unsafe_name', term.original_name)
        for term in snapshot if term.enabled and not name_is_allowed(term.original_name, term.kind)
        and literal_matches(normalized, term.original_name,
                            boundary_mode='identifier' if term.kind.startswith('sap_') else 'phrase'))
    groups = _with_configured_infotype_groups(snapshot, rules, query, _group_candidates(candidates, rules))
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
            glossary_revision=revision_snapshot.revision,
            strict_groups=tuple(groups),
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
            glossary_revision=revision_snapshot.revision,
            strict_groups=tuple(groups),
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
            glossary_revision=revision_snapshot.revision,
            strict_groups=tuple(groups),
        )
    return replace(plan, glossary_revision=revision_snapshot.revision, strict_groups=tuple(groups))
