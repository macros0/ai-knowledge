"""Векторное хранилище на Qdrant: индекс концептов, гибридный поиск с фильтром по тегам."""
import uuid

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.config import get_settings
from app.models.schemas import OkfDocument


class VectorStore:
    def __init__(self):
        self.settings = get_settings()
        self.client = QdrantClient(url=self.settings.qdrant_url)

    @property
    def collection(self) -> str:
        return self.settings.qdrant_collection

    def ensure_collection(self) -> None:
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=qm.VectorParams(
                    size=self.settings.embedding_dim,
                    distance=qm.Distance.COSINE,
                ),
            )

    def index_concepts(self, doc_id: str, okf_docs: list[OkfDocument], vectors: list[list[float]]) -> None:
        points = []
        for okf_doc, vector in zip(okf_docs, vectors):
            meta = okf_doc.metadata
            point_id = uuid.uuid5(uuid.NAMESPACE_URL, okf_doc.filepath)
            points.append(
                qm.PointStruct(
                    id=str(point_id),
                    vector=vector,
                    payload={
                        "doc_id": doc_id,
                        "filepath": okf_doc.filepath,
                        "title": meta.get("title", ""),
                        "type": meta.get("type", "concept"),
                        "tags": meta.get("tags", []),
                        "global_tags": meta.get("global_tags", []),
                        "source_document": meta.get("source_document", {}),
                        "attachments": meta.get("attachments", []),
                        "content": okf_doc.content[:4000],
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

    def search(self, vector: list[float], tags: list[str] | None = None, top_k: int = 5) -> list[dict]:
        query_filter = None
        if tags:
            query_filter = qm.Filter(must=[qm.FieldCondition(key="tags", match=qm.MatchAny(any=tags))])
        results = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=query_filter,
            limit=top_k,
        )
        return [{"score": r.score, "payload": r.payload} for r in results.points]
