from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field

Hex32 = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
SourceKey = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edit_token: SourceKey | None = None


class ScopeEditLockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edit_token: SourceKey
    activity_sequence: int = Field(default=0, ge=0, le=9007199254740991)


class ScopeSelectionIn(ScopeRequest):
    review_id: Hex32
    expected_revision: int = Field(ge=1)
    selected_source_keys: list[SourceKey] = Field(max_length=1000)


class ScopeReviewIn(ScopeRequest):
    expected_active_generation_id: Hex32 | None = None
    review_id: Hex32 | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    expand_source_keys: list[SourceKey] = Field(default_factory=list, max_length=1000)


class ScopeConfirmIn(ScopeRequest):
    review_id: Hex32
    revision: int = Field(ge=1)
    request_id: Hex32
    similarity_token: str | None = Field(default=None, max_length=128)


class ScopeChangePreviewIn(ScopeRequest):
    review_id: Hex32
    revision: int = Field(ge=1)
    expected_active_generation_id: Hex32


class ScopeChangeApplyIn(ScopeRequest):
    plan_id: Hex32
    request_id: Hex32
    expected_active_generation_id: Hex32


class ProcessingScopeSummary(BaseModel):
    mode: Literal["legacy_all", "all", "selected"]
    generation_id: str | None = None
    counts_version: int | None = None
    total: int | None = None
    included: int | None = None
    excluded: int = 0
    unsupported: int | None = None
    count_complete: bool = False
    selection_hash: str | None = None
    recommended_excluded_count: int | None = None
    chunk_counts: dict[str, int] | None = None


class ProcessingReviewSummary(BaseModel):
    id: str
    revision: int
    state: str
    requires_action: bool
    summary: ProcessingScopeSummary


class ProcessingScopeSnapshot(BaseModel):
    schema_version: Literal[1]
    source_file_hash: SourceKey
    parser_version: str
    assessor_version: str
    inventory: dict[str, Any]
    policy: dict[str, Any]
    traversal_limits: dict[str, Any]
    decisions: list[dict[str, Any]]
    selection_hash: SourceKey
