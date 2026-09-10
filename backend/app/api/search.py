# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Роут умного поиска: dense / BM25 по концептам и чанкам."""
from functools import lru_cache

from fastapi import APIRouter, Depends

from app.auth.models import User
from app.auth.service import require_user
from app.config import get_settings
from app.models.schemas import SearchHit, SearchRequest, SearchResponse
from app.services.chunk_store import enrich_chunk_hits
from app.services.concept_store import enrich_concept_hits
from app.services.context_builder import merge_and_format, resolve_branches
from app.services.embedder import Embedder
from app.services.search_filter import build_doc_lookup, drop_invisible_hits
from app.services.sparse import to_sparse_vector
from app.services.stopwords import KIND_BM25, get_stopwords
from app.services.vector_store import VectorStore
from app.services.rate_limiter import RateLimitExceeded, get_rate_limiter
from app.api import errors
from app.api.errors import ApiError

router = APIRouter(prefix="/search", tags=["search"])

# Ленивые синглтоны: как в chat.py, конструкторы не должны выполняться на
# импорте модуля — VectorStore открывает клиент к Qdrant, а `import app.main`
# обязан проходить на холодном окружении (мягкий старт живёт в lifespan).


@lru_cache(maxsize=1)
def _get_embedder() -> Embedder:
    return Embedder()


@lru_cache(maxsize=1)
def _get_vector_store() -> VectorStore:
    return VectorStore()


@router.post("", response_model=SearchResponse)
def search(req: SearchRequest, current_user: User = Depends(require_user)):
    settings = get_settings()
    try:
        get_rate_limiter().check_action(
            current_user.user_id,
            "search",
            max_requests=settings.search_rate_limit_per_minute,
        )
    except RateLimitExceeded as exc:
        raise ApiError(
            status_code=429,
            code=errors.RATE_LIMITED,
            detail=str(exc),
            headers={"Retry-After": str(max(1, int(exc.retry_after)))},
        ) from exc
    branches = resolve_branches(req.mode, req.dense, req.bm25, settings)
    vector = _get_embedder().embed(req.query) if "dense" in branches else None
    sparse_vec = (
        to_sparse_vector(req.query, stopwords=get_stopwords(KIND_BM25))
        if "bm25" in branches
        else None
    )

    hits = _get_vector_store().search_composite(
        dense_vec=vector,
        sparse_vec=sparse_vec,
        tags=req.tags or None,
        branches=branches,
        source_locales=req.source_locales or None,
        include_unknown_source_locale=req.include_unknown_source_locale,
        # Как в chat.py: берём широкий набор точек, режем по БЛОКАМ после merge —
        # иначе концепты-сиблинги группы с более низким fused-рангом не доживают
        # до группировки.
        top_k=settings.search_per_branch_top_k,
    )

    # Defense-in-depth к Qdrant-фильтру `must_not deleted` — единое место
    # (services/search_filter.py): гонка софт-делита (payload не синхронизирован)
    # и orphan-точки (документа нет в БД — восстановлен во время purge или сбой).
    doc_lookup = build_doc_lookup(hits)
    hits = drop_invisible_hits(hits, doc_lookup)

    enrich_concept_hits(hits)
    enrich_chunk_hits(hits)

    filename_lookup = {did: (d or {}).get("filename", "") for did, d in doc_lookup.items()}
    merged = merge_and_format(hits, settings, filename_lookup=filename_lookup)
    # top_k — число БЛОКОВ (merge-групп с сиблингами), не точек.
    merged = merged[: req.top_k]
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
