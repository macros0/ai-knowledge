"""Проверка здоровья зависимостей: LLM, embeddings, Qdrant, БД, PDF-провайдер.

Два разных вопроса — два разных ответа:

- get_health() (/health) — информационный статус для UI-баннера: включает
  внешние провайдеры (LLM, embeddings), кэшируется на _CACHE_TTL_SECONDS.
  TTL намеренно короче интервала поллинга фронтенда (30с), чтобы задержки не
  складывались.
- get_readiness() (/health/ready) — готовность самого сервиса обслуживать
  запросы: только собственное хранилище (БД, Qdrant) и PDF-провайдер. Его
  использует docker healthcheck: недоступность внешнего LLM-провайдера не
  должна мешать контуру подняться (поиск и список документов работают без
  LLM), а `up -d --wait` и `depends_on: service_healthy` иначе зависели бы от
  чужого SaaS.

Проверки выполняются параллельно с общим дедлайном: время ответа ограничено
_PROBE_DEADLINE_SECONDS, а не суммой таймаутов всех зависимостей (иначе
healthcheck с timeout 8s падал бы на одной медленной зависимости).

Оба эндпоинта открыты без авторизации, поэтому наружу отдаётся public_view():
только статусы. Тексты ошибок (в них внутренние хосты, порты, имена
пользователей БД) и версии библиотек пишутся в лог сервера при смене статуса.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Callable

import httpx

from app.config import get_settings
from app.services.embedder import Embedder
from app.services.vector_store import VectorStore
from docparser import PdfProviderUnavailable, get_pdf_provider_metadata

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 20
# Общий предел времени одного опроса: меньше timeout docker healthcheck (8s).
_PROBE_DEADLINE_SECONDS = 6.0
_cache: dict[str, dict] = {}
_cache_ts: float = 0.0
# Single-flight: /health поллят все открытые вкладки; без блокировки каждый
# промах кэша запускал бы свой опрос и держал поток пула на время таймаутов.
_refresh_lock = threading.Lock()

# Последний залогированный статус каждой зависимости: лог только на переходах,
# а не на каждом опросе (healthcheck раз в 10 с, баннер раз в 20 с).
_last_status: dict[str, str] = {}
_last_status_lock = threading.Lock()

# litellm-префиксы моделей Ollama: у нативного API Ollama нет GET /models.
_OLLAMA_MODEL_PREFIXES = ("ollama/", "ollama_chat/")


def _llm_probe_url(model: str, base_url: str | None) -> str:
    base = (base_url or "https://openrouter.ai/api/v1").rstrip("/")
    if (model or "").startswith(_OLLAMA_MODEL_PREFIXES):
        return f"{base}/api/tags"
    # OpenAI-совместимые API (OpenRouter, OpenAI, vLLM, Ollama /v1).
    return f"{base}/models"


def _check_llm() -> dict:
    """Лёгкий пинг LLM-провайдера (список моделей, без генерации токенов)."""
    settings = get_settings()
    try:
        resp = httpx.get(
            _llm_probe_url(settings.llm_model, settings.llm_base_url),
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


def _check_pdf_provider() -> dict:
    try:
        return {"status": "ok", **get_pdf_provider_metadata()}
    except PdfProviderUnavailable:
        return {"status": "down", "error": "PDF provider unavailable"}


def _run_checks(checks: dict[str, Callable[[], dict]]) -> dict[str, dict]:
    """Параллельный опрос с общим дедлайном; не успевшая проверка — "down"."""
    executor = ThreadPoolExecutor(max_workers=len(checks), thread_name_prefix="health-check")
    try:
        futures = {name: executor.submit(check) for name, check in checks.items()}
        deadline = time.monotonic() + _PROBE_DEADLINE_SECONDS
        results: dict[str, dict] = {}
        for name, future in futures.items():
            try:
                results[name] = future.result(timeout=max(0.0, deadline - time.monotonic()))
            except FutureTimeoutError:
                results[name] = {"status": "down", "error": "timeout"}
            except Exception as exc:
                results[name] = {"status": "down", "error": str(exc)[:120]}
        _log_transitions(results)
        return results
    finally:
        # Зависшая проверка дорабатывает в фоне до своего таймаута и не держит ответ.
        executor.shutdown(wait=False, cancel_futures=True)


def _log_transitions(results: dict[str, dict]) -> None:
    with _last_status_lock:
        changed = {
            name: dep for name, dep in results.items()
            if _last_status.get(name, "ok") != dep["status"]
        }
        _last_status.update({name: dep["status"] for name, dep in results.items()})
    for name, dep in changed.items():
        if dep["status"] == "ok":
            logger.info("Зависимость %s снова доступна", name)
        else:
            logger.warning("Зависимость %s: %s (%s)", name, dep["status"], dep.get("error", ""))


def public_view(result: dict) -> dict:
    """Ответ без деталей: /health и /health/ready доступны без авторизации."""
    return {
        **result,
        "dependencies": {name: {"status": dep["status"]} for name, dep in result["dependencies"].items()},
    }


def get_health() -> dict:
    """Возвращает агрегированный статус здоровья зависимостей.

    Кэширует результат на _CACHE_TTL_SECONDS. Статус:
      - "ok" — все зависимости доступны (включая rate_limited: провайдер жив,
        просто троттлит — это не outage);
      - "degraded" — хотя бы одна зависимость "down", но Qdrant жив;
      - "down" — Qdrant недоступен (поиск невозможен).
    """
    global _cache, _cache_ts

    if _cache and (time.time() - _cache_ts) < _CACHE_TTL_SECONDS:
        return _cache
    if not _refresh_lock.acquire(blocking=False):
        # Опрос уже идёт в другом потоке: прошлый результат лучше, чем ещё
        # один поток, заблокированный на таймаутах тех же зависимостей.
        if _cache:
            return _cache
        _refresh_lock.acquire()
    try:
        if _cache and (time.time() - _cache_ts) < _CACHE_TTL_SECONDS:
            return _cache
        settings = get_settings()
        deps = _run_checks({
            "llm": _check_llm,
            "ollama": _check_embeddings,
            "qdrant": _check_qdrant,
            "database": _check_database,
            "pdf_parser": _check_pdf_provider,
        })

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
        _cache_ts = time.time()
        return result
    finally:
        _refresh_lock.release()


def get_readiness() -> dict:
    """Готовность сервиса: собственное хранилище и PDF-провайдер, без внешних LLM.

    Не кэшируется: вызывается docker healthcheck'ом раз в интервал, а проверки
    дешёвые (SELECT 1, список коллекций Qdrant, метаданные провайдера).
    """
    deps = _run_checks({
        "database": _check_database,
        "qdrant": _check_qdrant,
        "pdf_parser": _check_pdf_provider,
    })
    ready = all(dep["status"] == "ok" for dep in deps.values())
    return {"status": "ready" if ready else "not_ready", "dependencies": deps}
