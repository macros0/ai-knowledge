"""Роут чата: RAG — композитный поиск (dense/BM25 + чанки) + синтез ответа LLM."""
from pathlib import Path

from fastapi import APIRouter

from app.config import get_settings
from app.models.schemas import ChatRequest, ChatResponse, ChatSource
from app.prompts.store import get_store
from app.services.context_builder import format_context, merge_and_format, resolve_branches
from app.services.embedder import Embedder
from app.services.errors import LLMError
from app.services.llm_client import LLMClient
from app.services.registry import get_registry
from app.services.sparse import to_sparse_vector
from app.services.vector_store import VectorStore

router = APIRouter(prefix="/chat", tags=["chat"])

_embedder = Embedder()
_vector_store = VectorStore()
_llm = LLMClient(interactive=True)
_prompts = get_store()


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest):
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
    context = format_context(merged)
    max_score = max((m["score"] for m in merged), default=0.0)
    sources = []
    for m in merged:
        score = m["score"] / max_score if max_score > 0 else m["score"]
        sources.append(
            ChatSource(
                title=m["title"],
                filepath=m["filepath"],
                score=round(score, 4),
                tags=m["tags"],
                doc_id=m["doc_id"],
                filename=Path(m["filepath"]).name,
                snippet=m["content"][:200],
                point_type=m["point_type"],
                chunk_index=m["chunk_index"],
            )
        )

    system = _prompts.get("chat_system")
    user = _prompts.format("chat_user", context=context, query=req.query)
    try:
        answer = _llm.chat(system, user)
    except Exception as exc:
        raise LLMError(cause=exc) from exc
    return ChatResponse(query=req.query, answer=answer, sources=sources)
