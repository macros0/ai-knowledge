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
    tags: list[str] = Field(default_factory=list)
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
    mode: SearchMode = "hybrid"


class SearchHit(BaseModel):
    score: float
    title: str
    type: str
    tags: list[str] = Field(default_factory=list)
    filepath: str
    snippet: str


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


class ChatRequest(BaseModel):
    query: str
    tags: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=50)
    mode: SearchMode = "hybrid"


class ChatSettingsOut(BaseModel):
    top_k_min: int
    top_k_max: int
    top_k_default: int
    top_k_presets: list[int]
    search_mode_default: str
    search_modes: list[str]


class ChatSource(BaseModel):
    title: str
    filepath: str
    score: float
    tags: list[str] = Field(default_factory=list)
    doc_id: str = ""
    filename: str = ""
    snippet: str = ""


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
