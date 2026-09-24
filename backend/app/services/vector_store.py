"""Векторное хранилище на Qdrant: dual-index (концепты + чанки), ветки поиска с RRF fusion.

Хранит два типа точек в одной коллекции:
  - point_type="concept" — LLM-выжимки (title, tags, relations, content[:4000]);
  - point_type="chunk"    — сырой текст чанка (content[:8000], chunk_index, section_title).

Ветки поиска (query-time, управляются флагами в config.py):
  - dense    — семантический поиск по dense-вектору (BGE-M3);
  - bm25     — лексический поиск по sparse-вектору (BM25, Qdrant IDF);
  - graph    — expansion: соседи по relations концептов (вес 0.5).

Tags — жёсткий pre-filter для dense и bm25 (MatchAny).
Слияние — RRF (Reciprocal Rank Fusion) в Python (services/fusion.py), не
встроенный Qdrant fusion. Каждая ветка отдаёт per_branch_top_k кандидатов.
"""
import logging
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlparse

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from qdrant_client.http.exceptions import UnexpectedResponse

from app.config import get_settings
from app import error_codes as codes
from app.models.schemas import OkfDocument
from app.services.errors import VectorStoreError, public_error_code
from app.services.fusion import Hit
from app.services.sparse import to_sparse_vector
from app.services.storage import StorageFullError, is_storage_full_text

logger = logging.getLogger(__name__)


class _UpsertStats(TypedDict):
    attempted_batches: int
    successful_batches: int
    failed_batches: int
    attempted_points: int
    indexed_points: int
    failure: VectorStoreError | None


def _qdrant_call(func, *args, **kwargs):
    """Обёртка для Qdrant-вызовов: перехватывает сбои → VectorStoreError.

    Различает два класса отказов (инцидент 03.09.2026: 400/422 от живого
    Qdrant маскировались под «Qdrant недоступен» и сбивали диагностику):
      - UnexpectedResponse (4xx/5xx) — живой Qdrant отклонил ЗАПРОС: дефект
        клиентских данных, в сообщении сохраняются HTTP-код и текст ответа;
      - прочее (сеть/таймауты/разрыв соединения) — сервис действительно
        недоступен.
    """
    try:
        return func(*args, **kwargs)
    except StorageFullError:
        raise
    except VectorStoreError:
        raise
    except UnexpectedResponse as exc:
        detail = ""
        try:
            detail = exc.content.decode("utf-8", errors="replace")[:500]
        except Exception:
            detail = str(exc)
        if is_storage_full_text(detail):
            raise StorageFullError() from exc
        raise VectorStoreError(
            f"Qdrant отклонил запрос (HTTP {exc.status_code}): {detail}",
            cause=exc,
        ) from exc
    except Exception as exc:
        # В gRPC transport Qdrant отдаёт RpcError вместо HTTP body. Проверка
        # текста допустима именно здесь: источник уже установлен как Qdrant.
        details = getattr(exc, "details", None)
        detail = details() if callable(details) else ""
        if is_storage_full_text(detail):
            raise StorageFullError() from exc
        url = None
        try:
            # Именно модульный get_settings (импортирован выше): локальный
            # re-import создавал бы новое имя в области функции и обходил
            # подмену настроек в тестах — сообщение зависело бы от .env
            # окружения, а не от настроек вызова.
            url = get_settings().qdrant_url
        except Exception:
            pass
        port_hint = f" по адресу {url}" if url else ""
        raise VectorStoreError(
            "База знаний (Qdrant) недоступна. "
            f"Проверьте, что Qdrant запущен{port_hint}.",
            cause=exc,
        ) from exc

SPARSE_VECTOR_NAME = "sparse"
SEARCH_MODES = ("dense", "bm25", "hybrid")

# Батч upsert: 256 точек × (1024-мерный dense + sparse + payload) ≈ 6-8 МБ —
# с запасом под серверный лимит Qdrant max_request_size_mb=32 (инцидент
# 03.09.2026: монолитный upsert 5667 концептов → 400 от actix по Content-Length).
UPSERT_BATCH_SIZE = 256

# Размер страницы scroll. Единый для всех обходов коллекции: backfill'ы просят
# либо ключ payload, либо вообще ничего, поэтому страница дешёвая и подбирать
# её под конкретный обход незачем.
SCROLL_PAGE_SIZE = 1000

CONCEPT_POINT_TYPE = "concept"
CHUNK_POINT_TYPE = "chunk"

PAYLOAD_INDEX_FIELDS: dict[str, str] = {
    "point_type": "keyword",
    "doc_id": "keyword",
    "chunk_index": "integer",
    "slug": "keyword",
    "type": "keyword",
    "tags": "keyword",
    "relations": "keyword",
    "section_title": "keyword",
    "dev_tags": "keyword",
    "deleted": "bool",
    "source_locale": "keyword",
}

# Search results are hydrated from the canonical PostgreSQL stores immediately
# after Qdrant returns.  Request only the metadata needed for fusion/merge and
# filters; omitting the potentially large payload `content` reduces response
# serialization without changing point ids, scores, or ranking.
RETRIEVAL_PAYLOAD_FIELDS = (
    "point_type",
    "doc_id",
    "chunk_index",
    "slug",
    "filepath",
    "title",
    "type",
    "tags",
    "relations",
    "section_title",
    "source_document",
    "source_locale",
    "dev_tags",
)


def _retrieval_payload_selector() -> qm.PayloadSelectorInclude:
    return qm.PayloadSelectorInclude(include=list(RETRIEVAL_PAYLOAD_FIELDS))


def _sparse_text(title: str, content: str) -> str:
    """Единая формула sparse-текста (BM25) для свежей индексации и backfill.

    Title содержит коды/номера разделов (например, "12410"), которые иначе
    не попадали в индекс и концепт не находился по поиску по коду. Обе точки
    построения (index_concepts и backfill_sparse) обязаны сходиться в этой
    формуле — иначе рестарт перезаписывает sparse из другого текста.
    """
    return f"{title}\n{content}" if title else content


def concept_point_id(doc_id: str, slug: str) -> str:
    """Детерминированный point_id концепта (uuid5 от логического ключа).

    Фаза 4: логический ключ `okf:concept:{doc_id}:{slug}` без привязки к
    абсолютному пути data_dir. Единственная точка вычисления id концепта —
    index_concepts, backfill_sparse/relations, document_tag_service и
    backfill_comment_concepts обязаны идти через этот хелпер.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"okf:concept:{doc_id}:{slug}"))


def chunk_point_id(doc_id: str, chunk_index: int) -> str:
    """Детерминированный point_id чанка (логический ключ, Фаза 4).

    Единственная точка вычисления id чанка (index_chunks, backfill_chunks,
    backfill_comment_concepts).
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"okf:chunk:{doc_id}:{chunk_index}"))

def _not_deleted() -> qm.Filter:
    """Фильтр-обёртка, исключающий мягко удалённые точки (Этап 4a.2 корзина).

    Единственная точка применения дисциплины `deleted`: все пути поиска обязаны
    добавлять этот must_not. Точки с `deleted=true` (документ в корзине) не
    участвуют в RAG-поиске, автодополнении и graph-expansion.
    """
    return qm.Filter(
        must_not=[qm.FieldCondition(key="deleted", match=qm.MatchValue(value=True))]
    )


def _demote_review_concepts(hits: list, penalty: int) -> None:
    """Понижает эффективный ранг концептов-замечаний (тег review) в ветке.

    Замечания рецензентов дублируют контекст абзаца-якоря: их узкий текст
    стабильно релевантнее запросу, чем широкий основной концепт, и они
    вытесняли основной контент из топа RRF (замечание на dense #1 при
    основном концепте на #10). Смещение ранга на penalty позиций опускает
    замечание ниже равнозначных основных хитов, не убирая из выдачи: по
    запросам именно про замечания («какие замечания от Тамойкиной») они
    остаются единственными релевантными. Вызывается до RRF из
    search_composite; penalty <= 0 — no-op.

    Внимание: после смещения порядок списка может не соответствовать rank
    (замечание с rank 0+10 встаёт ниже хита с rank 5) — RRF использует
    только hit.rank, порядок списка не важен.
    """
    if penalty <= 0:
        return
    for hit in hits:
        if (
            hit.payload.get("point_type") == CONCEPT_POINT_TYPE
            and "review" in (hit.payload.get("tags") or [])
        ):
            hit.rank += penalty


def _tag_match_filter(tags: list[str]) -> qm.Filter:
    """Точка матчится, если хотя бы один тег запроса есть в `tags` ИЛИ `dev_tags`.

    dev_tags — денормализованный номер/название/модуль разработки (Этап 4).
    OR между двумя полями реализуется через вложенный should внутри must.
    """
    return qm.Filter(
        must=[
            qm.Filter(
                should=[
                    qm.FieldCondition(key="tags", match=qm.MatchAny(any=tags)),
                    qm.FieldCondition(key="dev_tags", match=qm.MatchAny(any=tags)),
                ]
            )
        ]
    )


def _source_locale_filter(codes: list[str], include_unknown: bool) -> qm.Filter:
    """Жёсткий pre-filter по языку документа (Этап 7 фаза D, AND с тегами).

    `codes` — допустимые ISO-коды (MatchAny по payload `source_locale`), может быть
    пустым (тогда матчатся только unknown-документы, если include_unknown=True);
    `include_unknown` — добавить документы без языка (OR-ветка «не определён»).

    Семантика NULL в Qdrant 1.19 (валидировано spike'ом, 10.09.2026):
      - payload пишет `source_locale` явным null при NULL в БД (upsert и
        set_document_source_locale_payload) → `is_null` его матчит;
      - `is_null` НЕ матчит точки, у которых ключа source_locale нет ВОВСЕ
        (состояние до payload-backfill); их матчит `is_empty`.
      - `is_empty` матчит И явный null, И отсутствие ключа — поэтому unknown
        покрывается единственным условием `is_empty` (оборонительно и для окна
        до backfill, и после него). MatchAny по отсутствующему ключу не матчит,
        так что NULL-документы не «просачиваются» при фильтре только по кодам.
    """
    conditions: list = []
    if codes:
        conditions.append(
            qm.FieldCondition(key="source_locale", match=qm.MatchAny(any=list(codes)))
        )
    if include_unknown:
        conditions.append(qm.FieldCondition(key="source_locale", is_empty=True))
    return qm.Filter(should=conditions)


class VectorStore:
    def __init__(self):
        self.settings = get_settings()
        configured_grpc_port = getattr(self.settings, "qdrant_grpc_port", None)
        if configured_grpc_port is None:
            parsed_url = urlparse(self.settings.qdrant_url)
            configured_grpc_port = (parsed_url.port or 6333) + 1
        self.client = QdrantClient(
            url=self.settings.qdrant_url,
            api_key=self.settings.qdrant_api_key,
            timeout=10,
            prefer_grpc=bool(getattr(self.settings, "qdrant_prefer_grpc", False)),
            grpc_port=int(configured_grpc_port),
        )

    @property
    def collection(self) -> str:
        return self.settings.qdrant_collection

    @staticmethod
    def _sparse_params() -> dict[str, qm.SparseVectorParams]:
        return {
            SPARSE_VECTOR_NAME: qm.SparseVectorParams(modifier=qm.Modifier.IDF),
        }

    @staticmethod
    def _sparse_name_config() -> qm.SparseVectorNameConfig:
        return qm.SparseVectorNameConfig(
            sparse=qm.SparseVectorConfig(modifier=qm.Modifier.IDF),
        )

    def ensure_collection(self) -> None:
        if not _qdrant_call(self.client.collection_exists, self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=qm.VectorParams(
                    size=self.settings.embedding_dimensions,
                    distance=qm.Distance.COSINE,
                ),
                sparse_vectors_config=self._sparse_params(),
            )
            self._ensure_payload_indexes()
            return
        info = self.client.get_collection(self.collection)
        sparse_config = info.config.params.sparse_vectors
        if not sparse_config or SPARSE_VECTOR_NAME not in sparse_config:
            self.client.create_vector_name(
                collection_name=self.collection,
                vector_name=SPARSE_VECTOR_NAME,
                vector_name_config=self._sparse_name_config(),
            )
        self._ensure_payload_indexes()

    def _ensure_payload_indexes(self) -> None:
        """Создаёт payload-индексы для полей фильтрации (идемпотентно).

        Qdrant строит индекс по существующим точкам в фоне, без даунтайма.
        Уже существующие индексы — игнорируются (try/except).
        """
        schema_map = {
            "keyword": qm.PayloadSchemaType.KEYWORD,
            "integer": qm.PayloadSchemaType.INTEGER,
            "datetime": qm.PayloadSchemaType.DATETIME,
            "bool": qm.PayloadSchemaType.BOOL,
        }
        for field, schema_str in PAYLOAD_INDEX_FIELDS.items():
            schema_type = schema_map.get(schema_str, qm.PayloadSchemaType.KEYWORD)
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=schema_type,
                )
            except Exception:
                pass

    def _upsert_batches(
        self,
        points: list,
        *,
        doc_id: str,
        mode: str,
        retry_attempts: int = 3,
    ) -> _UpsertStats:
        """Upsert точек батчами фиксированного размера (инцидент 03.09.2026).

        Один upsert на 5667 концептов давал ~120 МБ JSON против серверного
        лимита Qdrant max_request_size_mb=32 → мгновенный 400. Батч 256 точек
        с 1024-мерным dense-вектором + sparse + payload ≈ 6-8 МБ — с запасом.

        Пер-батчевые ретраи (только сетевые/5xx/429): один сбойный батч не
        убивает остальные — максимум точек уходит до отказа. Счётчики
        возвращаются вызывающему для статусной модели (paused + resume
        доотправит остаток: point_id детерминированы, upsert идемпотентен).

        В лог — номер батча, размер, doc_id и режим (concept/chunk); payload
        в лог не пишется (данные документов).
        """
        total = len(points)
        stats: _UpsertStats = {
            "attempted_batches": 0,
            "successful_batches": 0,
            "failed_batches": 0,
            "attempted_points": 0,
            "indexed_points": 0,
            "failure": None,
        }
        if not points:
            return stats
        batch_size = self.settings.qdrant_upsert_batch_size or UPSERT_BATCH_SIZE
        n_batches = (total + batch_size - 1) // batch_size
        for start in range(0, total, batch_size):
            batch = points[start : start + batch_size]
            batch_no = start // batch_size + 1
            stats["attempted_batches"] += 1
            stats["attempted_points"] += len(batch)
            for attempt in range(1, retry_attempts + 1):
                try:
                    _qdrant_call(
                        self.client.upsert,
                        collection_name=self.collection,
                        points=batch,
                    )
                    stats["successful_batches"] += 1
                    stats["indexed_points"] += len(batch)
                    if n_batches > 1:
                        logger.info(
                            "[%s] Upsert %s: батч %d/%d (%d точек) — ок",
                            doc_id, mode, batch_no, n_batches, len(batch),
                        )
                    break
                except VectorStoreError as exc:
                    cause = exc.__cause__
                    status = getattr(cause, "status_code", None) if isinstance(cause, UnexpectedResponse) else None
                    # 4xx-валидация (400/422 — дефект данных) не ретраится;
                    # сеть и 5xx/429 (в т.ч. 503 «storage not ready» во время
                    # оптимизации Qdrant) — ретраится с бэкоффом.
                    retryable = status is None or status >= 500 or status == 429
                    if not retryable or attempt == retry_attempts:
                        # A permanent failure must not be hidden by a later
                        # network outage: retrying cannot repair invalid points.
                        if stats["failure"] is None or public_error_code(exc) == codes.INTERNAL_ERROR:
                            stats["failure"] = exc
                        diagnostic = (
                            cause.content.decode("utf-8", errors="replace")
                            if isinstance(cause, UnexpectedResponse) else exc.user_message
                        )
                        logger.error(
                            "[%s] Upsert %s: батч %d/%d (%d точек) не прошёл: %s",
                            doc_id, mode, batch_no, n_batches, len(batch), diagnostic,
                            exc_info=True,
                        )
                        stats["failed_batches"] += 1
                        break
                    time.sleep(min(2.0, 0.5 * attempt))
        return stats

    def index_concepts(
        self,
        doc_id: str,
        okf_docs: list[OkfDocument],
        vectors: list[list[float]],
        dev_tags: list[str] | None = None,
        source_locale: str | None = None,
    ) -> set[str]:
        """Индексирует концепты в Qdrant. Возвращает set point_id для последующей очистки орфанов."""
        cap = self.settings.okf_max_concept_chars
        dev_tags = dev_tags or []
        points = []
        point_ids: set[str] = set()
        for okf_doc, vector in zip(okf_docs, vectors):
            meta = okf_doc.metadata
            slug = Path(okf_doc.filepath).stem
            point_id = concept_point_id(doc_id, slug)
            point_ids.add(point_id)
            title = meta.get("title", "")
            capped_content = okf_doc.content[:cap]
            # Sparse-вектор строится из title + content (единая формула
            # _sparse_text — см. backfill_sparse).
            sparse_text = _sparse_text(title, capped_content)
            points.append(
                qm.PointStruct(
                    id=str(point_id),
                    vector={
                        "": vector,
                        SPARSE_VECTOR_NAME: to_sparse_vector(sparse_text),
                    },
                    payload={
                        "point_type": CONCEPT_POINT_TYPE,
                        "doc_id": doc_id,
                        "slug": slug,
                        "title": title,
                        "type": meta.get("type", "concept"),
                        "tags": meta.get("tags", []),
                        "relations": meta.get("relations", []),
                        "chunk_index": meta.get("chunk_index"),
                        "dev_tags": dev_tags,
                        "source_locale": source_locale,
                    },
                )
            )
        if points:
            stats = self._upsert_batches(points, doc_id=doc_id, mode="concept")
            if stats["failed_batches"]:
                raise VectorStoreError(
                    f"Qdrant: проиндексировано {stats['indexed_points']} из "
                    f"{stats['attempted_points']} концептов "
                    f"({stats['failed_batches']} батчей упали). "
                    "Не удалось завершить индексацию."
                ) from stats["failure"]
        return point_ids

    def index_chunks(
        self,
        doc_id: str,
        filename: str,
        chunk_texts: list[str],
        global_tags: list[str],
        vectors: list[list[float]],
        section_titles: list[str] | None = None,
        dev_tags: list[str] | None = None,
        source_locale: str | None = None,
    ) -> set[str]:
        """Индексирует сырые чанки как отдельные точки Qdrant (point_type="chunk").

        Чанк — полный текст исходного фрагмента документа (до 8000 символов).
        В отличие от концепта (LLM-выжимки), чанк сохраняет все детали, которые
        LLM могла уронить при генерации концептов. Dense и sparse-векторы
        строятся из section_title + полного текста чанка.

        Минимальный payload: только поля, нужные для поиска и merge/collapse.
        Атрибуты документа (filename, author, date) берутся из DocumentRegistry
        по doc_id на этапе форматирования ответа.

        point_id детерминирован и изолирован от концептов префиксом "chunk:".
        Возвращает set point_id для последующей очистки орфанов.
        """
        section_titles = section_titles or [""] * len(chunk_texts)
        dev_tags = dev_tags or []
        cap = self.settings.okf_max_chunk_index_chars
        points = []
        point_ids: set[str] = set()
        for i, (text, vector, section_title) in enumerate(zip(chunk_texts, vectors, section_titles)):
            point_id = chunk_point_id(doc_id, i)
            point_ids.add(point_id)
            capped = text[:cap]
            sparse_text = _sparse_text(section_title, capped)
            points.append(
                qm.PointStruct(
                    id=str(point_id),
                    vector={
                        "": vector,
                        SPARSE_VECTOR_NAME: to_sparse_vector(sparse_text),
                    },
                    payload={
                        "point_type": CHUNK_POINT_TYPE,
                        "doc_id": doc_id,
                        "chunk_index": i,
                        "tags": global_tags or [],
                        "dev_tags": dev_tags,
                        "source_locale": source_locale,
                    },
                )
            )
        if points:
            stats = self._upsert_batches(points, doc_id=doc_id, mode="chunk")
            if stats["failed_batches"]:
                raise VectorStoreError(
                    f"Qdrant: проиндексировано {stats['indexed_points']} из "
                    f"{stats['attempted_points']} чанков "
                    f"({stats['failed_batches']} батчей упали). "
                    "Не удалось завершить индексацию."
                ) from stats["failure"]
        return point_ids

    def delete_document(self, doc_id: str) -> None:
        _qdrant_call(
            self.client.delete,
            collection_name=self.collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
            ),
        )

    def delete_orphaned_points(self, doc_id: str, keep_point_ids: set[str]) -> None:
        """Удаляет точки документа, которых нет в keep_point_ids (осиротевшие старые версии).

        Используется после upsert новых концептов/чанков для безопасной очистки:
        новые точки уже записаны, удаляются только те, чьи point_id не совпадают
        с новым набором (например, если чанков стало меньше или изменились слаги).
        """
        orphan_ids: list[str] = []
        next_offset = None
        doc_filter = qm.Filter(
            must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]
        )
        while True:
            batch, next_offset = _qdrant_call(
                self.client.scroll,
                collection_name=self.collection,
                scroll_filter=doc_filter,
                limit=500,
                with_payload=False,
                with_vectors=False,
                offset=next_offset,
            )
            for rec in batch:
                pid = str(rec.id)
                if pid not in keep_point_ids:
                    orphan_ids.append(pid)
            if next_offset is None:
                break
        if orphan_ids:
            _qdrant_call(
                self.client.delete,
                collection_name=self.collection,
                points_selector=qm.PointIdsList(points=orphan_ids),
            )

    def reindex_document_dev_tags(self, doc_id: str, dev_tags: list[str]) -> None:
        """Обновляет payload `dev_tags` на всех точках документа (без пере-эмбеддинга).

        Используется при связывании/переименовании разработки: денормализованная
        проекция dev_tags (номер/название/модуль) обновляется точечно через
        set_payload по фильтру doc_id — dense/sparse-векторы не затрагиваются.
        """
        _qdrant_call(
            self.client.set_payload,
            collection_name=self.collection,
            payload={"dev_tags": list(dev_tags)},
            points=qm.FilterSelector(
                filter=qm.Filter(
                    must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]
                )
            ),
        )

    def set_document_deleted(self, doc_id: str, deleted: bool) -> None:
        """Ставит/снимает payload-флаг `deleted` на всех точках документа (корзина).

        Soft delete (Этап 4a.2): точки физически НЕ удаляются — только помечаются
        `deleted=true`, чтобы поиск их исключал через _not_deleted(). Восстановление
        снимает флаг (`deleted=false`) без пересчёта эмбеддингов.
        """
        _qdrant_call(
            self.client.set_payload,
            collection_name=self.collection,
            payload={"deleted": bool(deleted)},
            points=qm.FilterSelector(
                filter=qm.Filter(
                    must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]
                )
            ),
        )

    def set_document_source_locale_payload(self, doc_id: str, source_locale: str | None) -> None:
        """Обновляет payload `source_locale` на всех точках документа (без пере-эмбеддинга).

        Денормализованная проекция языка документа (Этап 7 фаза D, фильтр): пишется
        через set_payload по фильтру doc_id на concept И chunk точках — dense/sparse
        не затрагиваются. `None` пишется как явный null в payload (Qdrant 1.19
        хранит null в payload) — семантика «язык не определён» для фильтра.
        """
        _qdrant_call(
            self.client.set_payload,
            collection_name=self.collection,
            payload={"source_locale": source_locale},
            points=qm.FilterSelector(
                filter=qm.Filter(
                    must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]
                )
            ),
        )

    def set_document_tags_payload(
        self,
        doc_id: str,
        new_tags: list[str],
        removed: list[str] | None = None,
        added: list[str] | None = None,
        *,
        concept_points: list[tuple[str, list[str]]] | None = None,
        skip_chunks: bool = False,
    ) -> None:
        """Обновляет payload `tags` всех точек документа после правки тегов (Этап 4a).

        **Без scroll**: медленный scroll с фильтром по doc_id (и тем более с полным
        payload) делал синхронную правку тегов недопустимо медленной (секунды).
        Вместо этого:
          - chunk-точки несут `tags` = глобальные теги документа (user_tags) —
            обновляются одним filter-based set_payload (как dev_sync);
          - concept-точки: point_id детерминирован (uuid5 от filepath бандла),
            поэтому tags берутся из `okf_concepts` (уже пересчитаны правкой)
            и проставляются группированным set_payload по point_id.

        `concept_points` — список (point_id, tags) для concept-точек. Идемпотентно:
        повторный вызов приводит Qdrant к состоянию БД, порядок правок не важен.
        `skip_chunks=True` — не трогать chunk-точки вовсе (backfill тега «attachment»
        программно добавляет его ТОЛЬКО в okf_concepts.tags; chunk-слой не участвует).
        Бросает VectorStoreError при недоступности Qdrant — вызывающий решает.
        """
        del removed, added  # дельта больше не нужна — состояние читается из БД
        global_tags = list(new_tags or [])
        if not skip_chunks:
            _qdrant_call(
                self.client.set_payload,
                collection_name=self.collection,
                payload={"tags": global_tags},
                points=qm.FilterSelector(
                    filter=qm.Filter(
                        must=[
                            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
                            qm.FieldCondition(
                                key="point_type", match=qm.MatchValue(value=CHUNK_POINT_TYPE)
                            ),
                        ]
                    )
                ),
            )

        groups: dict[tuple[str, ...], list[str]] = {}
        for point_id, tags in concept_points or []:
            groups.setdefault(tuple(tags or []), []).append(str(point_id))
        for tag_tuple, point_ids in groups.items():
            _qdrant_call(
                self.client.set_payload,
                collection_name=self.collection,
                payload={"tags": list(tag_tuple)},
                points=point_ids,
            )

    def ping(self) -> bool:
        """Лёгкая проверка доступности Qdrant (для /health)."""
        try:
            self.client.get_collections()
            return True
        except Exception:
            return False

    def search(
        self,
        vector: list[float] | None,
        sparse_vec: qm.SparseVector | None,
        mode: str,
        tags: list[str] | None = None,
        top_k: int = 5,
    ) -> list[dict]:
        if mode not in SEARCH_MODES:
            raise ValueError(f"Неизвестный режим поиска: {mode}. Допустимы: {SEARCH_MODES}")
        query_filter = self._build_search_filter(tags)

        if mode == "dense":
            query = vector
            prefetch = None
            using = None
        elif mode == "bm25":
            query = sparse_vec
            prefetch = None
            using = SPARSE_VECTOR_NAME
        else:
            prefetch = []
            if vector is not None:
                prefetch.append(qm.Prefetch(query=vector, limit=max(top_k, 16)))
            if sparse_vec is not None and sparse_vec.indices:
                prefetch.append(qm.Prefetch(query=sparse_vec, using=SPARSE_VECTOR_NAME, limit=max(top_k, 16)))
            query = qm.FusionQuery(fusion=qm.Fusion.RRF)
            using = None

        if prefetch is not None and not prefetch:
            query = vector or []
            prefetch = None
            using = None

        results = _qdrant_call(
            self.client.query_points,
            collection_name=self.collection,
            query=query,
            prefetch=prefetch,
            using=using,
            query_filter=query_filter,
            limit=top_k,
            with_payload=_retrieval_payload_selector(),
        )
        return [{"score": r.score, "payload": r.payload} for r in results.points]

    # --- Ветки композитного поиска ---

    def search_dense(
        self, vector: list[float], query_filter: qm.Filter | None, top_k: int
    ) -> list[Hit]:
        """Семантический поиск по dense-вектору (по всем точкам: концепты + чанки)."""
        results = _qdrant_call(
            self.client.query_points,
            collection_name=self.collection,
            query=vector,
            query_filter=query_filter,
            limit=top_k,
            with_payload=_retrieval_payload_selector(),
        )
        return [
            Hit(point_id=str(r.id), score=r.score or 0.0, payload=r.payload or {}, rank=i)
            for i, r in enumerate(results.points)
        ]

    def search_bm25(
        self, sparse_vec: qm.SparseVector, query_filter: qm.Filter | None, top_k: int
    ) -> list[Hit]:
        """Лексический поиск по sparse-вектору (BM25, Qdrant IDF)."""
        if not sparse_vec.indices:
            return []
        results = _qdrant_call(
            self.client.query_points,
            collection_name=self.collection,
            query=sparse_vec,
            using=SPARSE_VECTOR_NAME,
            query_filter=query_filter,
            limit=top_k,
            with_payload=_retrieval_payload_selector(),
        )
        return [
            Hit(point_id=str(r.id), score=r.score or 0.0, payload=r.payload or {}, rank=i)
            for i, r in enumerate(results.points)
        ]

    def _graph_expansion(
        self, ranked_lists: list[tuple[list[Hit], float]]
    ) -> list[Hit]:
        """Graph expansion: достаёт соседей по relations концептов.

        Из hits dense/bm25 собирает relations (slug-и) из payload концептов,
        затем находит точки с matching slug. Соседи получают rank >= 100
        (ниже прямых хитов), вес 0.5.
        """
        relations_set: set[str] = set()
        for hits, _ in ranked_lists:
            for hit in hits:
                if hit.payload.get("point_type") == CONCEPT_POINT_TYPE:
                    for r in hit.payload.get("relations", []):
                        r = str(r).strip()
                        if r:
                            relations_set.add(r)
        if not relations_set:
            return []
        slug_filter = qm.Filter(
            must=[qm.FieldCondition(key="slug", match=qm.MatchAny(any=list(relations_set)))],
            must_not=_not_deleted().must_not,
        )
        records, _ = _qdrant_call(
            self.client.scroll,
            collection_name=self.collection,
            scroll_filter=slug_filter,
            limit=self.settings.search_per_branch_top_k,
            with_payload=True,
            with_vectors=False,
        )
        return [
            Hit(point_id=str(r.id), score=0.5, payload=r.payload or {}, rank=100 + i)
            for i, r in enumerate(records)
        ]

    def search_composite(
        self,
        *,
        dense_vec: list[float] | None,
        sparse_vec: qm.SparseVector | None,
        tags: list[str] | None,
        branches: set[str],
        top_k: int,
        source_locales: list[str] | None = None,
        include_unknown_source_locale: bool = False,
    ) -> list[Hit]:
        """Композитный поиск: запускает включённые ветки, сливает через RRF.

        Args:
            dense_vec: dense-вектор запроса (или None если dense выключен).
            sparse_vec: sparse-вектор запроса (или None если bm25 выключен).
            tags: теги для жёсткого pre-filter (OR по MatchAny).
            branches: набор включённых веток {"dense", "bm25"}.
            top_k: финальное число результатов.
            source_locales: язык документа (pre-filter, AND с тегами); None/пусто —
                фильтр не применяется.
            include_unknown_source_locale: добавить документы без языка (OR).

        Returns:
            Список Hit, отсортированный по fused RRF score.
        """
        search_filter = self._build_search_filter(
            tags, source_locales=source_locales,
            include_unknown=include_unknown_source_locale,
        )
        per_branch = self.settings.search_per_branch_top_k
        k = self.settings.search_rrf_k
        ranked_lists: list[tuple[list[Hit], float]] = []

        if "dense" in branches and dense_vec is not None and self.settings.search_dense_enabled:
            hits = self.search_dense(dense_vec, search_filter, per_branch)
            _demote_review_concepts(hits, self.settings.search_review_concept_rank_penalty)
            ranked_lists.append((hits, self.settings.search_rrf_dense_weight))
        if "bm25" in branches and sparse_vec is not None and self.settings.search_bm25_enabled:
            hits = self.search_bm25(sparse_vec, search_filter, per_branch)
            _demote_review_concepts(hits, self.settings.search_review_concept_rank_penalty)
            ranked_lists.append((hits, self.settings.search_rrf_bm25_weight))

        if self.settings.search_graph_expansion_enabled:
            graph_hits = self._graph_expansion(ranked_lists)
            if graph_hits:
                ranked_lists.append((graph_hits, self.settings.search_rrf_graph_expansion_weight))

        from app.services.fusion import reciprocal_rank_fusion

        fused = reciprocal_rank_fusion(ranked_lists, k=k)
        return fused[:top_k]

    @staticmethod
    def _build_search_filter(
        tags: list[str] | None,
        source_locales: list[str] | None = None,
        include_unknown: bool = False,
    ) -> qm.Filter:
        """Жёсткий pre-filter для dense и bm25 веток + исключение корзины.

        Матчит тег, если он есть в `tags` ИЛИ в `dev_tags` (денормализованный
        номер/название/модуль разработки — Этап 4). Если задан `source_locales` —
        добавляет AND-условие по языку документа (с опциональной OR-веткой
        «не определён» через `include_unknown`). Всегда добавляет `must_not
        deleted` (Этап 4a.2) — единая обёртка, чтобы удалённые точки не попадали
        в поиск из любого нового сценария.
        """
        must_not = _not_deleted().must_not
        must: list = []
        if tags:
            must.append(_tag_match_filter(tags))
        if source_locales or include_unknown:
            must.append(_source_locale_filter(source_locales or [], include_unknown))
        if not must:
            return qm.Filter(must_not=must_not)
        return qm.Filter(must=must, must_not=must_not)

    def _scroll_points(
        self,
        *,
        with_payload: bool | list[str],
        with_vectors: bool,
        scroll_filter: qm.Filter | None = None,
    ) -> Iterator[qm.Record]:
        """Итерирует точки коллекции батчами (scroll с пагинацией).

        with_payload принимает и список ключей — тогда Qdrant отдаёт только их,
        а не payload целиком. scroll_filter отбирает точки на стороне сервера:
        дешевле выкачать нужное подмножество, чем всю коллекцию ради проверки.
        """
        next_offset = None
        while True:
            batch, next_offset = self.client.scroll(
                collection_name=self.collection,
                limit=SCROLL_PAGE_SIZE,
                with_payload=with_payload,
                with_vectors=with_vectors,
                offset=next_offset,
                scroll_filter=scroll_filter,
            )
            yield from batch
            if next_offset is None:
                break

    def backfill_sparse(
        self, batch_size: int = 100, *, force: bool = False, include_chunks: bool = False
    ) -> int:
        """Добивает sparse-векторы старых точек из PostgreSQL (okf_concepts; с
        include_chunks=True — ещё и document_chunks).

        Этап 2b: источник истины — БД, а не .md-бандлы. sparse строится по ТОЙ ЖЕ
        формуле _sparse_text (title + content), что при свежей индексации.
        Dense-вектор и payload существующей точки не затрагиваются.

        Идемпотентно: точки, у которых sparse-вектор уже есть, пропускаются —
        их отбирает САМ Qdrant фильтром must_not has_vector. Раньше сюда
        выкачивалась вся коллекция с with_vectors=True (то есть все dense-векторы
        по сети и словарь на весь корпус в памяти) только чтобы проверить наличие
        ключа в словаре векторов — на каждый старт процесса. Теперь на прогретой
        коллекции scroll возвращает пустой результат за один запрос.

        force=True пересчитывает все точки — одноразовая миграция после смены
        формулы текста (rebuild_sparse.py); векторы не выкачиваются и в этом
        режиме, нужны только id.

        include_chunks=True дополнительно пересчитывает sparse ЧАНКОВ
        (formula: section_title + content[:okf_max_chunk_index_chars]) — чанки
        читаются из БД и проверяются по тому же target_ids (scroll с тем же
        has_vector-фильтром). По умолчанию выключено: стартовый backfill (main.py)
        добивает только концепты; чанки гоняет rebuild_sparse.py при смене
        токенайзера (08.09.2026 — немецкие ä/ö/ü/ß в алфавите).

        Документы В КОРЗИНЕ намеренно НЕ исключаются. Удаление мягкое: точки
        физически остаются в Qdrant (delete — флаг в payload), и восстановление
        (`trash.restore_document`) sparse не пересчитывает. Пока здесь стоял
        фильтр `deleted_at IS NULL`, документ, удалённый ДО смены токенайзера и
        восстановленный ПОСЛЕ прогона rebuild_sparse.py, навсегда оставался с
        векторами старой формулы: миграция его не видела, а стартовый backfill
        не подбирал (sparse уже есть → отсеивает has_vector). Лишней работы это
        не создаёт — без force трогаются только точки БЕЗ sparse.

        Требует Qdrant >= 1.11 (условие has_vector).
        """
        from sqlalchemy import select

        from app.db.models import Document, DocumentChunk, OkfConcept
        from app.db.session import session_scope

        # Без force берём только точки БЕЗ sparse — остальные не нужны вовсе.
        # point_type не фильтруем намеренно: id из БД всё равно концептные, а
        # легаси-точки без этого ключа payload должны оставаться чинимыми.
        scroll_filter = (
            None
            if force
            else qm.Filter(must_not=[qm.HasVectorCondition(has_vector=SPARSE_VECTOR_NAME)])
        )
        target_ids = {
            str(rec.id)
            for rec in self._scroll_points(
                with_payload=False, with_vectors=False, scroll_filter=scroll_filter
            )
        }

        points: list[qm.PointVectors] = []

        cap = self.settings.okf_max_concept_chars
        with session_scope() as s:
            rows = s.execute(
                select(OkfConcept.doc_id, OkfConcept.slug, OkfConcept.title, OkfConcept.content)
                .join(Document, OkfConcept.doc_id == Document.id)
            ).all()
        for doc_id, slug, title, content in rows:
            point_id = concept_point_id(doc_id, slug)
            # Точки нет в выборке — либо её нет в Qdrant, либо sparse уже
            # построен (фильтр отсеял). В обоих случаях трогать нечего.
            if point_id not in target_ids:
                continue
            sparse_vec = to_sparse_vector(_sparse_text(title or "", (content or "")[:cap]))
            if not sparse_vec.indices:
                continue
            points.append(
                qm.PointVectors(id=point_id, vector={SPARSE_VECTOR_NAME: sparse_vec})
            )

        if include_chunks:
            cap_chunk = self.settings.okf_max_chunk_index_chars
            with session_scope() as s:
                chunk_rows = s.execute(
                    select(
                        DocumentChunk.doc_id,
                        DocumentChunk.chunk_index,
                        DocumentChunk.section_title,
                        DocumentChunk.content,
                    ).join(Document, DocumentChunk.doc_id == Document.id)
                ).all()
            for doc_id, chunk_index, section_title, content in chunk_rows:
                point_id = chunk_point_id(doc_id, chunk_index)
                # Точки нет в выборке — либо её нет в Qdrant, либо sparse уже
                # построен (фильтр отсеял). В обоих случаях трогать нечего.
                if point_id not in target_ids:
                    continue
                sparse_vec = to_sparse_vector(
                    _sparse_text(section_title or "", (content or "")[:cap_chunk])
                )
                if not sparse_vec.indices:
                    continue
                points.append(
                    qm.PointVectors(id=point_id, vector={SPARSE_VECTOR_NAME: sparse_vec})
                )

        total = len(points)
        for start in range(0, total, batch_size):
            batch = points[start : start + batch_size]
            self.client.update_vectors(collection_name=self.collection, points=batch)
        return total

    def backfill_chunks(self, embedder, batch_size: int = 64) -> int:
        """Индексирует чанки из PostgreSQL (document_chunks) — Этап 2b.

        Для каждого done-документа (не в корзине) читает чанки из document_chunks,
        вычисляет dense+sparse-векторы, upsert как point_type="chunk". Идемпотентно:
        существующие точки (детерминированный point_id) скипаются ЦЕЛИКОМ по доку
        ДО вызова эмбеддинга. dev_tags — из БД (development), global_tags — из
        document_tags. Чанки документов, чей текст ещё не перенесён в БД, остаются
        в Qdrant нетронутыми (существующие точки не удаляются).
        """
        if not self.settings.search_index_chunks_enabled:
            return 0

        from sqlalchemy import select

        from app.db.models import Document, DocumentChunk
        from app.db.session import session_scope
        from app.services.development_registry import get_development_registry

        dev_reg = get_development_registry()

        existing_ids: set[str] = set()
        for rec in self._scroll_points(with_payload=False, with_vectors=False):
            existing_ids.add(str(rec.id))

        # Собрать состояние в память ДО эмбеддинга (не держим сессию БД во время
        # долгих LLM-эмбеддингов).
        docs_data: list[dict] = []
        with session_scope() as s:
            docs = s.execute(
                select(Document.id, Document.filename, Document.development_id).where(
                    # Не в корзине/не удалён. Статус не фильтруем: paused-документ
                    # (finalize упал ПОСЛЕ записи чанков в БД, но ДО Qdrant) имеет
                    # document_chunks без точек — backfill обязан их доиндексировать.
                    Document.deleted_at.is_(None)
                )
            ).all()
            for doc_id, filename, dev_id in docs:
                chunks = (
                    s.query(DocumentChunk)
                    .filter(DocumentChunk.doc_id == doc_id)
                    .order_by(DocumentChunk.chunk_index)
                    .all()
                )
                if not chunks:
                    continue
                doc = s.get(Document, doc_id)
                global_tags = [t.tag_rel.canonical_text for t in (doc.tags_rel or [])]
                docs_data.append(
                    {
                        "doc_id": doc_id,
                        "filename": filename,
                        "dev_id": dev_id,
                        "global_tags": global_tags,
                        "source_locale": doc.source_locale,
                        "chunks": [
                            (c.chunk_index, c.section_title or "", c.content or "")
                            for c in chunks
                        ],
                    }
                )

        chunk_points: list[qm.PointStruct] = []
        cap = self.settings.okf_max_chunk_index_chars
        for d in docs_data:
            doc_id = d["doc_id"]
            point_ids = [chunk_point_id(doc_id, ci) for ci, _, _ in d["chunks"]]
            if all(pid in existing_ids for pid in point_ids):
                continue  # весь документ уже проиндексирован — эмбеддинг не нужен
            embed_positions = [i for i, pid in enumerate(point_ids) if pid not in existing_ids]

            embed_inputs = []
            for i in embed_positions:
                st, t = d["chunks"][i][1], d["chunks"][i][2]
                embed_inputs.append(f"{st}\n{t[:cap]}" if st else t[:cap])
            vectors = embedder.embed_texts(embed_inputs)

            dev_tags = dev_reg.dev_tags(d["dev_id"]) if d["dev_id"] else []
            for pos, vec in zip(embed_positions, vectors):
                ci, st, text = d["chunks"][pos]
                capped = text[:cap]
                sparse_text = _sparse_text(st, capped)
                chunk_points.append(
                    qm.PointStruct(
                        id=point_ids[pos],
                        vector={
                            "": vec,
                            SPARSE_VECTOR_NAME: to_sparse_vector(sparse_text),
                        },
                        payload={
                            "point_type": CHUNK_POINT_TYPE,
                            "doc_id": doc_id,
                            "chunk_index": ci,
                            "tags": d["global_tags"],
                            "dev_tags": dev_tags,
                            "source_locale": d["source_locale"],
                        },
                    )
                )

        # Пер-батчевая устойчивость (инцидент 03.09.2026): один битый батч
        # (422 на коллизии sparse-индексов) убивал весь backfill корпуса.
        # Сбойный батч логируется и пропускается, остальные уходят; итог —
        # сводка со счётчиками. Возврат — число реально проиндексированных.
        total = len(chunk_points)
        attempted_batches = successful_batches = failed_batches = 0
        attempted_points = indexed_points = 0
        failed_doc_ids: set[str] = set()
        for start in range(0, total, batch_size):
            batch = chunk_points[start : start + batch_size]
            attempted_batches += 1
            attempted_points += len(batch)
            try:
                self.client.upsert(collection_name=self.collection, points=batch)
                successful_batches += 1
                indexed_points += len(batch)
            except Exception as exc:
                failed_batches += 1
                failed_doc_ids.update(
                    str(p.payload.get("doc_id")) for p in batch if p.payload
                )
                logger.error(
                    "Backfill чанков: батч %d упал (%d точек): %s",
                    attempted_batches, len(batch), exc,
                )
        if failed_batches:
            logger.error(
                "Backfill чанков завершён ЧАСТИЧНО: батчи ок %d/%d, проиндексировано "
                "%d из %d точек; неиндексированные документы: %s",
                successful_batches,
                attempted_batches,
                indexed_points,
                attempted_points,
                ", ".join(sorted(failed_doc_ids)),
            )
        elif indexed_points:
            logger.info(
                "Backfill чанков: %d/%d батчей, %d точек",
                successful_batches, attempted_batches, indexed_points,
            )
        return indexed_points

    def backfill_relations(self) -> int:
        """Нормализует relations в payload концептов из PostgreSQL (okf_concepts).

        Этап 2b: источник истины — БД (relations уже нормализованы при финализации).
        Сравнивает с текущим значением в Qdrant — пропускает неизменившиеся точки.
        Группирует изменившиеся по значению relations и обновляет одним set_payload
        на группу (вместо per-point вызовов). Идемпотентно.

        Без batch_size: после перехода на _scroll_points читает страницами по
        SCROLL_PAGE_SIZE, а пишет группами по значению relations — параметр
        остался от прежней реализации и ни на что не влиял.
        """
        from sqlalchemy import select

        from app.db.models import Document, OkfConcept
        from app.db.session import session_scope

        with session_scope() as s:
            rows = s.execute(
                select(OkfConcept.doc_id, OkfConcept.slug, OkfConcept.relations)
                .join(Document, OkfConcept.doc_id == Document.id)
                .where(Document.deleted_at.is_(None))
            ).all()

        db_relations: dict[str, list[str]] = {}
        for doc_id, slug, relations in rows:
            point_id = concept_point_id(doc_id, slug)
            db_relations[point_id] = list(relations or [])

        if not db_relations:
            return 0

        # with_payload=["relations"] — а не True: payload концепта содержит
        # content до 4000 символов, и полный scroll на каждый старт тянул бы
        # текст всего корпуса ради одного ключа.
        existing: dict[str, list[str]] = {}
        for rec in self._scroll_points(
            with_payload=["relations"], with_vectors=False
        ):
            pid = str(rec.id)
            if pid in db_relations:
                existing[pid] = list((rec.payload or {}).get("relations", []))

        changed: dict[str, list[str]] = {}
        for pid, normalized in db_relations.items():
            current = existing.get(pid)
            if current is None:
                continue  # точка удалена из Qdrant
            if list(current) != normalized:
                changed[pid] = normalized

        if not changed:
            return 0

        groups: dict[tuple[str, ...], list[str]] = {}
        for pid, normalized in changed.items():
            key = tuple(normalized)
            groups.setdefault(key, []).append(pid)

        total = 0
        for relations_tuple, point_ids in groups.items():
            self.client.set_payload(
                collection_name=self.collection,
                payload={"relations": list(relations_tuple)},
                points=point_ids,
            )
            total += len(point_ids)
        return total
