"""Small immutable DTOs shared by the glossary service layers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GlossaryKind = Literal[
    "sap_infotype",
    "sap_transaction",
    "sap_table",
    "abbreviation",
    "business_term",
]


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


@dataclass(frozen=True)
class MatchSpan:
    start: int
    end: int
    matched_text: str
    match_type: Literal["alias", "structural"]
    source_form: str


@dataclass(frozen=True)
class MatchGroup:
    term_id: int
    canonical: str
    kind: str
    canonical_locale: str
    original_name: str
    term_version: int
    source_revision: int
    spans: tuple[MatchSpan, ...]
    matched_forms: tuple[str, ...]
    match_type: Literal["alias", "structural", "mixed"]


@dataclass(frozen=True)
class AppliedTerm:
    term_id: int
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
