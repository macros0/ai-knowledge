"""Тесты для LLMClient: семафор параллельности и ретраи при 429/5xx/сетевых сбоях."""
import threading
import time

import httpx
import litellm
import pytest
from litellm.exceptions import RateLimitError

import app.services.llm_client as llm_module
from app.services.llm_client import LLMClient

NORMAL_RESPONSE = litellm.ModelResponse(choices=[litellm.Choices(message=litellm.Message(content="привет"))])


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
    )


class TestRetries:
    def test_success_first_try(self, client, monkeypatch):
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: NORMAL_RESPONSE)
        assert client.chat("s", "u") == "привет"

    def test_retry_after_two_rate_limits_succeeds(self, client, monkeypatch):
        calls = {"n": 0}

        def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RateLimitError(message="limit", model="test", llm_provider="test")
            return NORMAL_RESPONSE

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
            return NORMAL_RESPONSE

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
            with lock:
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
            time.sleep(0.2)
            with lock:
                active["n"] -= 1
            return NORMAL_RESPONSE

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
            return NORMAL_RESPONSE

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
            with lock:
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
            time.sleep(0.2)
            with lock:
                active["n"] -= 1
            return NORMAL_RESPONSE

        monkeypatch.setattr(litellm, "completion", slow)

        interactive_client = LLMClient(interactive=True)
        threads = [threading.Thread(target=lambda: interactive_client.chat("s", "u")) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert active["max"] == 2

    def test_timeout_passed_to_completion(self, client, monkeypatch):
        captured = {}

        def fake(**kwargs):
            captured.update(kwargs)
            return NORMAL_RESPONSE

        monkeypatch.setattr(litellm, "completion", fake)
        client.chat("s", "u")
        assert captured["timeout"] == _settings().llm_timeout_seconds


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
