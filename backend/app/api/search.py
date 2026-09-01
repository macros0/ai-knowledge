# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Роут умного поиска: dense / BM25 по концептам и чанкам."""
from fastapi import APIRouter

from app.config import get_settings
from app.models.schemas import SearchHit, SearchRequest, SearchResponse
from app.services.concept_store import enrich_concept_hits
from app.services.context_builder import merge_and_format, resolve_branches
from app.services.embedder import Embedder
from app.services.search_filter import build_doc_lookup, drop_invisible_hits
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

    # Defense-in-depth к Qdrant-фильтру `must_not deleted` — единое место
    # (services/search_filter.py): гонка софт-делита (payload не синхронизирован)
    # и orphan-точки (документа нет в БД — восстановлен во время purge или сбой).
    doc_lookup = build_doc_lookup(hits)
    hits = drop_invisible_hits(hits, doc_lookup)

    enrich_concept_hits(hits)

    filename_lookup = {did: (d or {}).get("filename", "") for did, d in doc_lookup.items()}
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
