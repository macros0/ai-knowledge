"""Тесты для LLMClient: семафор параллельности, ретраи при 429/5xx/сетевых сбоях,
а также стриминг с idle-timeout (медленная, но живая генерация не рвётся)."""
import logging
import threading
import time

import httpx
import litellm
import pytest
from litellm.exceptions import RateLimitError

import app.services.llm_client as llm_module
from app.services.llm_client import LLMClient, LLMTimeoutError, LLMTruncationError


def _stream_chunk(text: str):
    """Один чанк стрима с заданным текстом."""
    delta = type("Delta", (), {"content": text})()
    choice = type("Choice", (), {"delta": delta})()
    return type("Chunk", (), {"choices": [choice]})()


def _stream_response(*parts):
    """Генератор, эмулирующий стрим litellm.completion(stream=True) — несколько токенов."""
    for p in parts:
        yield _stream_chunk(p)


def _stream_chunk_finished(text: str, finish_reason: str | None):
    """Чанк с финальным finish_reason (провайдер кладёт его на choices[0])."""
    delta = type("Delta", (), {"content": text, "finish_reason": None})()
    choice = type("Choice", (), {"delta": delta, "finish_reason": finish_reason})()
    return type("Chunk", (), {"choices": [choice]})()


def _stream_response_finished(*parts, finish_reason: str = "stop"):
    """Стрим с finish_reason на последнем чанке — эмуляция конца генерации."""
    for i, p in enumerate(parts):
        yield _stream_chunk_finished(p, None if i < len(parts) - 1 else finish_reason)


def _hang_stream(first_token="первый"):
    """Генератор: выдаёт первый токен, затем зависает навсегда."""
    yield _stream_chunk(first_token)
    time.sleep(999)


@pytest.fixture(autouse=True)
def reset_semaphore():
    llm_module._bulk_semaphore = None
    llm_module._interactive_semaphore = None
    yield
    llm_module._bulk_semaphore = None
    llm_module._interactive_semaphore = None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(llm_module, "get_settings", lambda: _settings())
    return LLMClient()


def _settings(**overrides):
    from app.config import Settings

    kwargs = dict(
        llm_model="openai/test",
        # Явно нейтрализуем LLM_CHAT_MODEL из .env — тесты герметичны к окружению.
        llm_chat_model="",
        llm_base_url="http://localhost",
        llm_api_key="key",
        llm_max_concurrency=1,
        llm_interactive_concurrency=2,
        llm_retry_attempts=3,
        llm_retry_backoff_seconds=0,
        llm_timeout_seconds=120,
        llm_stream_idle_timeout_seconds=60,
        llm_max_total_timeout_seconds=600,
        # Интерактивные настройки (chat() использует именно их):
        # ретраев столько же, сколько в llm_retry_attempts — тесты ожидают 3.
        llm_interactive_retry_attempts=3,
        llm_interactive_stream_idle_timeout_seconds=60,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


class TestRetries:
    def test_openrouter_standard_endpoint_uses_litellm_default_api_base(self, monkeypatch):
        """LiteLLM сам выбирает корректный OpenRouter endpoint для `openrouter/...`.

        Явный https://openrouter.ai/api/v1 меняет маршрут авторизации в
        актуальном LiteLLM и приводил к 401 при живом ключе. Настройка адреса
        остаётся полезной для других провайдеров; это исключение только для
        стандартного OpenRouter URL.
        """
        monkeypatch.setattr(
            llm_module,
            "get_settings",
            lambda: _settings(
                llm_model="openrouter/mistralai/mistral-nemo",
                llm_base_url="https://openrouter.ai/api/v1",
            ),
        )
        client = LLMClient()
        captured = {}

        def fake(**kwargs):
            captured.update(kwargs)
            return _stream_response("ok")

        monkeypatch.setattr(litellm, "completion", fake)
        assert client.chat("s", "u") == "ok"
        assert captured["api_base"] is None

    def test_success_first_try(self, client, monkeypatch):
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: _stream_response("привет"))
        assert client.chat("s", "u") == "привет"

    def test_retry_after_two_rate_limits_succeeds(self, client, monkeypatch):
        calls = {"n": 0}

        def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RateLimitError(message="limit", model="test", llm_provider="test")
            return _stream_response("привет")

        monkeypatch.setattr(litellm, "completion", flaky)
        assert client.chat("s", "u") == "привет"
        assert calls["n"] == 3

    def test_rate_limit_exhausted_raises(self, client, monkeypatch):
        def always_429(**kwargs):
            raise RateLimitError(message="limit", model="test", llm_provider="test")

        monkeypatch.setattr(litellm, "completion", always_429)
        with pytest.raises(RateLimitError):
            client.chat("s", "u")

    def test_connection_error_retried(self, client, monkeypatch):
        calls = {"n": 0}

        def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("down")
            return _stream_response("привет")

        monkeypatch.setattr(litellm, "completion", flaky)
        assert client.chat("s", "u") == "привет"
        assert calls["n"] == 2

    def test_non_retryable_http_error_raises_immediately(self, client, monkeypatch):
        calls = {"n": 0}

        class BadRequest(Exception):
            status_code = 400

        def bad_request(**kwargs):
            calls["n"] += 1
            raise BadRequest("bad request")

        monkeypatch.setattr(litellm, "completion", bad_request)
        with pytest.raises(BadRequest):
            client.chat("s", "u")
        assert calls["n"] == 1


class TestSemaphore:
    def test_semaphore_serializes_calls(self, client, monkeypatch):
        active = {"n": 0, "max": 0}
        lock = threading.Lock()

        def slow(**kwargs):
            def gen():
                with lock:
                    active["n"] += 1
                    active["max"] = max(active["max"], active["n"])
                time.sleep(0.2)
                with lock:
                    active["n"] -= 1
                yield _stream_chunk("привет")
            return gen()

        monkeypatch.setattr(litellm, "completion", slow)

        threads = [threading.Thread(target=lambda: client.chat("s", "u")) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert active["max"] == 1

    def test_concurrency_zero_skips_semaphore(self, monkeypatch):
        settings = _settings()
        settings.llm_max_concurrency = 0
        monkeypatch.setattr(llm_module, "get_settings", lambda: settings)
        calls = {"n": 0}

        def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RateLimitError(message="limit", model="test", llm_provider="test")
            return _stream_response("привет")

        monkeypatch.setattr(litellm, "completion", flaky)

        client = LLMClient()
        assert llm_module._get_semaphore(False) is None
        assert client.chat("s", "u") == "привет"
        assert calls["n"] == 3

    def test_bulk_and_interactive_semaphores_are_separate(self, monkeypatch):
        monkeypatch.setattr(llm_module, "get_settings", lambda: _settings())
        bulk = llm_module._get_semaphore(False)
        interactive = llm_module._get_semaphore(True)
        assert bulk is not None
        assert interactive is not None
        assert bulk is not interactive

    def test_interactive_allows_two_parallel(self, client, monkeypatch):
        active = {"n": 0, "max": 0}
        lock = threading.Lock()

        def slow(**kwargs):
            def gen():
                with lock:
                    active["n"] += 1
                    active["max"] = max(active["max"], active["n"])
                time.sleep(0.2)
                with lock:
                    active["n"] -= 1
                yield _stream_chunk("привет")
            return gen()

        monkeypatch.setattr(litellm, "completion", slow)

        interactive_client = LLMClient(interactive=True)
        threads = [threading.Thread(target=lambda: interactive_client.chat("s", "u")) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert active["max"] == 2

    def test_stream_passed_to_completion(self, client, monkeypatch):
        captured = {}

        def fake(**kwargs):
            captured.update(kwargs)
            return _stream_response("привет")

        monkeypatch.setattr(litellm, "completion", fake)
        client.chat("s", "u")
        assert captured.get("stream") is True


class TestStreamIdleTimeout:
    """Стриминг: таймаут по тишине vs медленная, но живая генерация."""

    def test_idle_timeout_fires_when_stream_goes_silent(self, client, monkeypatch):
        monkeypatch.setattr(client.settings, "llm_interactive_stream_idle_timeout_seconds", 0.05)
        monkeypatch.setattr(client.settings, "llm_max_total_timeout_seconds", 600)
        # Закрываемый стрим — как настоящий httpx-стрим: поток брошенной попытки
        # завершается и возвращает слот, поэтому ретраи не встают в очередь за
        # собственными зомби (см. TestChaosFailureInjection про контракт слота).
        monkeypatch.setattr(
            litellm, "completion", lambda **kwargs: _ControllableStream(["первый"], silence=5)
        )
        with pytest.raises(LLMTimeoutError, match="Нет данных от LLM за"):
            client.chat("s", "u")

    def test_slow_but_healthy_stream_completes(self, client, monkeypatch):
        monkeypatch.setattr(client.settings, "llm_interactive_stream_idle_timeout_seconds", 0.2)
        monkeypatch.setattr(client.settings, "llm_max_total_timeout_seconds", 600)

        def slow_stream(**kwargs):
            def gen():
                for p in ["a", "b", "c", "d", "e"]:
                    time.sleep(0.05)
                    yield _stream_chunk(p)
            return gen()

        monkeypatch.setattr(litellm, "completion", slow_stream)
        result = client.chat("s", "u")
        assert result == "abcde"


class TestChaosFailureInjection:
    """Chaos-тесты: зависание LLM при стабильной сети.

    Воспроизводят реальный инцидент со zombie-воркерами ThreadPoolExecutor:
    зависший вызов не должен блокировать последующие бесконечно и не должен
    исчерпывать пул ресурсов.

    Контракт слота изменён: слот параллельности освобождает поток вызова по
    своему завершению (иначе брошенный вызов занимал бы соединение и квоту
    провайдера, не занимая слот). Поэтому подвисший вызов слот УДЕРЖИВАЕТ — но
    ожидание слота ограничено пределом времени одного вызова
    (llm_max_total_timeout_seconds), после чего следующий вызов получает
    retryable-таймаут, а не виснет навсегда.
    """

    def test_timeout_does_not_block_followup_calls(self, client, monkeypatch):
        """Зависший вызов не блокирует следующий, если его поток завершается."""
        monkeypatch.setattr(client.settings, "llm_stream_idle_timeout_seconds", 0.05)
        monkeypatch.setattr(client.settings, "llm_max_total_timeout_seconds", 600)
        call_count = {"n": 0}

        def hanging(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Стрим, который МОЖНО закрыть — как настоящий httpx-стрим.
                return _ControllableStream(["первый"], silence=0.5)
            return _stream_response("привет")

        monkeypatch.setattr(litellm, "completion", hanging)
        with pytest.raises(LLMTimeoutError):
            client._complete_once("s", "u")
        # Слот освободится, как только брошенный поток среагирует на отмену.
        result, reason = client._complete_once("s", "u")
        assert result == "привет"
        assert reason is None
        assert call_count["n"] == 2

    def test_unkillable_call_does_not_hang_followups_forever(self, client, monkeypatch):
        """Если поток не удаётся остановить — следующий вызов падает, а не виснет."""
        monkeypatch.setattr(client.settings, "llm_stream_idle_timeout_seconds", 0.05)
        # Предел времени вызова = и предел ожидания слота.
        monkeypatch.setattr(client.settings, "llm_max_total_timeout_seconds", 0.5)
        monkeypatch.setattr(client.settings, "llm_retry_attempts", 1)
        # _hang_stream спит в Python и не закрывается — худший случай.
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: _hang_stream())

        with pytest.raises(LLMTimeoutError):
            client._complete_once("s", "u")

        started = time.monotonic()
        with pytest.raises(LLMTimeoutError, match="слота параллельности"):
            client._complete_once("s", "u")
        # Ждём именно ограниченное время, а не бесконечно.
        assert time.monotonic() - started < 5

    def test_slot_held_while_abandoned_call_is_alive(self, client, monkeypatch):
        """Слот занят, пока живёт брошенный поток: реальная параллельность в учёте."""
        monkeypatch.setattr(client.settings, "llm_stream_idle_timeout_seconds", 0.05)
        monkeypatch.setattr(client.settings, "llm_max_total_timeout_seconds", 600)
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: _hang_stream())

        sem = llm_module._get_semaphore(interactive=False)
        assert sem is not None
        before = sem._value

        with pytest.raises(LLMTimeoutError):
            client._complete_once("s", "u")

        assert sem._value == before - 1, (
            "слот должен оставаться занятым, пока брошенный поток жив"
        )

    def test_slot_returns_when_abandoned_thread_finishes(self, client, monkeypatch):
        """Как только поток брошенного вызова завершился — слот возвращается."""
        monkeypatch.setattr(client.settings, "llm_stream_idle_timeout_seconds", 0.05)
        monkeypatch.setattr(client.settings, "llm_max_total_timeout_seconds", 600)
        monkeypatch.setattr(
            litellm, "completion", lambda **kwargs: _ControllableStream(["первый"], silence=0.5)
        )

        sem = llm_module._get_semaphore(interactive=False)
        before = sem._value

        with pytest.raises(LLMTimeoutError):
            client._complete_once("s", "u")

        assert _wait_until(lambda: sem._value == before), f"слот не вернулся: {sem._value}"


class _ControllableStream:
    """Стрим с наблюдаемыми close() и числом отданных чанков.

    Отдаёт chunks, затем «замолкает» на silence секунд (вызывающий успевает
    отвалиться по idle-таймауту) и продолжает отдавать бесконечно — так видно,
    дочитывает ли брошенный поток стрим или прекращает.
    """

    def __init__(self, chunks, silence: float):
        self._head = list(chunks)
        self._silence = silence
        self.consumed = 0
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self._head:
            self.consumed += 1
            return _stream_chunk(self._head.pop(0))
        if self._silence:
            time.sleep(self._silence)
            self._silence = 0.0
        self.consumed += 1
        return _stream_chunk("хвост")

    def close(self):
        self.closed = True


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class TestAbandonedCallCleanup:
    """Брошенный по таймауту вызов не должен жить вечно.

    Семафор освобождается вместе с LLMTimeoutError (это осознанно — зависший
    вызов не блокирует последующие), поэтому реальная параллельность зависит от
    того, как быстро завершится брошенный поток. См. _complete_once.
    """

    def test_network_timeout_passed_to_completion(self, client, monkeypatch):
        """litellm получает сетевой таймаут — молчащий провайдер рвёт соединение сам."""
        captured = {}

        def fake(**kwargs):
            captured.update(kwargs)
            return _stream_response("привет")

        monkeypatch.setattr(client.settings, "llm_interactive_stream_idle_timeout_seconds", 30)
        monkeypatch.setattr(litellm, "completion", fake)
        client.chat("s", "u")

        assert captured.get("timeout") is not None
        # Выше прикладного idle: первым срабатывает прикладной таймаут.
        assert captured["timeout"] > 30

    def test_stream_closed_when_caller_abandons(self, client, monkeypatch):
        """Отказ ждать закрывает стрим — соединение провайдера освобождается."""
        stream = _ControllableStream(["первый"], silence=0.6)
        monkeypatch.setattr(client.settings, "llm_stream_idle_timeout_seconds", 0.05)
        monkeypatch.setattr(client.settings, "llm_max_total_timeout_seconds", 600)
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: stream)

        with pytest.raises(LLMTimeoutError):
            client._complete_once("s", "u")

        _wait_until(lambda: stream.closed, timeout=2.0)
        assert stream.closed is True, "стрим брошенного вызова не закрыт"
        # Бесконечный хвост не вычитывается: после отмены цикл прекращается.
        assert stream.consumed <= 2, f"поток продолжал читать стрим: {stream.consumed} чанков"

    def test_abandoned_thread_finishes(self, client, monkeypatch):
        """Поток брошенного вызова завершается, а не остаётся жить."""
        stream = _ControllableStream(["первый"], silence=0.6)
        monkeypatch.setattr(client.settings, "llm_stream_idle_timeout_seconds", 0.05)
        monkeypatch.setattr(client.settings, "llm_max_total_timeout_seconds", 600)
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: stream)

        before = llm_module._inflight_count()
        with pytest.raises(LLMTimeoutError):
            client._complete_once("s", "u")

        # <=, а не ==: _inflight — глобальный счётчик процесса, и соседние тесты
        # намеренно оставляют висеть потоки на _hang_stream (см. пояснение в
        # test_inflight_returns_to_zero_after_success). Если такой поток
        # завершится во время ожидания, счётчик проскочит `before` вниз, и
        # проверка на равенство упадёт по чужой причине.
        assert _wait_until(lambda: llm_module._inflight_count() <= before), (
            "брошенный поток не завершился — соединение и квота провайдера заняты"
        )

    def test_inflight_over_limit_is_logged(self, caplog):
        """Счётчик живых вызовов — страховка: превышение лимита видно в логах.

        После переноса слота на поток превысить лимит штатным путём нельзя
        (поток стартует только со слотом), поэтому проверяем сам учёт.
        """
        with caplog.at_level(logging.WARNING, logger="app.services.llm_client"):
            llm_module._inflight_enter(1)
            try:
                llm_module._inflight_enter(1)
            finally:
                llm_module._inflight_leave()
                llm_module._inflight_leave()

        assert any("Живых LLM-запросов" in r.getMessage() for r in caplog.records), caplog.text

    def test_inflight_returns_to_zero_after_success(self, client, monkeypatch):
        """Успешный вызов возвращает свой слот учёта.

        «To zero» в имени — про вклад самого вызова, а не про абсолютный ноль:
        _inflight глобален для процесса, а тесты выше намеренно оставляют жить
        потоки на _hang_stream (он спит в Python и не закрывается), поэтому к
        этому моменту счётчик уже ненулевой и таким останется до конца прогона.

        Отсюда `<=`, а не `==`: равенство держалось лишь потому, что чужие
        потоки на локальной машине не успевали завершиться. На более медленном
        раннере CI один из них дренировался во время ожидания, счётчик ушёл
        ниже снимка — и тест падал, хотя проверяемый вызов слот вернул.
        """
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: _stream_response("привет"))
        before = llm_module._inflight_count()
        client.chat("s", "u")
        assert _wait_until(lambda: llm_module._inflight_count() <= before), (
            "успешный вызов не освободил слот учёта"
        )


class TestParseJson:
    @pytest.mark.parametrize("raw", [
        "Only images; no concepts.\n\n```json\n[]\n```",
        "No concepts: []\nNothing to extract.",
    ])
    def test_empty_array_with_explanation_is_valid(self, raw):
        assert llm_module._parse_json(raw) == []

    @pytest.mark.parametrize("raw,reason", [
        ('No concepts: [] [{"id":"extra"}]', None),
        ('No concepts: [] {"id":', None),
        ('No concepts: []', 'length'),
    ])
    def test_empty_array_does_not_hide_truncation(self, raw, reason):
        with pytest.raises(LLMTruncationError):
            llm_module._parse_json(raw, finish_reason=reason)

    def test_truncated_array_raises_truncation_error(self):
        from app.services.llm_client import _parse_json

        truncated = (
            '[\n  {"id":"a","title":"Один","type":"concept","tags":["x"],'
            '"content":"текст один","relations":[]},\n'
            '  {"id":"b","title":"Два","type":"concept","tags":["y"],"content":"незавершённ'
        )
        # Обрезанный ответ неполон — нельзя молча сохранить «спасённый» хвост.
        with pytest.raises(LLMTruncationError):
            _parse_json(truncated)

    def test_truncated_missing_outer_bracket_raises(self):
        from app.services.llm_client import _parse_json

        # Внешняя ] не дошла — верхний уровень не закрыт.
        truncated = '[{"id":"a","title":"Один","type":"concept","tags":[],"content":"x","relations":[]},{"id":"b"'
        with pytest.raises(LLMTruncationError):
            _parse_json(truncated)

    def test_finish_reason_length_raises_even_if_parseable(self):
        from app.services.llm_client import _parse_json

        raw = '[{"id":"a","title":"Один","type":"concept","tags":[],"content":"текст","relations":[]}]'
        # finish_reason="length" = модель прервана лимитом: даже валидный JSON мог
        # быть не дописан (модель собиралась добавить ещё элементы).
        with pytest.raises(LLMTruncationError, match="обрезан по лимиту токенов"):
            _parse_json(raw, finish_reason="length")

    def test_salvage_truncated_recovers_partial(self):
        from app.services.llm_client import _parse_json

        # Salvage последней надежды: обрезанный ответ (не дошла внешняя ])
        # всё же пропускается в каскад восстановления и спасается как неполный.
        partial = (
            '[{"id":"a","title":"Один","type":"concept","tags":[],"content":"текст","relations":[]},\n'
            ' {"id":"b","title":"Два","type":"concept","tags":[],"content":"текст","relations":[]}'
        )
        parsed = _parse_json(partial, finish_reason="length", salvage_truncated=True)
        assert [c["id"] for c in parsed] == ["a", "b"]

    def test_salvage_truncated_garbage_still_raises(self, tmp_path, monkeypatch):
        from app.services.llm_client import _parse_json

        monkeypatch.setattr("app.services.llm_client.get_settings", lambda: type("S", (), {"data_dir": tmp_path})())
        monkeypatch.setattr("app.services.llm_client._dump_debug_response", lambda *a, **k: None)
        # Salvage не помогает от мусора — ошибка сохраняется, чтобы не писать в базу
        # случайное содержимое.
        with pytest.raises(ValueError, match="Не удалось распарсить JSON"):
            _parse_json("совершенный мусор", finish_reason="length", salvage_truncated=True)

    def test_valid_json_unaffected(self):
        from app.services.llm_client import _parse_json

        raw = '[{"id":"a","title":"Один","type":"concept","tags":[],"content":"текст","relations":[]}]'
        parsed = _parse_json(raw)
        assert parsed[0]["id"] == "a"

    def test_closed_array_with_trailing_text_not_truncated(self):
        from app.services.llm_client import _parse_json

        # Структура закрыта, хвост — мусор провайдера: ремонт без потерь.
        garbage = '[{"id":"a","title":"One","type":"concept","tags":[],"content":"test","relations":[]}] blah blah'
        parsed = _parse_json(garbage)
        assert parsed[0]["id"] == "a"

    def test_closed_array_with_json_tail_raises_truncation(self):
        """Дыра закрытой структуры (инцидент 03.09.2026, «ФС 3509»: 4-5
        концептов вместо 11 при зелёном done): закрытый массив + второй
        массив — старый парсер молча брал первый префикс, теряя хвост."""
        from app.services.llm_client import _parse_json
        from app.services import gen_quality

        gen_quality.drain()
        raw = (
            '[{"id":"a","title":"Один","type":"concept","tags":[],"content":"текст","relations":[]}] '
            '[{"id":"b","title":"Два","type":"concept","tags":[],"content":"текст","relations":[]}]'
        )
        with pytest.raises(LLMTruncationError, match="после закрытой JSON-структуры"):
            _parse_json(raw)
        assert gen_quality.drain() == []

    def test_single_object_mode_accepts_first_complete_dict_with_structured_tail(self):
        from app.services.llm_client import _parse_json
        from app.services import gen_quality

        gen_quality.drain()
        raw = (
            '{"concept_per_row":true,"title_col":0,"description_cols":[1],'
            '"concept_type":"reference","extraction_mode":"per_row"}'
            ' {"concept_per_row":true,"title_col":0}'
        )
        parsed = _parse_json(raw, single_object=True)
        assert parsed["title_col"] == 0
        assert gen_quality.drain() == []

    def test_single_object_mode_never_accepts_finish_reason_length(self):
        from app.services.llm_client import _parse_json

        raw = '{"concept_per_row":true,"title_col":0}'
        with pytest.raises(LLMTruncationError, match="обрезан по лимиту токенов"):
            _parse_json(raw, finish_reason="length", single_object=True)

    def test_closed_array_with_open_json_tail_raises_truncation(self):
        """Закрытый массив + ОТКРЫТЫЙ второй (глубина не сходится) — тоже отказ."""
        from app.services.llm_client import _parse_json

        raw = (
            '[{"id":"a","title":"Один","type":"concept","tags":[],"content":"текст","relations":[]}] '
            '[{"id":"b","title":"Два"'
        )
        with pytest.raises(LLMTruncationError):
            _parse_json(raw)

    def test_salvage_closed_array_json_tail_returns_prefix_and_records(self):
        """Salvage-режим: возвращается первый префикс + событие llm_salvage."""
        from app.services.llm_client import _parse_json
        from app.services import gen_quality

        gen_quality.drain()
        raw = (
            '[{"id":"a","title":"Один","type":"concept","tags":[],"content":"текст","relations":[]}] '
            '[{"id":"b","title":"Два","type":"concept","tags":[],"content":"текст","relations":[]}]'
        )
        parsed = _parse_json(raw, salvage_truncated=True)
        assert [c["id"] for c in parsed] == ["a"]
        events = gen_quality.drain()
        assert any(e["event"] == gen_quality.LLM_SALVAGE for e in events), events

    def test_benign_tail_records_nothing(self):
        """Хвост без JSON-структуры (мусор модели) — потерь нет, телеметрия чиста."""
        from app.services.llm_client import _parse_json
        from app.services import gen_quality

        gen_quality.drain()
        garbage = '[{"id":"a","title":"One","type":"concept","tags":[],"content":"test","relations":[]}] Вот концепты:'
        parsed = _parse_json(garbage)
        assert parsed[0]["id"] == "a"
        assert gen_quality.drain() == []


    def test_raw_newlines_inside_string_are_sanitized(self):
        from app.services.llm_client import _parse_json

        raw = '[{"id":"a","title":"Один","type":"concept","tags":[],"content":"строка 1\nстрока 2\n```\nкод","relations":[]}]'
        parsed = _parse_json(raw)
        assert parsed[0]["content"] == "строка 1\nстрока 2\n```\nкод"

    def test_json_repair_recovers_dirty_llm_output(self):
        from app.services.llm_client import _parse_json

        dirty = '[{"id":"a","type":"concept","title":"test","content":"line1\nline2","tags":["x",],}]'
        parsed = _parse_json(dirty)
        assert parsed[0]["id"] == "a"
        assert parsed[0]["tags"] == ["x"]

    def test_json_repair_recovers_garbage_wrapped_json(self, tmp_path, monkeypatch):
        from app.services.llm_client import _parse_json

        garbage = "Вот ответ:\n```\n[\n  {\"id\":\"a\",\"title\":\"One\",\"type\":\"concept\",\"tags\":[],\"content\":\"test\",\"relations\":[]}\n]\n```"
        parsed = _parse_json(garbage)
        assert parsed[0]["id"] == "a"

    def test_parse_json_dumps_debug_on_failure(self, tmp_path, monkeypatch):
        from app.services.llm_client import _parse_json

        monkeypatch.setattr("app.services.llm_client.get_settings", lambda: type("S", (), {"data_dir": tmp_path})())
        monkeypatch.setattr("app.services.llm_client._dump_debug_response", lambda *a, **k: None)

        with pytest.raises(ValueError, match="Не удалось распарсить JSON"):
            _parse_json("совершенный мусор без json", doc_id="test123", chunk_idx=42)

    def test_debug_file_is_created_on_failure(self, tmp_path, monkeypatch):
        from app.services.llm_client import _parse_json

        debug_dir = tmp_path / "debug"
        monkeypatch.setattr("app.services.llm_client.get_settings", lambda: type("S", (), {"data_dir": tmp_path})())

        try:
            _parse_json("совершенный мусор без json", doc_id="dump-test", chunk_idx=7)
        except ValueError:
            pass

        files = list(debug_dir.glob("llm_raw_dump-test_7_*.txt"))
        assert len(files) == 1, f"Expected 1 debug file, got {files}"
        content = files[0].read_text(encoding="utf-8")
        assert "совершенный мусор" in content


class TestChatJsonTruncationRetry:
    """Обрезанный JSON (finish_reason=length / незакрытая структура) -> переотправка
    с увеличенным max_tokens вместо молчаливого сохранения неполного ответа."""

    _VALID = '[{"id":"a","title":"Один","type":"concept","tags":[],"content":"текст","relations":[]}]'

    def test_truncated_retried_with_bigger_max_tokens(self, client, monkeypatch):
        client.settings.llm_max_tokens = 4096
        calls: list[int] = []

        def flaky(**kwargs):
            mt = kwargs.get("max_tokens")
            calls.append(mt if isinstance(mt, int) else 0)
            if len(calls) == 1:
                return _stream_response_finished('[{"id":"a","title":"Один",', finish_reason="length")
            return _stream_response_finished(self._VALID, finish_reason="stop")

        monkeypatch.setattr(litellm, "completion", flaky)
        result = client.chat_json("s", "u", doc_id="d", chunk_idx=1)
        assert result[0]["id"] == "a"
        assert calls == [4096, 6144], f"max_tokens: {calls}"

    def test_max_tokens_capped_at_safety_cap(self, client, monkeypatch):
        client.settings.llm_max_tokens = 4096
        client.settings.llm_max_tokens_cap = 5000
        calls: list[int] = []

        def flaky(**kwargs):
            mt = kwargs.get("max_tokens")
            calls.append(mt if isinstance(mt, int) else 0)
            if len(calls) == 1:
                return _stream_response_finished('[{"id":"a",', finish_reason="length")
            return _stream_response_finished(self._VALID, finish_reason="stop")

        monkeypatch.setattr(litellm, "completion", flaky)
        client.chat_json("s", "u", doc_id="d", chunk_idx=1)
        # 4096*1.5=6144, но cap 5000 -> повтор с 5000
        assert calls == [4096, 5000], f"max_tokens: {calls}"

    def test_truncation_persists_raises_after_all_attempts(self, client, monkeypatch):
        client.settings.llm_truncation_retry_attempts = 2
        calls = {"n": 0}

        def always_truncated(**kwargs):
            calls["n"] += 1
            return _stream_response_finished('[{"id":"a",', finish_reason="length")

        monkeypatch.setattr(litellm, "completion", always_truncated)
        with pytest.raises(LLMTruncationError):
            client.chat_json("s", "u", doc_id="d", chunk_idx=1)
        # 1 начальная попытка + 2 ретрая
        assert calls["n"] == 3

    def test_salvage_truncated_returns_partial_result(self, client, monkeypatch):
        client.settings.llm_truncation_retry_attempts = 2
        calls = {"n": 0}
        partial = (
            '[{"id":"a","title":"Один","type":"concept","tags":[],"content":"текст","relations":[]},\n'
            ' {"id":"b","title":"Два","type":"concept","tags":[],"content":"текст","relations":[]}'
        )

        def always_truncated(**kwargs):
            calls["n"] += 1
            return _stream_response_finished(partial, finish_reason="length")

        monkeypatch.setattr(litellm, "completion", always_truncated)
        # Salvage последней надежды: неполный результат возвращается, исключение не летит.
        result = client.chat_json("s", "u", doc_id="d", chunk_idx=1, salvage_truncated=True)
        assert [c["id"] for c in result] == ["a", "b"]
        assert calls["n"] == 1  # спасаем сразу, бамп max_tokens уже исчерпан

    def test_finish_reason_stop_passes_through_sanitize_path(self, client, monkeypatch):
        monkeypatch.setattr(
            litellm, "completion",
            lambda **kwargs: _stream_response_finished(
                '[{"id":"a","title":"Один","type":"concept","tags":[],"content":"строка 1\nстрока 2","relations":[]}]',
                finish_reason="stop",
            ),
        )
        result = client.chat_json("s", "u", doc_id="d", chunk_idx=1)
        assert result[0]["content"] == "строка 1\nстрока 2"

class TestChatModelResolution:
    """LLM_CHAT_MODEL: интерактивный чат на отдельной модели, bulk — на llm_model."""

    def test_interactive_uses_chat_model(self, monkeypatch):
        monkeypatch.setattr(llm_module, "get_settings", lambda: _settings(llm_chat_model="openai/strong"))
        assert LLMClient(interactive=True).model == "openai/strong"

    def test_interactive_falls_back_to_llm_model_when_empty(self, monkeypatch):
        monkeypatch.setattr(llm_module, "get_settings", lambda: _settings())
        assert LLMClient(interactive=True).model == "openai/test"

    def test_bulk_ignores_chat_model(self, monkeypatch):
        monkeypatch.setattr(llm_module, "get_settings", lambda: _settings(llm_chat_model="openai/strong"))
        assert LLMClient().model == "openai/test"
