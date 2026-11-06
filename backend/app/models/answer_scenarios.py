"""User-owned answer instructions and immutable execution snapshots."""
from datetime import datetime
from typing import Literal, Protocol
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ScenarioInstructions(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def instructions(self) -> str: ...


class ScenarioCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1,max_length=120)
    description: str = Field(default="",max_length=1000)
    instructions: str = Field(min_length=1,max_length=12000)
    visibility: Literal["private","public"] = "private"

    @field_validator("name","description","instructions",mode="before")
    @classmethod
    def trim_text(cls,value):
        return value.strip() if isinstance(value,str) else value


class ScenarioUpdate(ScenarioCreate):
    expected_version: int = Field(ge=1,strict=True)


class ScenarioCopy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1,strict=True)


class ScenarioSummary(BaseModel):
    id: str
    owner_id: str
    owner_username: str | None = None
    visibility: Literal["private","public"]
    version: int
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    can_edit: bool


class ScenarioOut(ScenarioSummary):
    instructions: str
    instructions_sha256: str


class ScenarioListOut(BaseModel):
    items: list[ScenarioSummary]
    total: int
    limit: int
    offset: int


class ScenarioSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True,extra="forbid")
    schema_version: Literal[1] = 1
    id: str
    version: int
    name: str
    description: str
    instructions: str
    instructions_sha256: str
    owner_id: str
    owner_username: str | None = None


class ScenarioExecution(BaseModel):
    status: Literal["accepted","applied","no_sources","assessment_rejected","protected_authorship"]
    model_id: str | None = None
    prompt_fingerprint: str | None = None


class ScenarioPreviewSearch(BaseModel):
    model_config=ConfigDict(extra="forbid")
    query: str = Field(min_length=1,max_length=8192)
    locale: str = "ru"
    assess_sources: bool | None = Field(default=None,strict=True)
    mail_mode: Literal["all","exclude","only"] = "all"
    search_depth: int | None = Field(default=None,ge=1,le=500,strict=True)
    search_doc_ids: list[str] | None = Field(default=None,max_length=500)
    tags: list[str] = Field(default_factory=list,max_length=50)
    top_k: int = Field(default=5,ge=1,le=50)
    mode: Literal["dense","bm25","hybrid"] | None = None
    dense: bool | None = None
    bm25: bool | None = None
    use_glossary: bool = True
    source_locales: list[str] = Field(default_factory=list,max_length=20)
    include_unknown_source_locale: bool = False


    @model_validator(mode="after")
    def validate_search(self):
        # Reuse the chat contract before the streaming response starts. The
        # local import avoids the snapshot-schema import cycle at module load.
        from app.models.schemas import ChatRequest
        validated=ChatRequest(**self.model_dump(),response_mode="full")
        for key in type(self).model_fields:
            setattr(self,key,getattr(validated,key))
        return self


class ScenarioPreviewRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    draft: ScenarioCreate
    request: ScenarioPreviewSearch
