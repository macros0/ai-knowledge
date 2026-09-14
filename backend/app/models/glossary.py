"""Pydantic contracts for the glossary administration API."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_serializer, model_validator

from app.models.schemas import QueryText, ReferenceLocale


GlossaryKind = Literal[
    "sap_infotype",
    "sap_transaction",
    "sap_table",
    "sap_program",
    "sap_object",
    "abbreviation",
    "business_term",
]


class GlossaryAliasCreate(BaseModel):
    alias: str = Field(min_length=1, max_length=256)
    locale: ReferenceLocale | None = None
    auto_expand: bool = False
    search_enabled: bool = False


class GlossaryTermCreate(BaseModel):
    canonical: str | None = Field(default=None, min_length=1, max_length=128)
    kind: GlossaryKind
    original_name: str = Field(min_length=1, max_length=256)
    original_description: str | None = Field(default=None, max_length=8000)
    canonical_locale: ReferenceLocale = "und"
    infotype_number: str | None = Field(default=None, pattern=r"^[0-9]{4}$")
    aliases: list[GlossaryAliasCreate] = Field(default_factory=list, max_length=50)


class GlossaryConflictCheck(GlossaryTermCreate):
    term_id: int | None = Field(default=None, ge=1)
    enabled: StrictBool = True


class GlossaryTermPatch(BaseModel):
    version: int = Field(ge=1)
    enabled: bool | None = None


class GlossaryAliasAdd(BaseModel):
    version: int = Field(ge=1)
    alias: str = Field(min_length=1, max_length=256)
    locale: ReferenceLocale | None = None
    auto_expand: bool = False
    search_enabled: bool = False


class GlossarySourcePatch(BaseModel):
    version: int = Field(ge=1)
    kind: GlossaryKind | None = None
    infotype_number: str | None = Field(default=None, pattern=r"^[0-9]{4}$")
    original_name: str | None = Field(default=None, min_length=1, max_length=256)
    original_description: str | None = Field(default=None, max_length=8000)
    canonical_locale: ReferenceLocale | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _has_changes(self) -> "GlossarySourcePatch":
        if not self.model_fields_set.intersection(
            {"original_name", "original_description", "canonical_locale", "enabled", "infotype_number", "kind"}
        ):
            raise ValueError("Нужно указать хотя бы одно исходное поле")
        return self


class GlossaryAliasPatch(BaseModel):
    version: int = Field(ge=1)
    alias: str | None = Field(default=None, min_length=1, max_length=256)
    locale: ReferenceLocale | None = None
    auto_expand: bool | None = None
    search_enabled: bool | None = None


class GlossaryPreviewRequest(BaseModel):
    query: QueryText
    locale: ReferenceLocale = "ru"


class GlossaryAliasOut(BaseModel):
    id: int
    term_id: int
    alias: str
    normalized_alias: str
    locale: str | None = None
    auto_expand: bool = False
    search_enabled: bool = False
    created_at: Any
    updated_at: Any
    created_by: str | None = None
    updated_by: str | None = None


class GlossaryTranslationOut(BaseModel):
    locale: str
    display_name: str
    description: str | None = None
    source_revision: int
    version: int = 1
    is_machine_translated: bool = False
    reviewed_by: str | None = None
    reviewed_at: Any = None
    updated_by: str | None = None
    updated_at: Any = None


class GlossaryTermOut(BaseModel):
    id: int
    canonical: str
    kind: GlossaryKind | str
    infotype_number: str | None = None
    original_name: str
    original_description: str | None = None
    canonical_locale: str
    enabled: bool
    version: int
    source_revision: int
    created_at: Any
    updated_at: Any
    created_by: str | None = None
    updated_by: str | None = None
    aliases: list[GlossaryAliasOut] = Field(default_factory=list)
    translations: list[GlossaryTranslationOut] = Field(default_factory=list)
    has_duplicates: bool = False
    alias_conflicts: list[dict[str, Any]] = Field(default_factory=list)


class GlossaryAliasCheck(BaseModel):
    aliases: list[str] = Field(max_length=100)
    term_id: int | None = Field(default=None, ge=1)
    kind: GlossaryKind | None = None
    infotype_number: str | None = Field(default=None, pattern=r"^[0-9]{4}$")

    @model_validator(mode="after")
    def _bounded_aliases(self):
        if any(len(alias) > 256 for alias in self.aliases):
            raise ValueError("Алиас не может быть длиннее 256 символов")
        return self


class GlossaryListOut(BaseModel):
    terms: list[GlossaryTermOut]
    total: int
    limit: int
    offset: int


class GlossaryPendingOut(BaseModel):
    locale: str
    pending: dict[str, int] = Field(default_factory=dict)


class GlossaryTranslationPatch(BaseModel):
    translation_version: int = Field(ge=0)
    source_revision: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=8000)


class GlossaryTranslationReview(BaseModel):
    translation_version: int = Field(ge=1)
    source_revision: int = Field(ge=1)


class GlossaryTranslationBackfillRequest(BaseModel):
    locale: ReferenceLocale
    term_ids: list[int] = Field(min_length=1, max_length=10)
    expected_translation_versions: dict[int, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_expected_versions(self) -> "GlossaryTranslationBackfillRequest":
        if len(set(self.term_ids)) != len(self.term_ids):
            raise ValueError("term_ids не должны повторяться")
        if any(key not in self.term_ids for key in self.expected_translation_versions):
            raise ValueError("expected_translation_versions содержит лишний term_id")
        if any(value < 0 for value in self.expected_translation_versions.values()):
            raise ValueError("Ожидаемая версия перевода не может быть отрицательной")
        return self


class GlossaryPreviewOut(BaseModel):
    original_query: str
    dense_query: str
    added_sparse_texts: list[str] = Field(default_factory=list)
    expansion_status: str
    applied_terms: list[dict[str, Any]] = Field(default_factory=list)
    match_groups: list[dict[str, Any]] = Field(default_factory=list)
    skipped_reasons: list[dict[str, Any]] = Field(default_factory=list)
    glossary_revision: int = 0
    limits: dict[str, Any] = Field(default_factory=dict)


class GlossaryRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    number_from: int = Field(ge=0, le=9999)
    number_to: int = Field(ge=0, le=9999)
    prefixes: list[str] = Field(min_length=1, max_length=50)
    enabled: bool = True


class GlossaryRulePatch(GlossaryRuleCreate):
    version: int = Field(ge=1)


class GlossaryRulePreviewRequest(GlossaryRuleCreate):
    query: QueryText


class GlossaryRulePreviewSpan(BaseModel):
    """Source offsets in Unicode code points; end is exclusive."""
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str


class GlossaryRulePreviewMatch(BaseModel):
    number: str = Field(pattern=r"^[0-9]{4}$")
    code: str = Field(pattern=r"^IT[0-9]{4}$")
    spans: list[GlossaryRulePreviewSpan]
    generated_forms: list[str]


class GlossaryRulePreviewOut(BaseModel):
    query: str
    enabled: bool
    matches: list[GlossaryRulePreviewMatch] = Field(default_factory=list)


class GlossaryRuleOut(BaseModel):
    id: int
    name: str
    number_from: int
    number_to: int
    prefixes: list[str]
    enabled: bool
    version: int
    created_at: Any
    updated_at: Any
    created_by: str | None = None
    updated_by: str | None = None


class GlossaryRuleMergeRequest(BaseModel):
    source_rule_id: int | None = Field(default=None, ge=1)
    target_rule_id: int = Field(ge=1)
    source_version: int | None = Field(default=None, ge=1)
    target_version: int = Field(ge=1)
    draft: GlossaryRuleCreate | None = None
    source_edit: GlossaryRuleCreate | None = None
    preview_digest: str | None = Field(default=None, min_length=64, max_length=64)
    expected_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _one_rule_source(self):
        if (self.source_rule_id is None) == (self.draft is None):
            raise ValueError("Укажите исходное правило или черновик")
        if self.source_edit is not None and self.source_rule_id is None:
            raise ValueError("Изменение правила требует source_rule_id")
        return self


class GlossaryRuleMergePreviewOut(BaseModel):
    source: dict[str, Any]
    target: GlossaryRuleOut
    number_from: int
    number_to: int
    merged_prefixes: list[str]
    glossary_revision: int
    digest: str


class GlossaryMergeAliasChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    normalized_alias: str = Field(min_length=1, max_length=256)
    locale: ReferenceLocale | None
    auto_expand: StrictBool
    search_enabled: StrictBool


class GlossaryMergeTranslationChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locale: ReferenceLocale
    from_term_id: int = Field(ge=1)
    expected_version: int = Field(ge=1)


class GlossaryMergeSelections(BaseModel):
    model_config = ConfigDict(extra="forbid")
    original_name: Literal["source", "target"] | None = None
    original_description: Literal["source", "target"] | None = None
    canonical_locale: Literal["source", "target"] | None = None
    kind: Literal["source", "target"] | None = None
    infotype_number: Literal["source", "target"] | None = None
    enabled: Literal["source", "target"] | None = None
    alias_choices: list[GlossaryMergeAliasChoice] = Field(default_factory=list, max_length=100)
    translation_choices: list[GlossaryMergeTranslationChoice] = Field(default_factory=list, max_length=100)


class GlossaryMergeSourceEdit(GlossaryTermCreate):
    enabled: StrictBool | None = None


class GlossaryMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    source_term_id: int | None = Field(default=None, ge=1)
    draft: GlossaryTermCreate | None = None
    source_edit: GlossaryMergeSourceEdit | None = None
    target_term_id: int = Field(ge=1)
    source_version: int | None = Field(default=None, ge=1)
    target_version: int = Field(ge=1)
    selections: GlossaryMergeSelections = Field(default_factory=GlossaryMergeSelections)
    preview_digest: str | None = Field(default=None, min_length=64, max_length=64)
    expected_revision: int | None = Field(default=None, ge=0)

    @field_serializer("request_id")
    def _uuid_string(self, value):
        return str(value)

    @field_serializer("selections")
    def _explicit_selections(self, value):
        # Omit absent field decisions, but preserve an explicitly selected
        # locale=None inside alias_choices.
        return {key: item for key, item in value.model_dump().items() if item is not None}


    @model_validator(mode="after")
    def _one_source(self):
        if (self.source_term_id is None) == (self.draft is None):
            raise ValueError("Укажите исходную карточку или черновик")
        if self.source_edit is not None and self.source_term_id is None:
            raise ValueError("Изменение исходной карточки требует source_term_id")
        if self.source_term_id is not None and self.source_version is None:
            raise ValueError("Укажите версию исходной карточки")
        return self


class GlossaryMergeCommit(GlossaryMergeRequest):
    preview_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_revision: int = Field(ge=0)


class GlossaryMergeAliasProposal(GlossaryAliasOut):
    # Detached preview aliases have no database id until commit.
    id: int | None = None
    term_id: int | None = None
    created_at: Any = None
    updated_at: Any = None


class GlossaryMergeTermProposal(GlossaryTermOut):
    id: int | None = None
    canonical: str | None = None
    created_at: Any = None
    updated_at: Any = None
    aliases: list[GlossaryMergeAliasProposal] = Field(default_factory=list)


class GlossaryMergePreviewOut(BaseModel):
    source: GlossaryMergeTermProposal
    target: GlossaryMergeTermProposal
    merged: GlossaryMergeTermProposal
    digest: str
    glossary_revision: int = 0
    unresolved_fields: list[str] = Field(default_factory=list)
    result: GlossaryMergeTermProposal
    removed_term_id: int | None = None
    revision: int
    preview_digest: str
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    requires_confirmation: bool = True


class GlossaryRuleCheck(GlossaryRuleCreate):
    exclude_rule_id: int | None = Field(default=None, ge=1)
