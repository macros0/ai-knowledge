# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Роут чата: RAG — композитный поиск (dense/BM25 + чанки) + синтез ответа LLM."""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.auth.models import User
from app.auth.service import require_user
from app.config import get_settings
from app.models.schemas import ChatRequest, ChatResponse, ChatSource
from app.prompts.store import get_store
from app.services import chat_history
from app.services.concept_store import enrich_concept_hits
from app.services.context_builder import format_context, merge_and_format, resolve_branches
from app.services.embedder import Embedder
from app.services.errors import LLMError
from app.services.llm_client import LLMClient
from app.services.registry import get_registry
from app.services.sparse import to_sparse_vector
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_embedder = Embedder()
_vector_store = VectorStore()
_llm = LLMClient(interactive=True)
_prompts = get_store()


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest, current_user: User = Depends(require_user)):
    settings = get_settings()
    if req.session_id and not chat_history.is_valid_session_id(req.session_id):
        raise HTTPException(status_code=422, detail="session_id должен быть UUID")
    # Ранняя проверка состояния треда ДО тяжёлой работы (embed → search → LLM):
    # не тратим LLM-вызов на запрос, чей результат всё равно не сохранится.
    try:
        if req.session_id:
            chat_history.check_session_state(req.session_id, current_user.user_id)
    except chat_history.ChatOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except chat_history.ChatSessionDeletedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

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
    # Defense-in-depth к Qdrant-фильтру `must_not deleted`: отсекает хиты, чей
    # документ удалён в БД, но payload ещё не синхронизирован (гонка софт-делита).
    reg = get_registry()
    doc_lookup = {
        did: reg.get(did)
        for did in {h.payload.get("doc_id", "") for h in hits}
        if did
    }
    hits = [h for h in hits if not (doc_lookup.get(h.payload.get("doc_id", "")) or {}).get("deleted_at")]
    # Короткое замыкание (Этап 4a.1): при нуле хитов не зовём LLM — ответ
    # без источников формируется здесь, фронтенд по пустому `sources` покажет
    # переход «загрузить документ» при активном фильтре модуля/разработки.
    if not hits:
        answer = "Источники не найдены. Попробуйте изменить запрос или убрать фильтры."
        sources: list[ChatSource] = []
    else:
        enrich_concept_hits(hits)

        filename_lookup = {did: (d or {}).get("filename", "") for did, d in doc_lookup.items()}
        merged = merge_and_format(hits, settings, filename_lookup=filename_lookup)
        context = format_context(merged)
        max_score = max((m["score"] for m in merged), default=0.0)
        sources = []
        for m in merged:
            score = m["score"] / max_score if max_score > 0 else m["score"]
            src_doc = doc_lookup.get(m["doc_id"]) or {}
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
                    development_number=src_doc.get("development_number"),
                    development_name=src_doc.get("development_name"),
                    development_module=src_doc.get("development_module"),
                )
            )

        system = _prompts.get("chat_system")
        prompt_user = _prompts.format("chat_user", context=context, query=req.query)
        try:
            answer = _llm.chat(system, prompt_user)
        except Exception as exc:
            raise LLMError(cause=exc) from exc

    # Персистентная история (Этап 6): запись не блокирует ответ — сбой БД не
    # роняет чат. session_id привязан к текущему пользователю на стороне сервиса.
    session_id = req.session_id
    try:
        session_id = chat_history.store_turn(
            req.session_id,
            current_user,
            req.query,
            answer,
            [s.model_dump() for s in sources],
        )
    except chat_history.ChatOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except chat_history.ChatSessionDeletedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        logger.warning("Не удалось сохранить историю чата", exc_info=True)

    return ChatResponse(
        query=req.query,
        answer=answer,
        sources=sources,
        session_id=session_id,
    )
