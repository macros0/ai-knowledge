"""Роут умного поиска: семантический / BM25 / гибридный поиск по концептам с фильтром по тегам."""
from fastapi import APIRouter

from app.models.schemas import SearchHit, SearchRequest, SearchResponse
from app.services.embedder import Embedder
from app.services.sparse import to_sparse_vector
from app.services.vector_store import VectorStore

router = APIRouter(prefix="/search", tags=["search"])

_embedder = Embedder()
_vector_store = VectorStore()


@router.post("", response_model=SearchResponse)
def search(req: SearchRequest):
    hits = _run_search(req.query, req.tags or None, req.top_k, req.mode)
    return SearchResponse(query=req.query, hits=hits)


def _run_search(query: str, tags: list[str] | None, top_k: int, mode: str) -> list[SearchHit]:
    vector = _embedder.embed(query) if mode in ("dense", "hybrid") else None
    sparse_vec = to_sparse_vector(query) if mode in ("bm25", "hybrid") else None
    raw_hits = _vector_store.search(vector, sparse_vec, mode=mode, tags=tags, top_k=top_k)

    max_score = max((h["score"] for h in raw_hits), default=0.0)
    result = []
    for hit in raw_hits:
        payload = hit["payload"]
        content = payload.get("content", "")
        score = hit["score"]
        if mode in ("bm25", "hybrid") and max_score > 0:
            score = score / max_score
        result.append(
            SearchHit(
                score=round(score, 4),
                title=payload.get("title", ""),
                type=payload.get("type", "concept"),
                tags=payload.get("tags", []),
                filepath=payload.get("filepath", ""),
                snippet=content[:300],
            )
        )
    return result