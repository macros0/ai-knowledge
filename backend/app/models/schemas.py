from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.problem_codes import problem_message

SearchMode = Literal["dense", "bm25", "hybrid"]
QueryText = Annotated[str, Field(min_length=1, max_length=8192)]
FilterValue = Annotated[str, Field(min_length=1, max_length=128)]
ReferenceLocale = Annotated[str, Field(pattern=r"^[a-z]{2,3}(-[a-z0-9]{2,8})*$", max_length=16)]


def _normalize_query(value: str) -> str:
    """Normalize user text before it reaches embedding, Qdrant or the LLM."""
    value = value.strip()
    if not value:
        raise ValueError("query must not be blank")
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in value):
        raise ValueError("query contains control characters")
    return value


def _normalize_filter_values(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized:
            raise ValueError("filter values must not be blank")
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


class DocumentOut(BaseModel):
    id: str
    filename: str
    content_type: str
    size: int
    status: str
    error: str | None = None
    error_code: str | None = None
    # Диагностический код неполноты при зелёном done (services/problem_codes.py).
    problem: str | None = None
    # Человекочитаемое объяснение problem-кода — вычисляется из кода.
    problem_message: str | None = None
    okf_concept_count: int = 0
    # Latest actual generation timestamp among this document's saved concepts.
    concepts_generated_at: datetime | None = None
    total_chunks: int = 0
    processed_chunks: int = 0
    current_chunk: int | None = None
    # Zero-based indices recoverable from retained generation checkpoints.
    partial_chunks: list[int] = Field(default_factory=list)
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
    # Язык исходного документа (Этап 7 фаза D, py3langid; None — пустой текст,
    # 'en' — нейтральный fallback). 'manual' признак — в source_locale_source.
    source_locale: str | None = None
    # Источник source_locale: 'detected' | 'manual' | None.
    source_locale_source: str | None = None
    # True — синхронный реиндекс source_locale в Qdrant не удался, обновление
    # поставлено в фоновую очередь. Одноразовый флаг в ответе на правку языка.
    source_locale_sync_pending: bool = False

    @model_validator(mode="after")
    def _fill_problem_message(self) -> "DocumentOut":
        """problem_message вычисляется из problem-кода (один источник текста)."""
        if self.problem and not self.problem_message:
            self.problem_message = problem_message(self.problem)
        return self


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


class SourceLocaleFacetItem(BaseModel):
    code: str | None = None
    count: int = 0


class SourceLocaleFacetsOut(BaseModel):
    items: list[SourceLocaleFacetItem] = Field(default_factory=list)


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
    query: QueryText
    locale: str = "ru"
    use_glossary: bool = True
    tags: list[FilterValue] = Field(default_factory=list, max_length=50)
    top_k: int = Field(default=5, ge=1, le=50)
    mode: SearchMode | None = None
    dense: bool | None = None
    bm25: bool | None = None
    # Фильтр по языку документа (Этап 7 фаза D). Пустой список = фильтр не задан;
    # include_unknown_source_locale=true добавляет документы с NULL-языком (OR).
    source_locales: list[FilterValue] = Field(default_factory=list, max_length=20)
    include_unknown_source_locale: bool = False

    _normalize_query = field_validator("query", mode="before")(_normalize_query)
    _normalize_filters = field_validator("tags", "source_locales", mode="after")(
        _normalize_filter_values
    )


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
    expansion_status: str = "disabled"
    applied_terms: list[dict[str, Any]] = Field(default_factory=list)


class ChatRequest(BaseModel):
    query: QueryText
    # Язык ответа при неопределимом языке короткого запроса; UI передаёт
    # текущую локаль, прямые API-вызовы получают русский fallback.
    locale: str = "ru"
    use_glossary: bool = True
    tags: list[FilterValue] = Field(default_factory=list, max_length=50)
    top_k: int = Field(default=5, ge=1, le=50)
    mode: SearchMode | None = None
    dense: bool | None = None
    bm25: bool | None = None
    # Фильтр по языку документа (Этап 7 фаза D) — применяется ДО поиска в Qdrant
    # (pre-filter), комбинируется AND с tags/dev_tags. Пустой список = фильтр не
    # задан; include_unknown_source_locale=true добавляет документы с NULL (OR).
    source_locales: list[FilterValue] = Field(default_factory=list, max_length=20)
    include_unknown_source_locale: bool = False
    # Клиентский UUID треда (Этап 6). Если не задан — бэкенд создаёт новую сессию.
    session_id: str | None = None

    _normalize_query = field_validator("query", mode="before")(_normalize_query)
    _normalize_filters = field_validator("tags", "source_locales", mode="after")(
        _normalize_filter_values
    )


class ChatSettingsOut(BaseModel):
    knowledge_profile: str = "Основной контур"
    top_k_min: int
    top_k_max: int
    top_k_default: int
    top_k_presets: list[int]
    search_mode_default: str
    search_modes: list[str]
    search_index_chunks_enabled: bool = True
    glossary_query_expansion_enabled: bool = False
    # Провайдер/модель переводов справочников (Этап 7) — для диагностики в UI
    # бэкфилла (внутренний инструмент, конфиг не секрет).
    translation_provider: str | None = None
    translation_model: str | None = None
    # Export capabilities are intentionally limited to client-safe controls.
    # Quotas, storage reserve, and rate limits remain server-side only.
    bulk_export_enabled: bool = False
    bulk_export_download_enabled: bool = False
    bulk_export_max_docs: int = 1000


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
    expansion_status: str = "disabled"
    applied_terms: list[dict[str, Any]] = Field(default_factory=list)


class ChatHistoryMessageOut(BaseModel):
    role: str
    content: str
    sources: list[ChatSource] = Field(default_factory=list)
    retrieval_metadata: dict[str, Any] | None = None
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


class SourceSpan(BaseModel):
    """Verified source range within the canonical text of one document chunk."""

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    chunk_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceLocationSpanOut(BaseModel):
    start: int
    end: int
    quote: str


class SourceLocationOut(BaseModel):
    status: Literal["exact", "recovered", "chunk", "unavailable"]
    chunk_index: int | None = None
    spans: list[SourceLocationSpanOut] = Field(default_factory=list)


class DocumentTextChunkOut(BaseModel):
    chunk_index: int
    content: str


class Concept(BaseModel):
    id: str = ""
    title: str
    type: str = "concept"
    tags: list[str] = Field(default_factory=list)
    content: str
    relations: list[str] = Field(default_factory=list)
    source_spans: list[SourceSpan] = Field(default_factory=list)
    source_quotes: list[str] = Field(default_factory=list)


class OkfDocument(BaseModel):
    filepath: str
    metadata: dict[str, Any]
    content: str
    markdown: str


class TagTranslationOut(BaseModel):
    locale: str
    text: str
    is_machine_translated: bool = False
    reviewed_by: str | None = None
    translated_at: datetime | None = None


class TagOut(BaseModel):
    id: int
    canonical_locale: str = "und"
    name: str
    display: str
    count: int
    needs_review: bool = False
    translations: list[TagTranslationOut] = Field(default_factory=list)


class TagListOut(BaseModel):
    tags: list[TagOut]


class TagTranslationUpdate(BaseModel):
    locale: str
    text: str = Field(min_length=1, max_length=255)


class TagBulkReviewRequest(BaseModel):
    tag_ids: list[int]


class TagTranslationBackfillRequest(BaseModel):
    locale: str
    entities: list[str] = ["tags", "developments", "attributes"]


class UiDictionaryImportRequest(BaseModel):
    data: dict[str, Any]
    note: str | None = None
    confirm: bool = False


class UiDictionaryPreview(BaseModel):
    total: int = 0
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)


class UiDictionaryImportResult(BaseModel):
    errors: list[str] = Field(default_factory=list)
    applied: bool = False
    version: int | None = None
    preview: UiDictionaryPreview | None = None


class UiDictionaryHistoryEntry(BaseModel):
    id: int
    version: int
    note: str | None = None
    uploaded_by: str | None = None
    created_at: datetime | None = None
    key_count: int = 0


class UiDictionaryHistoryOut(BaseModel):
    entries: list[UiDictionaryHistoryEntry] = Field(default_factory=list)


class UiDictionaryActiveOut(BaseModel):
    locale: str
    version: int
    data: dict[str, Any]


class UiDictionaryAdminOut(BaseModel):
    locale: str
    version: int | None = None
    data: dict[str, Any] | None = None
    note: str | None = None
    uploaded_by: str | None = None
    created_at: datetime | None = None


class TranslationPendingOut(BaseModel):
    locale: str
    pending: dict[str, int]


class BulkOperationRequest(BaseModel):
    doc_ids: list[str]


class DocumentTagsUpdate(BaseModel):
    """Полная замена набора глобальных тегов документа (Этап 4a)."""

    tags: list[str] = Field(default_factory=list)
    canonical_locale: ReferenceLocale | None = None


class BulkTagsRequest(BaseModel):
    """Массовое редактирование тегов (Этап 4a): delta add/remove по списку документов."""

    doc_ids: list[str]
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)
    canonical_locale: ReferenceLocale | None = None


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
    canonical_locale: str = "und"
    number: str
    name: str
    module: str | None = None
    version: int
    created_at: datetime
    created_by: str | None = None
    documents_count: int = 0
    display_name: str | None = None


class DevelopmentListOut(BaseModel):
    developments: list[DevelopmentOut]
    total: int
    limit: int | None = None
    offset: int = 0


class DevelopmentCreate(BaseModel):
    number: str
    name: str
    module: str | None = None
    canonical_locale: ReferenceLocale | None = None


class DevelopmentUpdate(BaseModel):
    number: str | None = None
    name: str | None = None
    module: str | None = None
    version: int


class DocumentDevelopmentSet(BaseModel):
    development_id: int | None = None
    confirmed: bool = False


class DocumentSourceLocaleUpdate(BaseModel):
    """Ручная правка языка исходного документа (Этап 7 фаза D).

    `source_locale=None` сбрасывает и значение, и признак ручной правки
    (документ вернётся под авто-детекцию при следующем regenerate).
    """

    source_locale: str | None = None


class DetectDevelopmentOut(BaseModel):
    development_id: int | None = None
    number: str | None = None
    name: str | None = None
    module: str | None = None
    confidence: float | None = None
    matched: bool = False


class AttributeValueOut(BaseModel):
    id: int
    canonical_locale: str = "und"
    attribute_key: str
    value: str
    label: str | None = None
    sort_order: int = 0
    org_id: int | None = None
    created_by: str | None = None
    display_label: str | None = None


class AttributeListOut(BaseModel):
    values: list[AttributeValueOut]


class AttributeCreate(BaseModel):
    value: str
    label: str | None = None
    sort_order: int = 0
    canonical_locale: ReferenceLocale | None = None


class LocaleOut(BaseModel):
    code: str
    name: str
    status: str
    ui_dictionary_version: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    stopwords_bm25_count: int = 0
    stopwords_marker_count: int = 0


class LocaleListOut(BaseModel):
    locales: list[LocaleOut]


class ActiveLocaleOut(BaseModel):
    code: str
    name: str


class ActiveLocaleListOut(BaseModel):
    locales: list[ActiveLocaleOut]


class LocaleCreate(BaseModel):
    code: str = Field(min_length=2, max_length=16)
    name: str = Field(min_length=1, max_length=255)


class LocaleUpdate(BaseModel):
    name: str | None = None
    status: str | None = None


class StopwordImportRequest(BaseModel):
    words: list[str] = Field(default_factory=list)
    confirm: bool = False
    # Явное подтверждение ДЕСТРУКТИВНОГО пустого replace (mode=replace + words=[]):
    # без него сервер не применяет очистку всего набора (Этап 7, P2).
    confirm_empty_replace: bool = False


class StopwordImportResult(BaseModel):
    locale: str
    kind: str
    mode: str
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    total_after: int = 0
    applied: bool = False
    requires_empty_replace_confirmation: bool = False


class StopwordWordOut(BaseModel):
    word: str
    kind: str
    updated_by: str | None = None
    updated_at: datetime | None = None


class StopwordListOut(BaseModel):
    locale: str
    words: list[StopwordWordOut] = Field(default_factory=list)


class StopwordAddRequest(BaseModel):
    word: str = Field(min_length=1, max_length=64)
    kind: Literal["bm25", "marker"] = "bm25"


class StopwordRenameRequest(BaseModel):
    word: str = Field(min_length=1, max_length=64)


class StopwordHistoryEntry(BaseModel):
    id: int
    created_at: datetime | None = None
    username: str | None = None
    kind: str | None = None
    meta: dict[str, Any] | None = None
    words: list[str] = Field(default_factory=list)


class StopwordHistoryOut(BaseModel):
    entries: list[StopwordHistoryEntry] = Field(default_factory=list)


class StopwordRollbackRequest(BaseModel):
    entry_id: int


class StopwordProbeRequest(BaseModel):
    queries: list[str] = Field(default_factory=list)


class StopwordProbeHit(BaseModel):
    title: str
    score: float


class StopwordProbeResult(BaseModel):
    query: str
    hits: list[StopwordProbeHit] = Field(default_factory=list)


class StopwordProbeResponse(BaseModel):
    results: list[StopwordProbeResult] = Field(default_factory=list)
