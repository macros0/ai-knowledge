from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DocumentOut(BaseModel):
    id: str
    filename: str
    content_type: str
    size: int
    status: str
    error: str | None = None
    okf_file_count: int = 0
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


class SearchRequest(BaseModel):
    query: str
    tags: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=50)


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


class ChatSource(BaseModel):
    title: str
    filepath: str
    score: float
    tags: list[str] = Field(default_factory=list)


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
