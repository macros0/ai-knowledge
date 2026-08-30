from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SearchMode = Literal["dense", "bm25", "hybrid"]


class DocumentOut(BaseModel):
    id: str
    filename: str
    content_type: str
    size: int
    status: str
    error: str | None = None
    okf_concept_count: int = 0
    total_chunks: int = 0
    processed_chunks: int = 0
    current_chunk: int | None = None
    tags: list[str] = Field(default_factory=list)
    uploaded_by: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentListOut(BaseModel):
    documents: list[DocumentOut]


class OkfFileOut(BaseModel):
    filename: str
    filepath: str
    title: str
    type: str
    tags: list[str] = Field(default_factory=list)
    size: int
    chunk_index: int | None = None


class ChunkOut(BaseModel):
    index: int
    size: int
    concepts_count: int = 0


class SearchRequest(BaseModel):
    query: str
    tags: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=50)
    mode: SearchMode | None = None
    dense: bool | None = None
    bm25: bool | None = None


class SearchHit(BaseModel):
    score: float
    title: str
    type: str
    point_type: str = "concept"
    tags: list[str] = Field(default_factory=list)
    filepath: str
    snippet: str
    chunk_index: int | None = None
    source_filename: str = ""


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


class ChatRequest(BaseModel):
    query: str
    tags: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=50)
    mode: SearchMode | None = None
    dense: bool | None = None
    bm25: bool | None = None


class ChatSettingsOut(BaseModel):
    top_k_min: int
    top_k_max: int
    top_k_default: int
    top_k_presets: list[int]
    search_mode_default: str
    search_modes: list[str]
    search_index_chunks_enabled: bool = True


class ChatSource(BaseModel):
    title: str
    filepath: str
    score: float
    tags: list[str] = Field(default_factory=list)
    doc_id: str = ""
    filename: str = ""
    snippet: str = ""
    point_type: str = "concept"
    chunk_index: int | None = None


class ChatResponse(BaseModel):
    query: str
    answer: str
    sources: list[ChatSource]


class Concept(BaseModel):
    id: str = ""
    title: str
    type: str = "concept"
    tags: list[str] = Field(default_factory=list)
    content: str
    relations: list[str] = Field(default_factory=list)


class OkfDocument(BaseModel):
    filepath: str
    metadata: dict[str, Any]
    content: str
    markdown: str


class TagOut(BaseModel):
    name: str
    count: int


class TagListOut(BaseModel):
    tags: list[TagOut]


class BulkOperationRequest(BaseModel):
    doc_ids: list[str]


class BulkPreviewOut(BaseModel):
    requested: int
    matched: int
    missing: list[str] = Field(default_factory=list)
    documents: list[dict[str, Any]] = Field(default_factory=list)
    estimated_minutes: float = 0.0


class AuditQueryParams(BaseModel):
    action_type: str | None = None
    user_id: str | None = None
    target_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class BlockUserRequest(BaseModel):
    reason: str | None = None
    expires_at: datetime | None = None
