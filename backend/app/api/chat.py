# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Роут чата: RAG — композитный поиск (dense/BM25 + чанки) + синтез ответа LLM."""
import logging
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

from app.api import errors
from app.api.errors import ApiError
from fastapi import APIRouter, Depends

from app.auth.models import User
from app.auth.service import require_user
from app.config import get_settings
from app.models.schemas import ChatRequest, ChatResponse, ChatSource
from app.prompts.store import get_store
from app.services import chat_history
from app.services.citation import normalize_citations
from app.services.retrieval_hydration import load_visible_retrieval_hits
from app.services.context_builder import (
    drop_partial_title_matches,
    drop_unmatched_blocks,
    format_context,
    merge_and_format,
    resolve_branches,
)
from app.services.embedder import Embedder
from app.services.errors import LLMError
from app.services.llm_client import LLMClient
from app.services.rate_limiter import RateLimitExceeded, get_rate_limiter
from app.services.stopwords import KIND_BM25, get_stopwords
from app.services.vector_store import VectorStore
from app.services.glossary.expansion import prepare_query
from app.services.glossary.matching import exact_excerpt
from app.services.glossary.query_sparse import build_query_sparse
from app.services.glossary.snapshot import GlossaryMigrationRequiredError
from app.services.ui_dictionary import localized_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# Ленивые синглтоны вместо конструирования на импорте модуля: конструкторы
# читают конфигурацию, а VectorStore ещё и открывает клиент к Qdrant (сетевой
# вызов при создании). `import app.main` обязан проходить на холодном окружении
# — до готового Qdrant и накатанных миграций; проверка внешних зависимостей
# живёт в lifespan, где недоступный сервис не валит процесс (мягкий старт).
# PromptStore — уже ленивый синглтон, поэтому берётся через get_store().


@lru_cache(maxsize=1)
def _get_embedder() -> Embedder:
    return Embedder()


@lru_cache(maxsize=1)
def _get_vector_store() -> VectorStore:
    return VectorStore()


@lru_cache(maxsize=1)
def _get_llm() -> LLMClient:
    return LLMClient(interactive=True)


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest, current_user: User = Depends(require_user)):
    settings = get_settings()
    try:
        get_rate_limiter().check_action(
            current_user.user_id,
            "chat",
            max_requests=settings.chat_rate_limit_per_minute,
        )
    except RateLimitExceeded as exc:
        raise ApiError(
            status_code=429,
            code=errors.RATE_LIMITED,
            detail=str(exc),
            headers={"Retry-After": str(max(1, int(exc.retry_after)))},
        ) from exc
    if req.session_id and not chat_history.is_valid_session_id(req.session_id):
        raise ApiError(
            status_code=422,
            code=errors.INVALID_REQUEST,
            detail="session_id должен быть UUID",
        )
    # Ранняя проверка состояния треда ДО тяжёлой работы (embed → search → LLM):
    # не тратим LLM-вызов на запрос, чей результат всё равно не сохранится.
    try:
        if req.session_id:
            chat_history.check_session_state(req.session_id, current_user.user_id)
    except chat_history.ChatOwnershipError as exc:
        raise ApiError(
            status_code=403,
            code=errors.FORBIDDEN,
            detail=str(exc),
        ) from exc
    except chat_history.ChatSessionDeletedError as exc:
        raise ApiError(
            status_code=409,
            code=errors.CONFLICT,
            detail=str(exc),
        ) from exc

    try:
        plan = prepare_query(
            req.query,
            ui_locale=req.locale,
            enabled=settings.glossary_query_expansion_enabled and req.use_glossary,
            settings=settings,
        )
    except GlossaryMigrationRequiredError as exc:
        raise ApiError(status_code=503, code=errors.GLOSSARY_MIGRATION_REQUIRED, detail=str(exc)) from exc
    branches = resolve_branches(req.mode, req.dense, req.bm25, settings)
    vector = _get_embedder().embed(plan.dense_query) if "dense" in branches else None
    # Query-путь: динамический набор стоп-слов активных locales (индексная формула
    # заморожена — реиндекс при правке стоп-слов не требуется).
    sparse_vec = (
        build_query_sparse(
            plan,
            stopwords=get_stopwords(KIND_BM25),
            settings=settings,
        )
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
        # Берём широкий набор точек (per_branch_top_k): итог режем по БЛОКАМ после
        # merge (группы (doc_id, chunk_index) + сиблинг-концепты). Срез по точкам
        # до группировки ронял концепты-сиблинги с более низким fused-рангом
        # (таблица «Перечень: Наименование поля» при bm25-ранге #5) — они не
        # доживали до merge и не попадали ни в контекст LLM, ни в sources.
        top_k=settings.search_per_branch_top_k,
    )
    exact_groups = plan.strict_groups or plan.match_groups
    # Defense-in-depth к Qdrant-фильтру `must_not deleted` — единое место
    # (services/search_filter.py): гонка софт-делита (payload не синхронизирован)
    # и orphan-точки (документа нет в БД — восстановлен во время purge или сбой).
    hits, doc_lookup = load_visible_retrieval_hits(hits, **({"exact_groups": exact_groups} if exact_groups else {}))
    # Короткое замыкание (Этап 4a.1): при нуле хитов не зовём LLM — ответ
    # без источников формируется здесь, фронтенд по пустому `sources` покажет
    # переход «загрузить документ» при активном фильтре модуля/разработки.
    if not hits:
        answer = localized_message('chat.noSources', req.locale, russian_fallback='Источники не найдены.')
        sources: list[ChatSource] = []
    else:
        # Этап 2b: полный текст чанков — из document_chunks (natural key), а не
        # из payload Qdrant. Вызывается до merge_and_format, который читает
        # payload["content"]/["section_title"] чанк-точек.

        filename_lookup = {did: (d or {}).get("filename", "") for did, d in doc_lookup.items()}
        merged = merge_and_format(hits, settings, filename_lookup=filename_lookup, exact_groups=exact_groups)
        # top_k — число БЛОКОВ в контексте/источниках (группы с сиблингами), не точек.
        merged = merged[: req.top_k]
        domain_cache = {}
        lexical_cache = {}
        # Анти-шум: блоки без лексического совпадения с запросом не доходят до
        # LLM и sources — модели периодически вписывают их в ответ не по теме
        # (пустой Matched terms игнорируется даже сильными моделями). При
        # полном отсутствии совпадений (парафразный запрос) фильтр пропускает всё.
        merged = drop_unmatched_blocks(
            merged,
            req.query,
            match_groups=exact_groups,
            domain_cache=domain_cache,
            lexical_cache=lexical_cache,
        )
        # Запрос-точное-имя: если какой-то заголовок покрывает ВСЕ термины запроса,
        # контекст ограничивается блоками «про объект» — смежные блоки, где объект
        # лишь упомянут в теле, модели сливают в описание объекта.
        merged = drop_partial_title_matches(
            merged,
            req.query,
            match_groups=exact_groups,
            domain_cache=domain_cache,
        )
        context = format_context(
            merged,
            query=req.query,
            match_groups=exact_groups,
            domain_cache=domain_cache,
            lexical_cache=lexical_cache,
        )
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
                    snippet=exact_excerpt(m["content"], 200, exact_groups),
                    point_type=m["point_type"],
                    chunk_index=m["chunk_index"],
                    development_number=src_doc.get("development_number"),
                    development_name=src_doc.get("development_name"),
                    development_module=src_doc.get("development_module"),
                )
            )

        if not merged:
            answer = localized_message('chat.noSources', req.locale, russian_fallback='Источники не найдены.')
        else:
            system = get_store().format("chat_system", locale=req.locale)
            prompt_user = get_store().format("chat_user", context=context, query=req.query)
            try:
                answer = _get_llm().chat(system, prompt_user)
            except Exception as exc:
                raise LLMError(cause=exc) from exc
            # Normalize citations only after an answer was produced from sources.
            answer = normalize_citations(answer, max_index=len(merged))

    used_in = [branch for branch in ("dense", "bm25") if branch in branches]
    applied_terms = [
        {**asdict(term), "used_in": used_in}
        for term in plan.applied_terms
    ]
    retrieval_metadata = {
        "schema_version": 1,
        "expansion_status": plan.status,
        "applied_terms": applied_terms,
        "rules_version": plan.rules_version,
        "ui_locale": req.locale,
    }

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
            retrieval_metadata=retrieval_metadata,
        )
    except chat_history.ChatOwnershipError as exc:
        raise ApiError(
            status_code=403,
            code=errors.FORBIDDEN,
            detail=str(exc),
        ) from exc
    except chat_history.ChatSessionDeletedError as exc:
        raise ApiError(
            status_code=409,
            code=errors.CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.warning("Не удалось сохранить историю чата", exc_info=True)

    return ChatResponse(
        query=req.query,
        answer=answer,
        sources=sources,
        session_id=session_id,
        expansion_status=plan.status,
        applied_terms=applied_terms,
    )
