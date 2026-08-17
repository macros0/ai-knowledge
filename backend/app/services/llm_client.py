"""Клиент LLM через LiteLLM — модель настраивается в .env, код не меняется.

Ограничивает параллельные вызовы общим семафором (LLM_MAX_CONCURRENCY)
и повторяет запросы с экспоненциальным backoff при 429 / 5xx / сетевых сбоях.

Ответ читается стримом: таймаут считается по «тишине» между токенами
(LLM_STREAM_IDLE_TIMEOUT_SECONDS), а не по общему времени. Это отличает
медленную, но здоровую генерацию от оборванной сети. Общий жёсткий предел —
LLM_MAX_TOTAL_TIMEOUT_SECONDS. Каждый вызов выполняется в изолированном
daemon-потоке — zombie-потоки не блокируют последующие вызовы.
"""
import json
import logging
import re
import threading
import time
from pathlib import Path

import httpx
import litellm
from json_repair import repair_json

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

_bulk_semaphore: threading.BoundedSemaphore | None = None
_interactive_semaphore: threading.BoundedSemaphore | None = None
_semaphore_lock = threading.Lock()


class LLMTimeoutError(Exception):
    """LLM-вызов превысил LLM_TIMEOUT_SECONDS."""


class LLMTruncationError(Exception):
    """Ответ LLM обрезан по лимиту токенов (finish_reason=length или незакрытая структура).

    Означает потерю данных: `_parse_json` не возвращает спасённый хвост,
    а `chat_json` повторяет запрос с увеличенным max_tokens.
    """


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

    def chat(self, system: str, user: str, max_tokens: int | None = None) -> str:
        semaphore = _get_semaphore(self.interactive)
        if semaphore is None:
            text, _ = self._complete_with_retries(system, user, max_tokens=max_tokens)
            return text
        with semaphore:
            text, _ = self._complete_with_retries(system, user, max_tokens=max_tokens)
            return text

    def _complete_with_retries(self, system: str, user: str, max_tokens: int | None = None) -> tuple[str, str | None]:
        attempts = max(1, self.settings.llm_retry_attempts)
        backoff = max(0.0, self.settings.llm_retry_backoff_seconds)
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._complete_once(system, user, max_tokens=max_tokens)
            except LLMTimeoutError as exc:
                last_exc = exc
                logger.warning(
                    "LLM вызов не удался (попытка %d/%d): %s — повтор через %.1f с",
                    attempt,
                    attempts,
                    exc,
                    backoff * (2 ** (attempt - 1)),
                )
                if attempt < attempts:
                    time.sleep(backoff * (2 ** (attempt - 1)))
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

    def _complete_once(self, system: str, user: str, max_tokens: int | None = None) -> tuple[str, str | None]:
        """Один вызов LLM стримом в изолированном daemon-потоке.

        Таймаут считается по тишине между чанками (idle) — любой пришедший чанк
        сбрасывает таймер, поэтому медленная, но живая генерация не рвётся.
        Семафор удерживается вызывающим потоком (в `chat`), поэтому при
        LLMTimeoutError слот семафора освобождается вместе с исключением.

        Возвращает (текст, finish_reason): finish_reason="length" означает,
        что генерация прервана лимитом max_tokens — ответ неполон.
        """
        container: dict = {
            "parts": [],
            "done": threading.Event(),
            "last_activity": 0.0,
            "finish_reason": None,
        }
        lock = threading.Lock()
        idle = max(0.0, self.settings.llm_stream_idle_timeout_seconds)
        total = max(0.0, self.settings.llm_max_total_timeout_seconds)
        limit = max_tokens or self.settings.llm_max_tokens

        def run() -> None:
            try:
                stream = litellm.completion(
                    model=self.settings.llm_model,
                    api_base=self.settings.llm_base_url or None,
                    api_key=self.settings.llm_api_key or None,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=self.settings.llm_temperature,
                    max_tokens=limit,
                    stream=True,
                )
                for chunk in stream:
                    with lock:
                        container["last_activity"] = time.monotonic()
                    reason = _stream_finish_reason(chunk)
                    if reason:
                        with lock:
                            container["finish_reason"] = reason
                    text = _stream_delta(chunk)
                    if text:
                        with lock:
                            container["parts"].append(text)
            except BaseException as exc:
                container["error"] = exc
            finally:
                container["done"].set()

        thread = threading.Thread(target=run, daemon=True, name="llm-call")
        thread.start()
        start = time.monotonic()
        while not container["done"].is_set():
            with lock:
                last = container["last_activity"] or start
            now = time.monotonic()
            if now - last >= idle:
                raise LLMTimeoutError(f"Нет данных от LLM за {idle:.0f}s")
            if now - start >= total:
                raise LLMTimeoutError(f"LLM вызов превысил {total:.0f}s")
            container["done"].wait(timeout=0.1)
        if "error" in container:
            raise container["error"]
        return "".join(container["parts"]), container["finish_reason"]

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, LLMTimeoutError):
            return True
        if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError, ConnectionError)):
            return True
        status = getattr(exc, "status_code", None)
        return status in _retryable_statuses

    def chat_json(
        self,
        system: str,
        user: str,
        doc_id: str = "unknown",
        chunk_idx: int = 0,
        salvage_truncated: bool = False,
    ) -> list | dict:
        semaphore = _get_semaphore(self.interactive)
        if semaphore is None:
            return self._chat_json_with_truncation_retry(system, user, doc_id, chunk_idx, salvage_truncated)
        with semaphore:
            return self._chat_json_with_truncation_retry(system, user, doc_id, chunk_idx, salvage_truncated)

    def _chat_json_with_truncation_retry(
        self, system: str, user: str, doc_id: str, chunk_idx: int, salvage_truncated: bool
    ) -> list | dict:
        """chat_json + повтор при обрезании JSON (потеря данных).

        При LLMTruncationError повторяем запрос с увеличенным max_tokens
        (формула base * multiplier**n, не выше llm_max_tokens_cap). Если и это
        не помогло:
          - salvage_truncated=True  -> спасаем частичный результат (неполный
            JSON), log WARNING, чтобы конвейер не застревал;
          - salvage_truncated=False -> LLMTruncationError уходит наверх
            (строгий режим / диагностика).
        """
        settings = self.settings
        max_attempts = max(1, settings.llm_truncation_retry_attempts + 1)
        base = max(1, settings.llm_max_tokens)
        multiplier = max(1.0, settings.llm_truncation_max_tokens_multiplier)
        cap = max(base, settings.llm_max_tokens_cap)
        max_tokens = base
        for attempt in range(max_attempts):
            text, finish_reason = self._complete_with_retries(system, user, max_tokens=max_tokens)
            try:
                return _parse_json(
                    text,
                    finish_reason=finish_reason,
                    doc_id=doc_id,
                    chunk_idx=chunk_idx,
                    salvage_truncated=salvage_truncated,
                )
            except LLMTruncationError:
                if attempt == max_attempts - 1:
                    raise
                max_tokens = min(int(base * (multiplier ** (attempt + 1))), cap)
                logger.warning(
                    "[%s] Чанк %s: JSON обрезан по лимиту токенов, повтор с max_tokens=%d",
                    doc_id,
                    chunk_idx,
                    max_tokens,
                )
        raise LLMTruncationError(f"Чанк {chunk_idx}: не удалось получить полный JSON")


def _stream_delta(chunk) -> str:
    """Извлекает текст из стрим-чанка (устойчив к пустым служебным дельтам).

    Некоторые провайдеры шлют reasoning_content или служебные чанки с
    пустым content — для OKF нужен только итоговый content.
    """
    choices = getattr(chunk, "choices", None)
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return ""
    return getattr(delta, "content", None) or ""


def _stream_finish_reason(chunk) -> str | None:
    """Достаёт finish_reason из стрим-чанка (провайдеры кладут его по-разному).

    "length" означает, что генерация прервана max_tokens — ответ неполон.
    """
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    choice = choices[0]
    reason = getattr(choice, "finish_reason", None)
    if reason:
        return str(reason)
    delta = getattr(choice, "delta", None)
    if delta is not None:
        reason = getattr(delta, "finish_reason", None)
        if reason:
            return str(reason)
    return None


def _parse_json(
    text: str,
    finish_reason: str | None = None,
    doc_id: str = "unknown",
    chunk_idx: int = 0,
    salvage_truncated: bool = False,
) -> list | dict:
    """Извлекает JSON из ответа LLM.

    Каскад:
      1. json.loads (быстрый путь)
      2. проверка на обрезание (finish_reason="length" / незакрытая структура)
      3. поиск ближайшего [ / { + восстановление обрезанного хвоста
      4. json_repair — неэкранированные символы, лишние запятые, мусор
      5. дамп ответа в data/debug/ и понятная ошибка

    Обрезанный ответ в строгом режиме (salvage_truncated=False) не
    возвращается «как есть»: он неполон по определению, а json_repair мог бы
    молча закрыть оборванный массив и выкинуть хвост. Тогда _parse_json бросает
    LLMTruncationError, чтобы chat_json переотправил запрос с большим max_tokens.

    При salvage_truncated=True (salvage последней надежды) обрезанный ответ
    всё же пропускается в каскад восстановления: _recover_truncated/json_repair
    спасают последний валидный префикс, а неполнота фиксируется WARNING-логом.
    """
    if not text:
        raise ValueError("LLM вернул пустой ответ")
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    if finish_reason == "length":
        if not salvage_truncated:
            raise LLMTruncationError(
                f"Ответ LLM обрезан по лимиту токенов (чанк {chunk_idx}). "
                "Запрос будет повторён с увеличенным max_tokens."
            )
        logger.warning(
            "[%s] Чанк %s: JSON обрезан по лимиту токенов, спасаю частичный результат (данные неполные)",
            doc_id,
            chunk_idx,
        )

    parsed = _try_load(cleaned)
    if parsed is not None:
        return parsed

    start = min(
        (cleaned.find("[") if "[" in cleaned else len(cleaned)),
        (cleaned.find("{") if "{" in cleaned else len(cleaned)),
    )
    fragment = cleaned[start:] if start < len(cleaned) else cleaned

    if _is_truncated(fragment):
        if not salvage_truncated:
            raise LLMTruncationError(
                f"Ответ LLM обрезан по лимиту токенов (чанк {chunk_idx}): "
                "незакрытая структура JSON. Запрос будет повторён с увеличенным max_tokens."
            )
        logger.warning(
            "[%s] Чанк %s: незакрытая структура JSON, спасаю частичный результат (данные неполные)",
            doc_id,
            chunk_idx,
        )

    recovered = _recover_truncated(fragment)
    if recovered is not None:
        logger.info(
            "[%s] Чанк %s: JSON восстановлен через _recover_truncated (хвост обрезан)",
            doc_id,
            chunk_idx,
        )
        return recovered

    try:
        repaired = repair_json(fragment, return_objects=True)
        if isinstance(repaired, (list, dict)) and repaired:
            logger.info(
                "[%s] Чанк %s: JSON успешно восстановлен через json_repair",
                doc_id,
                chunk_idx,
            )
            return repaired
    except Exception as exc:
        logger.warning("json_repair не удался (чанк %s/%s): %s", doc_id, chunk_idx, exc)

    _dump_debug_response(text, doc_id, chunk_idx)
    raise ValueError(
        f"Не удалось распарсить JSON (чанк {chunk_idx}). "
        f"Дамп сохранён в data/debug/. Начало ответа: {text[:200]!r}"
    )


def _dump_debug_response(text: str, doc_id: str, chunk_idx: int) -> None:
    """Сохраняет сырой ответ LLM в data/debug/ для расследования сбоя парсинга."""
    try:
        debug_dir: Path = get_settings().data_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = debug_dir / f"llm_raw_{doc_id}_{chunk_idx}_{ts}.txt"
        path.write_text(text, encoding="utf-8")
        logger.warning("Сырой ответ LLM сохранён: %s", path)
    except Exception as exc:
        logger.warning("Не удалось сохранить дамп LLM: %s", exc)


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


def _is_truncated(fragment: str) -> bool:
    """True, если JSON-фрагмент оборван: верхний уровень так и не закрылся.

    Считаем глубину вложенности, учитывая строки и экранирование (\" не
    ломает счётчик). Если к концу фрагмента глубина не вернулась к 0 —
    не хватает закрывающей скобки верхнего уровня, т.е. ответ обрезан.

    Примеры:
      [{"id":1}, {"id":2        -> True  (нет внешней ])
      [{"a":1},{"b":2}          -> True  (внешняя ] не дошла)
      [{"id":1}] trailing text  -> False (закрыт; хвост — мусор для json_repair)
    """
    frag = fragment.lstrip()
    if not frag or frag[0] not in "[{":
        return False
    depth = 0
    in_string = False
    escaped = False
    for ch in frag:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return False
    return True


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
