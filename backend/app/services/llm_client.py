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
from app.services import gen_quality

logger = logging.getLogger(__name__)

_retryable_statuses = {403, 429, 500, 502, 503, 504}

_FATAL_STATUSES = {401, 402, 404}

_OPENROUTER_STANDARD_API_BASE = "https://openrouter.ai/api/v1"


def _completion_api_base(model: str, configured_base: str | None) -> str | None:
    """Возвращает api_base для LiteLLM, сохраняя его provider-specific routing.

    LiteLLM корректно подставляет штатный endpoint и авторизацию OpenRouter,
    когда модель задана как ``openrouter/...`` без явного ``api_base``. В новых
    версиях передача того же стандартного URL меняет маршрут авторизации и
    приводит к 401. Кастомные OpenRouter-compatible gateway остаются явными.
    """
    base = configured_base.strip() if configured_base else ""
    if (
        model.lower().startswith("openrouter/")
        and base.rstrip("/").lower() == _OPENROUTER_STANDARD_API_BASE
    ):
        return None
    return base or None


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


# Сетевой таймаут ставится на эту величину выше прикладного idle: первым должен
# срабатывать прикладной (его сообщение информативнее, и на нём построены retry),
# а сетевой — страховка, гарантирующая завершение брошенного потока.
_HTTP_TIMEOUT_GRACE_SECONDS = 5.0

_inflight = 0
_inflight_lock = threading.Lock()


def _inflight_count() -> int:
    """Сколько потоков LLM-вызовов реально живо прямо сейчас."""
    with _inflight_lock:
        return _inflight


def _inflight_enter(limit: int) -> None:
    """Учитывает начало реального запроса к провайдеру и логирует превышение лимита.

    Слот семафора освобождается вместе с LLMTimeoutError, а поток брошенного
    вызова ещё какое-то время жив, поэтому фактическая параллельность может
    превысить LLM_MAX_CONCURRENCY. Без этого счётчика превышение было бы
    невидимым: в логах — только таймауты, а не занятые соединения и квота.
    """
    global _inflight
    with _inflight_lock:
        _inflight += 1
        current = _inflight
    if limit > 0 and current > limit:
        logger.warning(
            "Живых LLM-запросов: %d при лимите параллельности %d — есть брошенные "
            "по таймауту вызовы, ещё удерживающие соединение и квоту провайдера",
            current,
            limit,
        )


def _inflight_leave() -> None:
    global _inflight
    with _inflight_lock:
        _inflight -= 1


def _acquire_slot(interactive: bool, max_wait: float):
    """Занимает слот параллельности; возвращает идемпотентную функцию освобождения.

    Слот освобождает ПОТОК вызова по своему завершению, а не вызывающий по
    выходу из chat() — иначе брошенный по таймауту вызов продолжал бы занимать
    соединение и квоту провайдера, не занимая слот, и фактическая
    параллельность превышала бы LLM_MAX_CONCURRENCY.

    Ожидание ограничено max_wait (предел времени одного вызова): дольше слот
    держать некому — поток вызова гарантированно завершается за это время.
    Если не дождались, значит слот удерживает поток, который не удалось ни
    остановить, ни дождаться; лучше вернуть retryable-таймаут, чем повторить
    исходный инцидент, когда подвисший вызов блокировал все последующие.
    """
    semaphore = _get_semaphore(interactive)
    if semaphore is None:
        return None
    if not semaphore.acquire(blocking=False):
        waited = time.monotonic()
        got = semaphore.acquire(timeout=max_wait) if max_wait > 0 else semaphore.acquire()
        elapsed = time.monotonic() - waited
        if not got:
            logger.warning(
                "Слот параллельности LLM не освободился за %.0fs — удерживается "
                "подвисшим вызовом (живых запросов: %d)",
                max_wait,
                _inflight_count(),
            )
            raise LLMTimeoutError(
                f"Не дождались слота параллельности LLM за {max_wait:.0f}s"
            )
        if max_wait > 0 and elapsed > max_wait / 2:
            logger.info("Ожидание слота параллельности LLM: %.0fs", elapsed)
    lock = threading.Lock()
    released = False

    def release() -> None:
        nonlocal released
        with lock:
            if released:
                return
            released = True
        semaphore.release()

    return release


def _close_stream(stream) -> None:
    """Best-effort закрытие стрима: освобождает HTTP-соединение провайдера.

    Вызывается и из самого потока (штатное завершение), и снаружи — когда
    вызывающий отказался ждать по таймауту. Закрытие может не поддерживаться
    объектом стрима или упасть (генератор занят в другом потоке) — это не
    ошибка вызова, поэтому глушим до debug.
    """
    close = getattr(stream, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        logger.debug("Не удалось закрыть стрим LLM", exc_info=True)


def _retry_after(exc: Exception) -> float | None:
    value = getattr(exc, "retry_after", None)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


class LLMClient:
    def __init__(self, interactive: bool = False, *, model: str | None = None):
        self.settings = get_settings()
        self.interactive = interactive
        # Интерактивному чату — своя модель (LLM_CHAT_MODEL), если задана;
        # bulk-пайплайн OKF-генерации всегда на llm_model.
        self.model = model or (
            self.settings.llm_chat_model
            if interactive and self.settings.llm_chat_model
            else self.settings.llm_model
        )

    def chat(self, system: str, user: str, max_tokens: int | None = None) -> str:
        attempts = self.settings.llm_interactive_retry_attempts
        idle = self.settings.llm_interactive_stream_idle_timeout_seconds
        # Слот параллельности берётся не здесь, а на каждый фактический запрос
        # (_complete_once) — и освобождается по завершению его потока. Иначе
        # брошенный по таймауту вызов освобождал бы слот, продолжая занимать
        # соединение и квоту провайдера.
        text, _ = self._complete_with_retries(system, user, max_tokens=max_tokens, attempts=attempts, idle_timeout=idle)
        return text

    def _complete_with_retries(self, system: str, user: str, max_tokens: int | None = None, attempts: int | None = None, idle_timeout: float | None = None) -> tuple[str, str | None]:
        attempts = max(1, attempts if attempts is not None else self.settings.llm_retry_attempts)
        backoff = max(0.0, self.settings.llm_retry_backoff_seconds)
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._complete_once(system, user, max_tokens=max_tokens, idle_timeout=idle_timeout)
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

    def _complete_once(self, system: str, user: str, max_tokens: int | None = None, idle_timeout: float | None = None) -> tuple[str, str | None]:
        """Один вызов LLM стримом в изолированном daemon-потоке.

        Таймаут считается по тишине между чанками (idle) — любой пришедший чанк
        сбрасывает таймер, поэтому медленная, но живая генерация не рвётся.

        Слот параллельности берётся здесь, на каждый фактический запрос, и
        освобождается ПОТОКОМ по его завершению — не вызывающим по выходу из
        chat(). Иначе брошенный по таймауту вызов освобождал бы слот, продолжая
        занимать соединение и квоту провайдера, и реальная параллельность
        превышала бы LLM_MAX_CONCURRENCY (особенно на retry-каскаде).

        Это безопасно только потому, что поток гарантированно завершается:
          - СЕТЕВОЙ таймаут (litellm -> httpx) рвёт соединение с молчащим
            провайдером без участия приложения;
          - отказ ждать выставляет флаг отмены и принудительно закрывает стрим;
          - жёсткий предел общего времени проверяется в самом цикле чтения —
            он ловит «капающего» провайдера, которого сетевой таймаут не берёт.
        Без любого из трёх зависший вызов удерживал бы слот бесконечно и
        блокировал бы все последующие (инцидент со zombie-воркерами, см.
        докстринг модуля).

        Число реально живых вызовов считается отдельно (_inflight) и логируется
        при превышении лимита параллельности.

        Возвращает (текст, finish_reason): finish_reason="length" означает,
        что генерация прервана лимитом max_tokens — ответ неполон.
        """
        container: dict = {
            "parts": [],
            "done": threading.Event(),
            "last_activity": 0.0,
            "finish_reason": None,
            "stream": None,
        }
        lock = threading.Lock()
        cancelled = threading.Event()
        idle = max(0.0, idle_timeout if idle_timeout is not None else self.settings.llm_stream_idle_timeout_seconds)
        total = max(0.0, self.settings.llm_max_total_timeout_seconds)
        limit = max_tokens or self.settings.llm_max_tokens
        concurrency = (
            self.settings.llm_interactive_concurrency
            if self.interactive
            else self.settings.llm_max_concurrency
        )
        # httpx трактует скалярный timeout как предел на КАЖДУЮ операцию, в том
        # числе на чтение следующего байта — то есть это тот же idle, но на
        # уровне сокета. Держим его выше прикладного, чтобы порядок срабатывания
        # был предсказуем, и не выше LLM_TIMEOUT_SECONDS — жёсткого потолка на
        # один сетевой запрос (на него же ссылается докстринг LLMTimeoutError).
        hard_cap = max(0.0, self.settings.llm_timeout_seconds)
        http_timeout = min(idle + _HTTP_TIMEOUT_GRACE_SECONDS, total) if idle else total
        if hard_cap:
            http_timeout = min(http_timeout, hard_cap) if http_timeout else hard_cap

        def run() -> None:
            thread_start = time.monotonic()
            stream = None
            try:
                stream = litellm.completion(
                    model=self.model,
                    api_base=_completion_api_base(
                        self.model, self.settings.llm_base_url
                    ),
                    api_key=self.settings.llm_api_key or None,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=self.settings.llm_temperature,
                    max_tokens=limit,
                    stream=True,
                    timeout=http_timeout or None,
                )
                container["stream"] = stream
                for chunk in stream:
                    # Вызывающий уже не ждёт ответ — дочитывать стрим незачем.
                    if cancelled.is_set():
                        break
                    # Жёсткий предел жизни самого потока. Сетевой таймаут спасает
                    # от молчащего провайдера, но не от «капающего»: тот шлёт по
                    # байту и держит соединение сколько угодно. Без этого предела
                    # такой поток удерживал бы слот параллельности бесконечно.
                    if total and time.monotonic() - thread_start >= total:
                        break
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
                _close_stream(stream)
                _inflight_leave()
                # Слот освобождается здесь, а не по выходу из _complete_once:
                # пока поток жив, запрос к провайдеру реально выполняется.
                if release_slot is not None:
                    release_slot()
                container["done"].set()

        release_slot = _acquire_slot(self.interactive, total)
        thread = threading.Thread(target=run, daemon=True, name="llm-call")
        _inflight_enter(concurrency)
        try:
            thread.start()
        except BaseException:
            _inflight_leave()
            if release_slot is not None:
                release_slot()
            raise
        start = time.monotonic()
        try:
            while not container["done"].is_set():
                with lock:
                    last = container["last_activity"] or start
                now = time.monotonic()
                if now - last >= idle:
                    raise LLMTimeoutError(f"Нет данных от LLM за {idle:.0f}s")
                if now - start >= total:
                    raise LLMTimeoutError(f"LLM вызов превысил {total:.0f}s")
                container["done"].wait(timeout=0.1)
        except LLMTimeoutError:
            # Отказываемся ждать — но не бросаем поток «как есть»: помечаем
            # вызов отменённым и закрываем стрим, чтобы соединение и квота
            # провайдера освободились, а не удерживались до конца генерации.
            cancelled.set()
            _close_stream(container.get("stream"))
            raise
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
        single_object: bool = False,
    ) -> list | dict:
        # Слот параллельности — на каждый запрос внутри (_complete_once), см. chat().
        return self._chat_json_with_truncation_retry(
            system, user, doc_id, chunk_idx, salvage_truncated, single_object
        )

    def _chat_json_with_truncation_retry(
        self,
        system: str,
        user: str,
        doc_id: str,
        chunk_idx: int,
        salvage_truncated: bool,
        single_object: bool,
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
                    single_object=single_object,
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
    single_object: bool = False,
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
        gen_quality.record(
            gen_quality.LLM_SALVAGE,
            f"chunk {chunk_idx}: ответ обрезан по лимиту токенов (finish_reason=length)",
        )

    parsed = _try_load(cleaned)
    if parsed is not None:
        return parsed

    start = min(
        (cleaned.find("[") if "[" in cleaned else len(cleaned)),
        (cleaned.find("{") if "{" in cleaned else len(cleaned)),
    )
    fragment = cleaned[start:] if start < len(cleaned) else cleaned

    # Табличный классификатор ожидает ровно один объект. Некоторые модели
    # после полного объекта повторяют JSON-решение (иногда второй объект
    # остаётся незакрытым). Первый объект уже содержит всё решение
    # классификатора, поэтому в явно запрошенном single_object-режиме
    # принимаем его. finish_reason="length" обработан выше и по-прежнему
    # запрещает любое принятие ответа без retry.
    if single_object:
        closed = _top_level_close_pos(fragment)
        if closed != -1:
            prefix = fragment[: closed + 1].strip()
            first = _try_load(prefix)
            if isinstance(first, dict):
                tail = fragment[closed + 1 :].strip()
                if tail:
                    logger.warning(
                        "[%s] Чанк %s: лишний хвост после одиночного JSON-объекта "
                        "классификатора отброшен",
                        doc_id,
                        chunk_idx,
                    )
                return first

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

    # Дыра закрытой структуры (инцидент 03.09.2026): модель выдала ЗАКРЫТЫЙ
    # JSON, а после него — ещё данные. Проверки выше (finish_reason/глубина)
    # такое не ловят: скобки сошлись, хвост молча отбрасывался при salvage.
    # Теперь хвост анализируется: JSON-структура в нём = потеря данных.
    closed = _top_level_close_pos(fragment)
    if closed != -1:
        prefix = fragment[: closed + 1].strip()
        recovered = _try_load(prefix)
        if recovered is not None and recovered:
            tail = fragment[closed + 1 :].strip()
            if _tail_has_json_structure(tail):
                if not salvage_truncated:
                    raise LLMTruncationError(
                        f"Ответ LLM содержит данные после закрытой JSON-структуры (чанк {chunk_idx}). "
                        "Запрос будет повторён с увеличенным max_tokens."
                    )
                logger.warning(
                    "[%s] Чанк %s: после закрытой структуры есть ещё JSON, спасаю первый префикс (данные неполные)",
                    doc_id,
                    chunk_idx,
                )
                gen_quality.record(
                    gen_quality.LLM_SALVAGE,
                    f"chunk {chunk_idx}: данные после закрытой структуры отброшены",
                )
                return recovered
            if tail:
                logger.info(
                    "[%s] Чанк %s: хвост без JSON-структуры отброшен (мусор модели)",
                    doc_id,
                    chunk_idx,
                )
            logger.info(
                "[%s] Чанк %s: JSON восстановлен через _recover_truncated (хвост обрезан)",
                doc_id,
                chunk_idx,
            )
            return recovered

    recovered = _recover_truncated(fragment)
    if recovered is not None:
        if salvage_truncated and _is_truncated(fragment):
            gen_quality.record(
                gen_quality.LLM_SALVAGE,
                f"chunk {chunk_idx}: обрезанный JSON спасён частично",
            )
        logger.info(
            "[%s] Чанк %s: JSON восстановлен через _recover_truncated (хвост обрезан)",
            doc_id,
            chunk_idx,
        )
        return recovered

    try:
        repaired = repair_json(fragment, return_objects=True)
        if isinstance(repaired, (list, dict)) and repaired:
            if salvage_truncated and _is_truncated(fragment):
                gen_quality.record(
                    gen_quality.LLM_SALVAGE,
                    f"chunk {chunk_idx}: обрезанный JSON восстановлен json_repair (возможна потеря хвоста)",
                )
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


def _top_level_close_pos(fragment: str) -> int:
    """Индекс закрывающей скобки первого ПОЛНОГО значения верхнего уровня, или -1.

    Считает глубину как _is_truncated, но возвращает позицию первого возврата
    к 0 — там заканчивается первый законченный JSON (массив/объект). Всё, что
    после — «хвост», который старый _recover_truncated отбрасывал молча.
    """
    frag = fragment.lstrip()
    if not frag or frag[0] not in "[{":
        return -1
    depth = 0
    in_string = False
    escaped = False
    for i, ch in enumerate(frag):
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
                return i
    return -1


def _tail_has_json_structure(tail: str) -> bool:
    """True, если отброшенный хвост содержит JSON-структуру ([ или {).

    Хвост с квадратными скобками после закрытого JSON — вероятная потеря
    данных (модель выдала второй массив/объект). Хвост без скобок — мусор
    модели («Вот концепты:»), его отбрасывание потерь не несёт. Ложное
    срабатывание (текст со скобками) дешевле ложного пропуска (потеря
    концептов): максимум лишний ретрай через каскад.
    """
    return "[" in tail or "{" in tail


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
