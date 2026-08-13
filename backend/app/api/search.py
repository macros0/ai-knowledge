"""Роут умного поиска: семантический поиск по концептам с фильтром по тегам."""
from fastapi import APIRouter

from app.models.schemas import SearchHit, SearchRequest, SearchResponse
from app.services.embedder import Embedder
from app.services.vector_store import VectorStore

router = APIRouter(prefix="/search", tags=["search"])

_embedder = Embedder()
_vector_store = VectorStore()


@router.post("", response_model=SearchResponse)
def search(req: SearchRequest):
    vector = _embedder.embed(req.query)
    hits = _vector_store.search(vector, tags=req.tags or None, top_k=req.top_k)
    result = []
    for hit in hits:
        payload = hit["payload"]
        content = payload.get("content", "")
        result.append(
            SearchHit(
                score=round(hit["score"], 4),
                title=payload.get("title", ""),
                type=payload.get("type", "concept"),
                tags=payload.get("tags", []),
                filepath=payload.get("filepath", ""),
                snippet=content[:300],
            )
        )
    return SearchResponse(query=req.query, hits=result)
