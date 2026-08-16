"""Клиент LLM через LiteLLM — модель настраивается в .env, код не меняется.

Ограничивает параллельные вызовы общим семафором (LLM_MAX_CONCURRENCY)
и повторяет запросы с экспоненциальным backoff при 429 / 5xx / сетевых сбоях.

Гарантирует прерывание зависших вызовов через ThreadPoolExecutor с жёстким
таймаутом (LLM_TIMEOUT_SECONDS).
"""
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _ThreadTimeoutError

import httpx
import litellm

from app.config import get_settings

logger = logging.getLogger(__name__)

_retryable_statuses = {403, 429, 500, 502, 503, 504}

_FATAL_STATUSES = {401, 402, 404}


def is_fatal_error(exc: Exception) -> bool:
    """Фатальные ошибки LLM — бессмысленно ретраить (auth, биллинг, модель)."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in _FATAL_STATUSES
    return False

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm")

_bulk_semaphore: threading.BoundedSemaphore | None = None
_interactive_semaphore: threading.BoundedSemaphore | None = None
_semaphore_lock = threading.Lock()


class LLMTimeoutError(Exception):
    """LLM-вызов превысил LLM_TIMEOUT_SECONDS."""


def _get_semaphore(interactive: bool = False) -> threading.BoundedSemaphore | None:
    """Семафор по типу задачи: bulk (фоновая генерация) или interactive (чат).

    Разделение гарантирует, что интерактивные запросы пользователей не встают
    в очередь позади длительных фоновых пайплайнов (генерация бандлов).
    Возвращает None при concurrency <= 0 (без ограничения семафора).
    """
    global _bulk_semaphore, _interactive_semaphore
    if interactive:
        concurrency = get_settings().llm_interactive_concurrency
        if concurrency <= 0:
            return None
        if _interactive_semaphore is None:
            with _semaphore_lock:
                if _interactive_semaphore is None:
                    _interactive_semaphore = threading.BoundedSemaphore(concurrency)
        return _interactive_semaphore
    concurrency = get_settings().llm_max_concurrency
    if concurrency <= 0:
        return None
    if _bulk_semaphore is None:
        with _semaphore_lock:
            if _bulk_semaphore is None:
                _bulk_semaphore = threading.BoundedSemaphore(concurrency)
    return _bulk_semaphore


def _retry_after(exc: Exception) -> float | None:
    value = getattr(exc, "retry_after", None)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


class LLMClient:
    def __init__(self, interactive: bool = False):
        self.settings = get_settings()
        self.interactive = interactive

    def chat(self, system: str, user: str) -> str:
        semaphore = _get_semaphore(self.interactive)
        if semaphore is None:
            return self._complete_with_retries(system, user)
        with semaphore:
            return self._complete_with_retries(system, user)

    def _complete_with_retries(self, system: str, user: str) -> str:
        attempts = max(1, self.settings.llm_retry_attempts)
        backoff = max(0.0, self.settings.llm_retry_backoff_seconds)
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            future = None
            try:
                future = _executor.submit(
                    litellm.completion,
                    model=self.settings.llm_model,
                    api_base=self.settings.llm_base_url or None,
                    api_key=self.settings.llm_api_key or None,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=self.settings.llm_temperature,
                    max_tokens=self.settings.llm_max_tokens,
                    timeout=self.settings.llm_timeout_seconds,
                )
                response = future.result(timeout=self.settings.llm_timeout_seconds + 5)
                return response.choices[0].message.content or ""
            except _ThreadTimeoutError:
                if future is not None:
                    future.cancel()
                last_exc = LLMTimeoutError(
                    f"LLM вызов превысил {self.settings.llm_timeout_seconds}s "
                    f"(попытка {attempt}/{attempts})"
                )
                logger.warning(str(last_exc))
                delay = backoff * (2 ** (attempt - 1))
                if attempt < attempts:
                    time.sleep(delay)
                continue
            except Exception as exc:
                last_exc = exc
                if not self._is_retryable(exc):
                    raise
                delay = _retry_after(exc) or backoff * (2 ** (attempt - 1))
                logger.warning(
                    "LLM вызов не удался (попытка %d/%d): %s — повтор через %.1f с",
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
                if attempt < attempts:
                    time.sleep(delay)
        raise last_exc  # type: ignore[misc]

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, LLMTimeoutError):
            return True
        if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError, ConnectionError)):
            return True
        status = getattr(exc, "status_code", None)
        return status in _retryable_statuses

    def chat_json(self, system: str, user: str) -> list | dict:
        raw = self.chat(system, user)
        return _parse_json(raw)


def _parse_json(text: str) -> list | dict:
    """Извлекает JSON из ответа LLM (устойчив к markdown-обёртке и мусору)."""
    if not text:
        raise ValueError("LLM вернул пустой ответ")
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    parsed = _try_load(cleaned)
    if parsed is not None:
        return parsed

    start = min(
        (cleaned.find("[") if "[" in cleaned else len(cleaned)),
        (cleaned.find("{") if "{" in cleaned else len(cleaned)),
    )
    if start < len(cleaned):
        fragment = cleaned[start:]
        recovered = _recover_truncated(fragment)
        if recovered is not None:
            return recovered
    raise ValueError("Не удалось распарсить JSON из ответа LLM")


def _try_load(text: str) -> list | dict | None:
    for candidate in (text, _sanitize_control_chars(text)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _sanitize_control_chars(text: str) -> str:
    """Экранирует сырые управляющие символы внутри JSON-строк.

    Модели часто вставляют реальные переносы строк внутри строковых полей
    (например, content с markdown), что делает JSON невалидным.
    """
    escapes = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string and ord(ch) < 0x20:
            out.append(escapes.get(ch, "\\u%04x" % ord(ch)))
            continue
        out.append(ch)
    return "".join(out)


def _recover_truncated(fragment: str) -> list | dict | None:
    """Пытается восстановить JSON, обрезанный на лимите токенов.

    Ищет закрывающую скобку только в последних SEARCH_LIMIT символах, чтобы
    избежать O(n²) при длинных фрагментах (~8k токенов).
    """
    SEARCH_LIMIT = 1000
    fragment = fragment.strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        if not fragment.startswith(opener):
            continue
        start = max(len(opener), len(fragment) - SEARCH_LIMIT)
        for end in range(len(fragment), start - 1, -1):
            candidate = fragment[:end]
            if not candidate.endswith(closer):
                candidate += closer
            parsed = _try_load(candidate)
            if parsed is not None and parsed:
                return parsed
            if candidate.rstrip().endswith(","):
                parsed = _try_load(candidate.rstrip()[:-1] + closer)
                if parsed is not None and parsed:
                    return parsed
    return None
