"""Проверка здоровья зависимостей: LLM (OpenRouter), Ollama, Qdrant.

Результат кэшируется на 30 секунд, чтобы не спамить внешние сервисы
при поллинге фронтенда. Каждая проверка имеет короткий таймаут (2-3с).
"""
import logging
import time

import httpx

from app.config import get_settings
from app.services.embedder import Embedder
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30
_cache: dict[str, dict] = {}
_cache_ts: float = 0.0


def _check_llm() -> dict:
    """Пинг OpenRouter через бесплатный эндпоинт GET /api/v1/models."""
    settings = get_settings()
    base = (settings.llm_base_url or "https://openrouter.ai/api/v1").rstrip("/")
    try:
        resp = httpx.get(
            f"{base}/models",
            timeout=3.0,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {},
        )
        if resp.status_code < 400:
            return {"status": "ok"}
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


def get_health() -> dict:
    """Возвращает агрегированный статус здоровья зависимостей.

    Кэширует результат на _CACHE_TTL_SECONDS. Статус:
      - "ok" — все зависимости доступны
      - "degraded" — хотя бы одна недоступна, но Qdrant жив
      - "down" — Qdrant недоступен (поиск невозможен)
    """
    global _cache, _cache_ts

    now = time.time()
    if _cache and (now - _cache_ts) < _CACHE_TTL_SECONDS:
        return _cache

    deps = {
        "llm": _check_llm(),
        "ollama": _check_embeddings(),
        "qdrant": _check_qdrant(),
    }

    qdrant_ok = deps["qdrant"]["status"] == "ok"
    any_down = any(d["status"] != "ok" for d in deps.values())

    if not qdrant_ok:
        overall = "down"
    elif any_down:
        overall = "degraded"
    else:
        overall = "ok"

    result = {"status": overall, "dependencies": deps}
    _cache = result
    _cache_ts = now
    return result
