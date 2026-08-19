"""Векторное хранилище на Qdrant: индекс концептов, три режима поиска с фильтром по тегам.

Режимы:
  - dense  — семантический поиск по dense-вектору (default-вектор коллекции);
  - bm25   — лексический поиск по sparse-вектору (BM25, Qdrant применяет IDF);
  - hybrid — prefetch обоих + fusion (RRF).

Коллекция хранит dense-вектор как безымянный (default) и sparse-вектор как
именованный "sparse". Добавление sparse к уже существующей коллекции делается
без пересоздания через update_collection; старые точки добиваются через
update_vectors (без перезаписи dense и payload).
"""
import uuid
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.config import get_settings
from app.models.schemas import OkfDocument
from app.services.sparse import to_sparse_vector

SPARSE_VECTOR_NAME = "sparse"
SEARCH_MODES = ("dense", "bm25", "hybrid")


class VectorStore:
    def __init__(self):
        self.settings = get_settings()
        self.client = QdrantClient(url=self.settings.qdrant_url)

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
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=qm.VectorParams(
                    size=self.settings.embedding_dimensions,
                    distance=qm.Distance.COSINE,
                ),
                sparse_vectors_config=self._sparse_params(),
            )
            return
        info = self.client.get_collection(self.collection)
        sparse_config = info.config.params.sparse_vectors
        if not sparse_config or SPARSE_VECTOR_NAME not in sparse_config:
            self.client.create_vector_name(
                collection_name=self.collection,
                vector_name=SPARSE_VECTOR_NAME,
                vector_name_config=self._sparse_name_config(),
            )

    def index_concepts(self, doc_id: str, okf_docs: list[OkfDocument], vectors: list[list[float]]) -> None:
        cap = self.settings.okf_max_concept_chars
        points = []
        for okf_doc, vector in zip(okf_docs, vectors):
            meta = okf_doc.metadata
            point_id = uuid.uuid5(uuid.NAMESPACE_URL, okf_doc.filepath)
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
                        "doc_id": doc_id,
                        "filepath": okf_doc.filepath,
                        "title": title,
                        "type": meta.get("type", "concept"),
                        "tags": meta.get("tags", []),
                        "global_tags": meta.get("global_tags", []),
                        "source_document": meta.get("source_document", {}),
                        "attachments": meta.get("attachments", []),
                        "content": capped_content,
                    },
                )
            )
        if points:
            self.client.upsert(collection_name=self.collection, points=points)

    def delete_document(self, doc_id: str) -> None:
        self.client.delete(
            collection_name=self.collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
            ),
        )

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