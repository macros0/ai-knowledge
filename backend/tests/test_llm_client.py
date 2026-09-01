"""Тесты для LLMClient: семафор параллельности, ретраи при 429/5xx/сетевых сбоях,
а также стриминг с idle-timeout (медленная, но живая генерация не рвётся)."""
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
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: _hang_stream())
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
    зависший вызов не должен блокировать последующие и не должен исчерпывать
    пул ресурсов, а семафор обязан освобождаться даже при LLMTimeoutError.
    """

    def test_timeout_does_not_block_followup_calls(self, client, monkeypatch):
        monkeypatch.setattr(client.settings, "llm_stream_idle_timeout_seconds", 0.05)
        monkeypatch.setattr(client.settings, "llm_max_total_timeout_seconds", 600)
        call_count = {"n": 0}

        def hanging(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _hang_stream()
            return _stream_response("привет")

        monkeypatch.setattr(litellm, "completion", hanging)
        with pytest.raises(LLMTimeoutError):
            client._complete_once("s", "u")
        # Следующий вызов должен пройти — zombie-поток не блокирует новые.
        result, reason = client._complete_once("s", "u")
        assert result == "привет"
        assert reason is None
        assert call_count["n"] == 2

    def test_semaphore_released_on_timeout(self, client, monkeypatch):
        monkeypatch.setattr(client.settings, "llm_interactive_stream_idle_timeout_seconds", 0.05)
        monkeypatch.setattr(client.settings, "llm_max_total_timeout_seconds", 600)
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: _hang_stream())
        # chat() захватывает семафор в вызывающем потоке — слот обязан
        # освободиться при LLMTimeoutError из-за контекстного менеджера with.
        with pytest.raises(LLMTimeoutError):
            client.chat("s", "u")
        # Следующий вызов проходит немедленно — слот свободен.
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: _stream_response("привет"))
        assert client.chat("s", "u") == "привет"

    def test_semaphore_no_leak_under_concurrent_hangs(self, client, monkeypatch):
        monkeypatch.setattr(client.settings, "llm_interactive_stream_idle_timeout_seconds", 0.05)
        monkeypatch.setattr(client.settings, "llm_max_total_timeout_seconds", 600)
        monkeypatch.setattr(client.settings, "llm_interactive_retry_attempts", 1)
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: _hang_stream())

        sem = llm_module._get_semaphore(interactive=False)
        assert sem is not None, "bulk semaphore must exist for lllm_max_concurrency=1"
        before = sem._value

        for _ in range(3):
            with pytest.raises(LLMTimeoutError):
                client.chat("s", "u")

        after = sem._value
        assert after == before, f"semaphore leaked: {before} -> {after}"


class TestParseJson:
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
        from app.services.llm_client import _parse_json, _dump_debug_response

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
