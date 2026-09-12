"""Проверка здоровья зависимостей: LLM (OpenRouter), Ollama, Qdrant.

Результат кэшируется на _CACHE_TTL_SECONDS, чтобы не спамить внешние сервисы
при поллинге фронтенда. Каждая проверка имеет короткий таймаут (5с). TTL кэша
намеренно короче интервала поллинга фронтенда (30с), чтобы задержки не
складывались.
"""
import logging
import time

import httpx

from app.config import get_settings
from app.services.embedder import Embedder
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 20
_cache: dict[str, dict] = {}
_cache_ts: float = 0.0


def _check_llm() -> dict:
    """Пинг OpenRouter через бесплатный эндпоинт GET /api/v1/models."""
    settings = get_settings()
    base = (settings.llm_base_url or "https://openrouter.ai/api/v1").rstrip("/")
    try:
        resp = httpx.get(
            f"{base}/models",
            timeout=5.0,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {},
        )
        if resp.status_code < 400:
            return {"status": "ok"}
        if resp.status_code == 429:
            # Провайдер жив, но троттлит (общий пул) — это НЕ outage, не
            # должно переводить статус в degraded и пугать баннером.
            return {"status": "rate_limited", "error": "HTTP 429"}
        return {"status": "down", "error": f"HTTP {resp.status_code}"}
    except Exception as exc:
        return {"status": "down", "error": str(exc)[:120]}


def _check_embeddings() -> dict:
    try:
        if Embedder().ping():
            return {"status": "ok"}
        return {"status": "down", "error": "embedding call failed"}
    except Exception as exc:
        return {"status": "down", "error": str(exc)[:120]}


def _check_qdrant() -> dict:
    try:
        if VectorStore().ping():
            return {"status": "ok"}
        return {"status": "down", "error": "get_collections failed"}
    except Exception as exc:
        return {"status": "down", "error": str(exc)[:120]}


def _check_database() -> dict:
    """Проверка реляционной БД (SELECT 1)."""
    try:
        from sqlalchemy import text

        from app.db.session import get_engine

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "down", "error": str(exc)[:120]}


def get_health() -> dict:
    """Возвращает агрегированный статус здоровья зависимостей.

    Кэширует результат на _CACHE_TTL_SECONDS. Статус:
      - "ok" — все зависимости доступны (включая rate_limited: провайдер жив,
        просто троттлит — это не outage);
      - "degraded" — хотя бы одна зависимость "down", но Qdrant жив;
      - "down" — Qdrant недоступен (поиск невозможен).
    """
    global _cache, _cache_ts

    now = time.time()
    if _cache and (now - _cache_ts) < _CACHE_TTL_SECONDS:
        return _cache

    settings = get_settings()
    deps = {
        "llm": _check_llm(),
        "ollama": _check_embeddings(),
        "qdrant": _check_qdrant(),
        "database": _check_database(),
    }

    qdrant_ok = deps["qdrant"]["status"] == "ok"
    # Только реальный "down" считается проблемой; "rate_limited" не деградирует
    # общий статус (устраняет ложные тревоги при 429 OpenRouter).
    any_down = any(d["status"] == "down" for d in deps.values())

    if not qdrant_ok:
        overall = "down"
    elif any_down:
        overall = "degraded"
    else:
        overall = "ok"

    result = {
        "status": overall,
        # Диагностическая метка помогает отличить основную БД от
        # измерительного контура; секреты и connection URL здесь не выдаются.
        "knowledge_profile": settings.knowledge_profile,
        "dependencies": deps,
    }
    _cache = result
    _cache_ts = now
    return result
