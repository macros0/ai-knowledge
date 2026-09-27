# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Роут чата: RAG — композитный поиск (dense/BM25 + чанки) + синтез ответа LLM."""
import logging
import re
import threading
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

from app.api import errors
from app.api.errors import ApiError
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.auth.models import User
from app.auth.service import require_user
from app.config import Settings, get_settings
from app.models.schemas import ChatRequest, ChatResponse, ChatSource
from app.prompts.store import get_store
from app.services import chat_history
from app.services.citation import normalize_citations
from app.services.authorship_evidence import answer_authorship, is_authorship_query
from app.services.retrieval_hydration import load_visible_retrieval_hits
from app.services.context_builder import (
    drop_partial_title_matches,
    drop_unmatched_blocks,
    format_context,
    limit_context,
    merge_and_format,
    filter_mail_scope_blocks,
    resolve_branches,
)
from app.services.embedder import Embedder
from app.services.errors import LLMError
from app.services.llm_client import LLMBusyError, LLMClient
from app.services.llm_profiles import request_scope, remaining
from app.services.chat_stream import stream_chat
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


# Одновременные чаты процесса (CHAT_MAX_INFLIGHT). Семафор — по значению
# лимита, как слоты парсера: смена настройки не требует рестарта модуля.
_inflight_lock = threading.Lock()
_inflight_by_limit: dict[int, threading.BoundedSemaphore] = {}
_BUSY_RETRY_AFTER_SECONDS = 5


def _busy(detail: str, retry_after: float = _BUSY_RETRY_AFTER_SECONDS) -> ApiError:
    return ApiError(
        status_code=429,
        code=errors.RATE_LIMITED,
        detail=detail,
        headers={"Retry-After": str(max(1, int(retry_after)))},
    )


def _admit_chat(limit: int) -> threading.BoundedSemaphore:
    """Неблокирующий допуск: лишний чат сразу получает 429, а не занимает поток пула."""
    with _inflight_lock:
        slots = _inflight_by_limit.setdefault(limit, threading.BoundedSemaphore(limit))
    if not slots.acquire(blocking=False):
        logger.warning("Чат отклонён: заняты все %d слотов CHAT_MAX_INFLIGHT", limit)
        raise _busy("Сервис ответов перегружен. Повторите попытку позже.")
    return slots


_LANGUAGE_NEUTRAL_QUERY = re.compile(r"[\s\d\W_А-ЯЁA-Z]+", re.UNICODE)
_NEUTRAL_RESPONSE_LANGUAGE = {
    "ru": "Язык ответа — русский. Запрос состоит только из кода или сокращения; "
          "весь ответ, включая пояснения и подписи источников, пиши по-русски.",
    "en": "Response language is English. The query contains only a code or abbreviation; "
          "write the entire answer, including explanations and source labels, in English.",
}


def _chat_system_prompt(query: str, locale: str, settings: Settings) -> str:
    """Build the final chat instruction, making neutral-code fallback explicit."""
    system = get_store().format("chat_system", locale=locale)
    if settings.llm_profile == "local_qwen" and settings.llm_local_chat_instructions:
        system += "\n\nResponse style:\n" + settings.llm_local_chat_instructions
    if _LANGUAGE_NEUTRAL_QUERY.fullmatch(query):
        language = _NEUTRAL_RESPONSE_LANGUAGE.get(locale.lower().split("-", 1)[0])
        if language:
            system += "\n\n" + language
    return system


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

    # Допуск после дешёвых проверок, до embed → поиск → LLM: всё это время
    # запрос держит поток общего пула синхронных эндпоинтов.
    slots = _admit_chat(settings.chat_max_inflight)
    try:
        return _answer(req, current_user, settings)
    finally:
        slots.release()


def _answer(req: ChatRequest, current_user: User, settings: Settings) -> ChatResponse:
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
        mail_mode=req.mail_mode,
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
    hits, doc_lookup = load_visible_retrieval_hits(hits, mail_mode=req.mail_mode, **({"exact_groups": exact_groups} if exact_groups else {}))
    # Короткое замыкание (Этап 4a.1): при нуле хитов не зовём LLM — ответ
    # без источников формируется здесь, фронтенд по пустому `sources` покажет
    # переход «загрузить документ» при активном фильтре модуля/разработки.
    if not hits:
        answer = _no_sources_answer(req)
        sources: list[ChatSource] = []
    else:
        # Этап 2b: полный текст чанков — из document_chunks (natural key), а не
        # из payload Qdrant. Вызывается до merge_and_format, который читает
        # payload["content"]/["section_title"] чанк-точек.

        filename_lookup = {did: (d or {}).get("filename", "") for did, d in doc_lookup.items()}
        merged = merge_and_format(hits, settings, filename_lookup=filename_lookup, exact_groups=exact_groups, mail_mode=req.mail_mode)
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
            focus_named_objects=settings.chat_focus_named_objects,
        )
        merged = limit_context(
            merged,
            settings.chat_max_context_chars,
            query=req.query,
            match_groups=exact_groups,
            domain_cache=domain_cache,
            lexical_cache=lexical_cache,
            strict=settings.llm_profile == "local_qwen",
        )
        merged = filter_mail_scope_blocks(merged, mail_mode=req.mail_mode)
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
                    source_id=m.get("source_id"),
                    source_path=m.get("source_path"),
                    development_number=src_doc.get("development_number"),
                    development_name=src_doc.get("development_name"),
                    development_module=src_doc.get("development_module"),
                )
            )

        if not merged:
            answer = _no_sources_answer(req)
        else:
            system = _chat_system_prompt(req.query, req.locale, settings)
            prompt_user = get_store().format("chat_user", context=context, query=req.query)
            try:
                if is_authorship_query(req.query):
                    # Internal extraction JSON is not a user-facing answer.
                    with request_scope(on_text=None):
                        answer = answer_authorship(
                            req.query, merged, _get_llm(), locale=req.locale,
                            max_chars=settings.chat_max_context_chars, mail_mode=req.mail_mode,
                        )
                else:
                    answer = _get_llm().chat(system, prompt_user)
            except LLMBusyError as exc:
                raise _busy("Все слоты генерации ответа заняты. Повторите попытку позже.", exc.retry_after) from exc
            except Exception as exc:
                raise LLMError(cause=exc) from exc
            # Normalize citations only after an answer was produced from sources.
            if not is_authorship_query(req.query):
                answer = normalize_citations(answer, max_index=len(merged))

    used_in = [branch for branch in ("dense", "bm25") if branch in branches]
    applied_terms = [
        {**asdict(term), "used_in": used_in}
        for term in plan.applied_terms
    ]
    retrieval_metadata = {
        "schema_version": 1,
        "mail_mode": req.mail_mode,
        "expansion_status": plan.status,
        "applied_terms": applied_terms,
        "rules_version": plan.rules_version,
        "ui_locale": req.locale,
    }

    # Персистентная история (Этап 6): запись не блокирует ответ — сбой БД не
    # роняет чат. session_id привязан к текущему пользователю на стороне сервиса.
    session_id = req.session_id
    remaining(settings.llm_chat_total_timeout_seconds)
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


@router.post("/stream")
def chat_stream(req: ChatRequest, current_user: User = Depends(require_user)):
    return StreamingResponse(
        stream_chat(lambda: chat(req, current_user)),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _no_sources_answer(req):
    if req.mail_mode == "all":
        return localized_message('chat.noSources', req.locale, russian_fallback='Источники не найдены.')
    fallback = ('No sources match the selected filters. Change the filters and try again.'
                if req.locale.lower().startswith('en') else
                'Источники не найдены с учётом выбранных фильтров. Измените фильтры и повторите запрос.')
    return localized_message('chat.noSourcesMailFilter', req.locale, russian_fallback=fallback)
