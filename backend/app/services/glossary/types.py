"""Small immutable DTOs shared by the glossary service layers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GlossaryKind = Literal[
    "sap_infotype",
    "sap_transaction",
    "sap_table",
    "sap_program",
    "sap_object",
    "abbreviation",
    "business_term",
]


@dataclass(frozen=True)
class InfotypeRuleSnapshot:
    """Immutable user-configured number range and literal prefixes."""

    rule_id: int
    name: str
    number_from: int
    number_to: int
    prefixes: tuple[str, ...]
    enabled: bool = True
    version: int = 1


@dataclass(frozen=True)
class FormSource:
    """Why a resolved search form is allowed."""

    kind: Literal["name", "explicit_alias", "rule_alias"]
    alias_id: int | None = None
    rule_id: int | None = None
    rule_version: int | None = None


@dataclass(frozen=True)
class ResolvedForm:
    """One complete literal form accepted by the query-side matcher."""

    text: str
    normalized: str
    identity_key: str
    boundary_mode: Literal["phrase", "identifier"]
    sources: tuple[FormSource, ...] = ()
    can_trigger: bool = False
    can_search: bool = False


@dataclass(frozen=True)
class RuleMatch:
    start: int
    end: int
    matched_text: str
    number: str
    rule_ids: tuple[int, ...]


@dataclass(frozen=True)
class GlossarySnapshot:
    revision: int
    terms: tuple["GlossaryTermSnapshot", ...]
    rules: tuple[InfotypeRuleSnapshot, ...] = ()


@dataclass(frozen=True)
class GlossaryAliasInput:
    alias: str
    locale: str | None = None
    auto_expand: bool = False
    search_enabled: bool = False
    created_by: str | None = None


@dataclass(frozen=True)
class GlossaryAliasSnapshot:
    """Detached alias data used after the registry session is closed."""

    alias_id: int
    alias: str
    normalized_alias: str
    locale: str | None
    auto_expand: bool
    search_enabled: bool
    is_conflicting: bool = False


@dataclass(frozen=True)
class GlossaryTranslationSnapshot:
    locale: str
    display_name: str
    source_revision: int
    is_machine_translated: bool


@dataclass(frozen=True)
class GlossaryTermSnapshot:
    """Immutable, eagerly-loaded view of one active glossary term."""

    term_id: int
    canonical: str
    kind: GlossaryKind | str
    original_name: str
    original_description: str | None
    canonical_locale: str
    enabled: bool
    version: int
    source_revision: int
    aliases: tuple[GlossaryAliasSnapshot, ...] = ()
    translations: tuple[GlossaryTranslationSnapshot, ...] = ()
    infotype_number: str | None = None


@dataclass(frozen=True)
class MatchSpan:
    start: int
    end: int
    matched_text: str
    match_type: Literal["alias", "structural"]
    source_form: str


@dataclass(frozen=True)
class MatchGroup:
    term_id: int | None
    canonical: str
    kind: str
    canonical_locale: str
    original_name: str
    term_version: int
    source_revision: int
    spans: tuple[MatchSpan, ...]
    matched_forms: tuple[str, ...]
    match_type: Literal["alias", "structural", "mixed"]
    system_rule: Literal["sap_infotype"] | None = None
    structural_code: str | None = None
    resolved_forms: tuple[ResolvedForm, ...] = ()


@dataclass(frozen=True)
class AppliedTerm:
    term_id: int | None
    canonical: str
    kind: str
    display_name: str
    display_locale: str
    display_is_machine_translated: bool
    display_is_fallback: bool
    canonical_locale: str
    term_version: int
    source_revision: int
    matched_texts: tuple[str, ...]
    match_type: Literal["alias", "structural", "mixed"]
    added_forms: tuple[str, ...]
    used_in: tuple[str, ...] = ("dense", "bm25")
    system_rule: Literal["sap_infotype"] | None = None
    saved_alias_forms: tuple[str, ...] = ()
    form_sources: tuple[FormSource, ...] = ()


@dataclass(frozen=True)
class SkippedReason:
    """A bounded glossary candidate that preview intentionally omitted."""

    term_id: int | None
    canonical: str | None
    reason: str
    form: str | None = None


@dataclass(frozen=True)
class QueryPlan:
    original_query: str
    dense_query: str
    added_sparse_texts: tuple[str, ...]
    match_groups: tuple[MatchGroup, ...]
    applied_terms: tuple[AppliedTerm, ...]
    status: Literal["disabled", "no_match", "applied", "limited", "unavailable"]
    skipped_reasons: tuple[SkippedReason, ...] = ()
    rules_version: str = "glossary-v1"
    glossary_revision: int = 0
    strict_groups: tuple[MatchGroup, ...] = ()
