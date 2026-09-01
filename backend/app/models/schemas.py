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
    development_id: int | None = None
    development_number: str | None = None
    development_name: str | None = None
    development_module: str | None = None
    development_confidence: float | None = None
    development_confirmed_by: str | None = None
    development_suggestion: dict[str, Any] | None = None
    # True — синхронный реиндекс dev_tags не удался, обновление поставлено в фоновую
    # очередь. Одноразовый флаг в ответе на привязку разработки (не состояние БД).
    dev_tags_sync_pending: bool = False
    # Есть почти-дубликаты (уровень 2/3 дедупликации).
    has_duplicates: bool = False
    # Не None — у загруженного файла есть близнец (SHA-256) в корзине: загрузка
    # разрешена (близнец не блокирует), информация — для тоста пользователю.
    duplicate_in_trash: dict[str, Any] | None = None
    # Корзина (Этап 4a.2): не None — документ удалён и находится в корзине.
    deleted_at: datetime | None = None
    deleted_by: str | None = None


class TrashItemOut(DocumentOut):
    """Элемент корзины: документ + индикация срока до окончательного удаления."""

    days_left: int = 0
    purge_at: datetime | None = None


class TrashListOut(BaseModel):
    documents: list[TrashItemOut]
    total: int = 0
    limit: int | None = None
    offset: int = 0
    retention_days: int = 14


class DocumentListOut(BaseModel):
    documents: list[DocumentOut]
    total: int = 0
    limit: int | None = None
    offset: int = 0


class DocumentStatsOut(BaseModel):
    total: int = 0
    with_development: int = 0


class UploaderListOut(BaseModel):
    uploaders: list[str]


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
    query: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=50)
    mode: SearchMode | None = None
    dense: bool | None = None
    bm25: bool | None = None
    # Клиентский UUID треда (Этап 6). Если не задан — бэкенд создаёт новую сессию.
    session_id: str | None = None


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
    # Бейджи модуль/разработка в источниках (Этап 5.1).
    development_number: str | None = None
    development_name: str | None = None
    development_module: str | None = None


class ChatResponse(BaseModel):
    query: str
    answer: str
    sources: list[ChatSource]
    # UUID треда, к которому относится обмен (для продолжения «Нового чата»).
    session_id: str | None = None


class ChatHistoryMessageOut(BaseModel):
    role: str
    content: str
    sources: list[ChatSource] = Field(default_factory=list)
    created_at: datetime


class ChatHistorySessionOut(BaseModel):
    session_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    deleted_at: datetime | None = None


class ChatHistoryListOut(BaseModel):
    sessions: list[ChatHistorySessionOut]
    total: int = 0
    limit: int | None = None
    offset: int = 0


class ChatHistoryThreadOut(BaseModel):
    session_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[ChatHistoryMessageOut]
    deleted_at: datetime | None = None


class ChatAdminUserOut(BaseModel):
    user_id: str
    username: str | None = None
    session_count: int = 0


class ChatAdminUserListOut(BaseModel):
    users: list[ChatAdminUserOut]


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


class DocumentTagsUpdate(BaseModel):
    """Полная замена набора глобальных тегов документа (Этап 4a)."""

    tags: list[str] = Field(default_factory=list)


class BulkTagsRequest(BaseModel):
    """Массовое редактирование тегов (Этап 4a): delta add/remove по списку документов."""

    doc_ids: list[str]
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


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


class DevelopmentOut(BaseModel):
    id: int
    number: str
    name: str
    module: str | None = None
    version: int
    created_at: datetime
    created_by: str | None = None
    documents_count: int = 0


class DevelopmentListOut(BaseModel):
    developments: list[DevelopmentOut]
    total: int
    limit: int | None = None
    offset: int = 0


class DevelopmentCreate(BaseModel):
    number: str
    name: str
    module: str | None = None


class DevelopmentUpdate(BaseModel):
    number: str | None = None
    name: str | None = None
    module: str | None = None
    version: int


class DocumentDevelopmentSet(BaseModel):
    development_id: int | None = None
    confirmed: bool = False


class DetectDevelopmentOut(BaseModel):
    development_id: int | None = None
    number: str | None = None
    name: str | None = None
    module: str | None = None
    confidence: float | None = None
    matched: bool = False


class AttributeValueOut(BaseModel):
    id: int
    attribute_key: str
    value: str
    label: str | None = None
    sort_order: int = 0
    org_id: int | None = None
    created_by: str | None = None


class AttributeListOut(BaseModel):
    values: list[AttributeValueOut]


class AttributeCreate(BaseModel):
    value: str
    label: str | None = None
    sort_order: int = 0
