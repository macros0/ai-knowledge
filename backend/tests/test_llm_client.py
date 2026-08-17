"""Тесты для LLMClient: семафор параллельности, ретраи при 429/5xx/сетевых сбоях,
а также стриминг с idle-timeout (медленная, но живая генерация не рвётся)."""
import threading
import time

import httpx
import litellm
import pytest
from litellm.exceptions import RateLimitError

import app.services.llm_client as llm_module
from app.services.llm_client import LLMClient, LLMTimeoutError


def _stream_chunk(text: str):
    """Один чанк стрима с заданным текстом."""
    delta = type("Delta", (), {"content": text})()
    choice = type("Choice", (), {"delta": delta})()
    return type("Chunk", (), {"choices": [choice]})()


def _stream_response(*parts):
    """Генератор, эмулирующий стрим litellm.completion(stream=True) — несколько токенов."""
    for p in parts:
        yield _stream_chunk(p)


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


def _settings():
    from app.config import Settings

    return Settings(
        llm_model="openai/test",
        llm_base_url="http://localhost",
        llm_api_key="key",
        llm_max_concurrency=1,
        llm_interactive_concurrency=2,
        llm_retry_attempts=3,
        llm_retry_backoff_seconds=0,
        llm_timeout_seconds=120,
        llm_stream_idle_timeout_seconds=60,
        llm_max_total_timeout_seconds=600,
    )


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
        monkeypatch.setattr(client.settings, "llm_stream_idle_timeout_seconds", 0.05)
        monkeypatch.setattr(client.settings, "llm_max_total_timeout_seconds", 600)
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: _hang_stream())
        with pytest.raises(LLMTimeoutError, match="Нет данных от LLM за"):
            client.chat("s", "u")

    def test_slow_but_healthy_stream_completes(self, client, monkeypatch):
        monkeypatch.setattr(client.settings, "llm_stream_idle_timeout_seconds", 0.2)
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
        result = client._complete_once("s", "u")
        assert result == "привет"
        assert call_count["n"] == 2

    def test_semaphore_released_on_timeout(self, client, monkeypatch):
        monkeypatch.setattr(client.settings, "llm_stream_idle_timeout_seconds", 0.05)
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
        monkeypatch.setattr(client.settings, "llm_stream_idle_timeout_seconds", 0.05)
        monkeypatch.setattr(client.settings, "llm_max_total_timeout_seconds", 600)
        monkeypatch.setattr(client.settings, "llm_retry_attempts", 1)
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
    def test_truncated_array_recovers_last_valid_concept(self):
        from app.services.llm_client import _parse_json

        truncated = (
            '[\n  {"id":"a","title":"Один","type":"concept","tags":["x"],'
            '"content":"текст один","relations":[]},\n'
            '  {"id":"b","title":"Два","type":"concept","tags":["y"],"content":"незавершённ'
        )
        parsed = _parse_json(truncated)
        assert isinstance(parsed, list)
        assert [c["id"] for c in parsed] == ["a"]
        assert parsed[0]["content"] == "текст один"

    def test_valid_json_unaffected(self):
        from app.services.llm_client import _parse_json

        raw = '[{"id":"a","title":"Один","type":"concept","tags":[],"content":"текст","relations":[]}]'
        parsed = _parse_json(raw)
        assert parsed[0]["id"] == "a"

    def test_raw_newlines_inside_string_are_sanitized(self):
        from app.services.llm_client import _parse_json

        raw = '[{"id":"a","title":"Один","type":"concept","tags":[],"content":"строка 1\nстрока 2\n```\nкод","relations":[]}]'
        parsed = _parse_json(raw)
        assert parsed[0]["content"] == "строка 1\nстрока 2\n```\nкод"