"""Роут умного поиска: dense / BM25 по концептам и чанкам."""
from fastapi import APIRouter

from app.config import get_settings
from app.models.schemas import SearchHit, SearchRequest, SearchResponse
from app.services.context_builder import merge_and_format, resolve_branches
from app.services.embedder import Embedder
from app.services.registry import get_registry
from app.services.sparse import to_sparse_vector
from app.services.vector_store import VectorStore

router = APIRouter(prefix="/search", tags=["search"])

_embedder = Embedder()
_vector_store = VectorStore()


@router.post("", response_model=SearchResponse)
def search(req: SearchRequest):
    settings = get_settings()
    branches = resolve_branches(req.mode, req.dense, req.bm25, settings)
    vector = _embedder.embed(req.query) if "dense" in branches else None
    sparse_vec = to_sparse_vector(req.query) if "bm25" in branches else None

    hits = _vector_store.search_composite(
        dense_vec=vector,
        sparse_vec=sparse_vec,
        tags=req.tags or None,
        branches=branches,
        top_k=req.top_k,
    )

    reg = get_registry()
    filename_lookup = {did: (reg.get(did) or {}).get("filename", "")
                       for did in {h.payload.get("doc_id", "") for h in hits}}
    merged = merge_and_format(hits, settings, filename_lookup=filename_lookup)
    max_score = max((m["score"] for m in merged), default=0.0)
    result = []
    for m in merged:
        score = m["score"] / max_score if max_score > 0 else m["score"]
        result.append(
            SearchHit(
                score=round(score, 4),
                title=m["title"],
                type="raw_text" if m["point_type"] == "chunk" else "concept",
                point_type=m["point_type"],
                tags=m["tags"],
                filepath=m["filepath"],
                snippet=m["content"][:300],
                chunk_index=m["chunk_index"],
                source_filename=m["source_filename"],
            )
        )
    return SearchResponse(query=req.query, hits=result)
