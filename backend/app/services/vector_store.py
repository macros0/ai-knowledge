"""Векторное хранилище на Qdrant: dual-index (концепты + чанки), ветки поиска с RRF fusion.

Хранит два типа точек в одной коллекции:
  - point_type="concept" — LLM-выжимки (title, tags, relations, content[:4000]);
  - point_type="chunk"    — сырой текст чанка (content[:8000], chunk_index).

Ветки поиска (query-time, управляются флагами в config.py):
  - dense    — семантический поиск по dense-вектору (BGE-M3);
  - bm25     — лексический поиск по sparse-вектору (BM25, Qdrant IDF);
  - metadata — filter-only по payload-полям (tags, type, doc_id, relations, ...);
  - graph    — expansion: соседи по relations концептов (вес 0.5).

Слияние — RRF (Reciprocal Rank Fusion) в Python (services/fusion.py), не
встроенный Qdrant fusion. Каждая ветка отдаёт per_branch_top_k кандидатов.
"""
import logging
import time
import uuid
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.config import get_settings
from app.models.schemas import OkfDocument
from app.services.errors import VectorStoreError
from app.services.fusion import Hit
from app.services.sparse import to_sparse_vector

logger = logging.getLogger(__name__)


def _qdrant_call(func, *args, **kwargs):
    """Обёртка для Qdrant-вызовов: перехватывает сетевые ошибки → VectorStoreError."""
    try:
        return func(*args, **kwargs)
    except VectorStoreError:
        raise
    except Exception as exc:
        raise VectorStoreError(
            "База знаний (Qdrant) недоступна. "
            "Проверьте, что Qdrant запущен на порту 6333.",
            cause=exc,
        ) from exc

SPARSE_VECTOR_NAME = "sparse"
SEARCH_MODES = ("dense", "bm25", "hybrid", "full")

CONCEPT_POINT_TYPE = "concept"
CHUNK_POINT_TYPE = "chunk"

PAYLOAD_INDEX_FIELDS: dict[str, str] = {
    "point_type": "keyword",
    "doc_id": "keyword",
    "chunk_index": "integer",
    "slug": "keyword",
    "type": "keyword",
    "tags": "keyword",
    "global_tags": "keyword",
    "relations": "keyword",
    "section_title": "keyword",
    "source_document.author": "keyword",
    "source_document.date": "datetime",
}


class VectorStore:
    def __init__(self):
        self.settings = get_settings()
        self.client = QdrantClient(url=self.settings.qdrant_url, timeout=10)

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

    def index_concepts(self, doc_id: str, okf_docs: list[OkfDocument], vectors: list[list[float]]) -> set[str]:
        """Индексирует концепты в Qdrant. Возвращает set point_id для последующей очистки орфанов."""
        cap = self.settings.okf_max_concept_chars
        points = []
        point_ids: set[str] = set()
        for okf_doc, vector in zip(okf_docs, vectors):
            meta = okf_doc.metadata
            point_id = uuid.uuid5(uuid.NAMESPACE_URL, okf_doc.filepath)
            point_ids.add(str(point_id))
            title = meta.get("title", "")
            capped_content = okf_doc.content[:cap]
            # Sparse-вектор строится из title + content: title содержит коды/номера
            # разделов (например, "12410"), которые иначе не попадали в индекс и
            # концепт не находился по поиску по коду.
            sparse_text = f"{title}\n{capped_content}" if title else capped_content
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
                        "filepath": okf_doc.filepath,
                        "slug": Path(okf_doc.filepath).name,
                        "title": title,
                        "type": meta.get("type", "concept"),
                        "tags": meta.get("tags", []),
                        "global_tags": meta.get("global_tags", []),
                        "source_document": meta.get("source_document", {}),
                        "attachments": meta.get("attachments", []),
                        "relations": meta.get("relations", []),
                        "chunk_index": meta.get("chunk_index"),
                        "content": capped_content,
                    },
                )
            )
        if points:
            _qdrant_call(self.client.upsert, collection_name=self.collection, points=points)
        return point_ids

    def index_chunks(
        self,
        doc_id: str,
        filename: str,
        chunk_texts: list[str],
        global_tags: list[str],
        vectors: list[list[float]],
        section_titles: list[str] | None = None,
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
        cap = self.settings.okf_max_chunk_index_chars
        points = []
        point_ids: set[str] = set()
        for i, (text, vector, section_title) in enumerate(zip(chunk_texts, vectors, section_titles)):
            point_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"chunk:{doc_id}/chunks/chunk_{i:02d}.md",
            )
            point_ids.add(str(point_id))
            capped = text[:cap]
            sparse_text = f"{section_title}\n{capped}" if section_title else capped
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
                        "section_title": section_title,
                        "content": capped,
                    },
                )
            )
        if points:
            _qdrant_call(self.client.upsert, collection_name=self.collection, points=points)
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
        query_filter = None
        if tags:
            query_filter = qm.Filter(must=[qm.FieldCondition(key="tags", match=qm.MatchAny(any=tags))])

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

        results = self.client.query_points(
            collection_name=self.collection,
            query=query,
            prefetch=prefetch,
            using=using,
            query_filter=query_filter,
            limit=top_k,
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
            must=[qm.FieldCondition(key="slug", match=qm.MatchAny(any=list(relations_set)))]
        )
        records, _ = self.client.scroll(
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
    ) -> list[Hit]:
        """Композитный поиск: запускает включённые ветки, сливает через RRF.

        Args:
            dense_vec: dense-вектор запроса (или None если dense выключен).
            sparse_vec: sparse-вектор запроса (или None если bm25 выключен).
            tags: теги для жёсткого pre-filter (OR по MatchAny).
            branches: набор включённых веток {"dense", "bm25"}.
            top_k: финальное число результатов.

        Returns:
            Список Hit, отсортированный по fused RRF score.
        """
        search_filter = self._build_search_filter(tags)
        per_branch = self.settings.search_per_branch_top_k
        k = self.settings.search_rrf_k
        ranked_lists: list[tuple[list[Hit], float]] = []

        if "dense" in branches and dense_vec is not None and self.settings.search_dense_enabled:
            ranked_lists.append(
                (self.search_dense(dense_vec, search_filter, per_branch), self.settings.search_rrf_dense_weight)
            )
        if "bm25" in branches and sparse_vec is not None and self.settings.search_bm25_enabled:
            ranked_lists.append(
                (self.search_bm25(sparse_vec, search_filter, per_branch), self.settings.search_rrf_bm25_weight)
            )

        if self.settings.search_graph_expansion_enabled:
            graph_hits = self._graph_expansion(ranked_lists)
            if graph_hits:
                ranked_lists.append((graph_hits, self.settings.search_rrf_graph_expansion_weight))

        from app.services.fusion import reciprocal_rank_fusion

        fused = reciprocal_rank_fusion(ranked_lists, k=k)
        return fused[:top_k]

    @staticmethod
    def _build_search_filter(tags: list[str] | None) -> qm.Filter | None:
        """Жёсткий pre-filter по tags для dense и bm25 веток."""
        if not tags:
            return None
        return qm.Filter(
            must=[qm.FieldCondition(key="tags", match=qm.MatchAny(any=tags))]
        )

    def backfill_sparse(self, batch_size: int = 100) -> int:
        """Добивает sparse-векторы для старых точек из OKF-бандлов.

        id точки детерминирован (uuid5 от filepath бандла), поэтому sparse
        пересчитывается из полного текста и обновляется через update_vectors —
        dense-вектор и payload существующей точки не затрагиваются.

        Пропускает точки, которые есть в бандлах на диске, но уже удалены
        из Qdrant (scroll собирает только существующие ID).
        """
        if not self.settings.okf_dir.is_dir():
            return 0

        existing_ids: set[str] = set()
        next_offset = None
        while True:
            batch = self.client.scroll(
                collection_name=self.collection,
                limit=1000,
                with_vectors=False,
                offset=next_offset,
            )
            records, next_offset = batch
            for rec in records:
                existing_ids.add(str(rec.id))
            if next_offset is None:
                break

        points: list[qm.PointVectors] = []
        for bundle_dir in sorted(self.settings.okf_dir.iterdir()):
            if not bundle_dir.is_dir():
                continue
            for md in sorted(bundle_dir.glob("*.md")):
                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(md)))
                if point_id not in existing_ids:
                    continue
                sparse_vec = to_sparse_vector(md.read_text(encoding="utf-8"))
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

    def backfill_point_type(self, batch_size: int = 500) -> int:
        """Добавляет point_type="concept" в payload существующих точек без этого поля.

        Идемпотентно: пропускает точки, у которых point_type уже установлен.
        Батчит set_payload одним списком ID вместо индивидуальных вызовов.
        """
        need_update: list[str] = []
        next_offset = None
        while True:
            batch, next_offset = self.client.scroll(
                collection_name=self.collection,
                limit=batch_size,
                with_payload=True,
                with_vectors=False,
                offset=next_offset,
            )
            for rec in batch:
                payload = rec.payload or {}
                if "point_type" not in payload:
                    need_update.append(str(rec.id))
            if next_offset is None:
                break
        if not need_update:
            return 0
        self.client.set_payload(
            collection_name=self.collection,
            payload={"point_type": CONCEPT_POINT_TYPE},
            points=need_update,
        )
        return len(need_update)

    def backfill_chunks(self, embedder, batch_size: int = 64) -> int:
        """Индексирует чанки из существующих OKF-бандлов (data/okf_bundles/*/chunks/).

        Для каждого бандла читает chunk_XX.md, вычисляет dense+sparse-векторы,
        upsert как point_type="chunk". Идемпотентно: точки уже существуют
        (детерминированный point_id) — upsert перезаписывает без дублирования.
        """
        if not self.settings.okf_dir.is_dir():
            return 0
        if not self.settings.search_index_chunks_enabled:
            return 0

        existing_ids: set[str] = set()
        next_offset = None
        while True:
            batch, next_offset = self.client.scroll(
                collection_name=self.collection,
                limit=1000,
                with_payload=True,
                with_vectors=False,
                offset=next_offset,
            )
            for rec in batch:
                existing_ids.add(str(rec.id))
            if next_offset is None:
                break

        chunk_points: list[qm.PointStruct] = []
        for bundle_dir in sorted(self.settings.okf_dir.iterdir()):
            if not bundle_dir.is_dir():
                continue
            doc_id = bundle_dir.name
            chunks_dir = bundle_dir / "chunks"
            if not chunks_dir.is_dir():
                continue

            chunk_texts: list[str] = []
            chunk_indices: list[int] = []
            for md in sorted(chunks_dir.glob("chunk_*.md")):
                idx = int(md.stem.split("_")[-1])
                chunk_texts.append(md.read_text(encoding="utf-8"))
                chunk_indices.append(idx)

            if not chunk_texts:
                continue

            # Извлекаем section_title и global_tags
            from app.services.pipeline import _extract_section_title

            section_titles = [_extract_section_title(t) for t in chunk_texts]
            cap = self.settings.okf_max_chunk_index_chars
            embed_inputs = []
            for st, t in zip(section_titles, chunk_texts):
                embed_inputs.append(f"{st}\n{t[:cap]}" if st else t[:cap])
            vectors = embedder.embed_texts(embed_inputs)

            global_tags: list[str] = []
            for md in sorted(bundle_dir.glob("*.md")):
                if md.parent.name == "chunks":
                    continue
                frontmatter = _read_frontmatter(md)
                gt = frontmatter.get("global_tags", [])
                if isinstance(gt, list) and gt:
                    global_tags = gt
                    break

            for i, (text, vec, st) in enumerate(zip(chunk_texts, vectors, section_titles)):
                idx = chunk_indices[i]
                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"chunk:{doc_id}/chunks/chunk_{idx:02d}.md"))
                capped = text[:cap]
                sparse_text = f"{st}\n{capped}" if st else capped
                chunk_points.append(
                    qm.PointStruct(
                        id=point_id,
                        vector={
                            "": vec,
                            SPARSE_VECTOR_NAME: to_sparse_vector(sparse_text),
                        },
                        payload={
                            "point_type": CHUNK_POINT_TYPE,
                            "doc_id": doc_id,
                            "chunk_index": idx,
                            "tags": global_tags,
                            "section_title": st,
                            "content": capped,
                        },
                    )
                )

        total = len(chunk_points)
        for start in range(0, total, batch_size):
            batch = chunk_points[start : start + batch_size]
            self.client.upsert(collection_name=self.collection, points=batch)
        return total

    def backfill_relations(self, batch_size: int = 500) -> int:
        """Нормализует relations в payload концептов из OKF-бандлов на диске.

        Перечитывает .md-бандлы, парсит frontmatter, нормализует relations через
        _parse_relation. Сравнивает с текущим значением в Qdrant — пропускает
        неизменившиеся точки. Группирует изменившиеся по значению relations и
        обновляет одним set_payload на группу (вместо per-point вызовов).
        Идемпотентно.
        """
        from app.services.okf_generator import _parse_relation

        if not self.settings.okf_dir.is_dir():
            return 0

        # 1. Собираем нормализованные relations из бандлов на диске
        disk_relations: dict[str, list[str]] = {}
        for bundle_dir in sorted(self.settings.okf_dir.iterdir()):
            if not bundle_dir.is_dir():
                continue
            for md in sorted(bundle_dir.glob("*.md")):
                if md.parent.name == "chunks":
                    continue
                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(md)))
                frontmatter = _read_frontmatter(md)
                raw_relations = frontmatter.get("relations", [])
                if not isinstance(raw_relations, list):
                    continue
                normalized = [_parse_relation(str(r)) for r in raw_relations if str(r).strip()]
                disk_relations[point_id] = normalized

        if not disk_relations:
            return 0

        # 2. Scroll существующих точек, получаем текущие relations
        existing: dict[str, list[str]] = {}
        next_offset = None
        while True:
            batch, next_offset = self.client.scroll(
                collection_name=self.collection,
                limit=batch_size,
                with_payload=True,
                with_vectors=False,
                offset=next_offset,
            )
            for rec in batch:
                pid = str(rec.id)
                if pid in disk_relations:
                    payload = rec.payload or {}
                    existing[pid] = list(payload.get("relations", []))
            if next_offset is None:
                break

        # 3. Фильтруем: только изменившиеся
        changed: dict[str, list[str]] = {}
        for pid, normalized in disk_relations.items():
            current = existing.get(pid)
            if current is None:
                continue  # точка удалена из Qdrant
            if list(current) != normalized:
                changed[pid] = normalized

        if not changed:
            return 0

        # 4. Группируем по значению relations → один set_payload на группу
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


def _read_frontmatter(path: Path) -> dict:
    """Читает YAML frontmatter из .md файла (между --- и ---)."""
    import yaml

    try:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return {}
        end = text.find("\n---", 4)
        if end == -1:
            return {}
        frontmatter_text = text[4:end]
        result = yaml.safe_load(frontmatter_text)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}