"""Pydantic contracts for the glossary administration API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.models.schemas import QueryText, ReferenceLocale


GlossaryKind = Literal[
    "sap_infotype",
    "sap_transaction",
    "sap_table",
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
    aliases: list[GlossaryAliasCreate] = Field(default_factory=list, max_length=50)


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
    original_name: str | None = Field(default=None, min_length=1, max_length=256)
    original_description: str | None = Field(default=None, max_length=8000)
    canonical_locale: ReferenceLocale | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _has_changes(self) -> "GlossarySourcePatch":
        if not self.model_fields_set.intersection(
            {"original_name", "original_description", "canonical_locale", "enabled"}
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
    limits: dict[str, Any] = Field(default_factory=dict)
