import threading
import time
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services import llm_client as module


def chunk(text='', reason=None, thinking=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=text, reasoning_content=thinking), finish_reason=reason)])


def local_client(monkeypatch, **overrides):
    settings = Settings(_env_file=None, llm_profile='local_qwen', llm_model='openai/local',
                        llm_base_url='http://127.0.0.1:8080/v1', llm_api_key='local',
                        llm_retry_backoff_seconds=0, **overrides)
    monkeypatch.setattr(module, 'get_settings', lambda: settings)
    return module.LLMClient(interactive=True)


def test_reasoning_only_length_is_truncation():
    with pytest.raises(module.LLMTruncationError):
        module._parse_json('', finish_reason='length')


def test_local_profile_disables_thinking_and_limits_chat(monkeypatch):
    client = local_client(monkeypatch)
    requests = []

    def completion(**kwargs):
        requests.append(kwargs)
        return iter([chunk('Ответ [1].', 'stop')])

    monkeypatch.setattr(module.litellm, 'completion', completion)
    assert client.chat('s', 'u') == 'Ответ [1].'
    assert requests[0]['extra_body']['chat_template_kwargs'] == {'enable_thinking': False}
    assert requests[0]['extra_body']['min_p'] == 0
    assert requests[0]['max_tokens'] == 1536
    assert requests[0]['top_p'] == .8


def test_role_chunk_does_not_shorten_prefill_budget(monkeypatch):
    client = local_client(monkeypatch, llm_first_token_timeout_seconds=1,
                          llm_interactive_stream_idle_timeout_seconds=.05)

    def response(**kwargs):
        yield chunk()  # role/header is not the first generated token
        time.sleep(.2)
        yield chunk('Готово', 'stop')

    monkeypatch.setattr(module.litellm, 'completion', response)
    assert client.chat('s', 'u') == 'Готово'


def test_json_tasks_have_distinct_schemas(monkeypatch):
    client = local_client(monkeypatch)
    requests = []

    def completion(**kwargs):
        requests.append(kwargs)
        return iter([chunk('["hello"]', 'stop')])

    monkeypatch.setattr(module.litellm, 'completion', completion)
    assert client.chat_json('s', 'u', task='translation') == ['hello']
    schema = requests[0]['response_format']['json_schema']['schema']
    assert schema['type'] == 'array' and schema['items']['type'] == 'string'
    assert requests[0]['max_tokens'] == 2048
    with pytest.raises(ValueError):
        client.chat_json('s', 'u', task='generation')


def test_truncated_chat_is_not_a_success(monkeypatch):
    client = local_client(monkeypatch)
    monkeypatch.setattr(module.litellm, 'completion', lambda **kwargs: iter([chunk('обрыв', 'length')]))
    with pytest.raises(module.LLMTruncationError):
        client.chat('s', 'u')


def test_standard_provider_does_not_receive_qwen_parameters(monkeypatch):
    settings = Settings(_env_file=None, llm_profile='standard', llm_model='openai/other')
    monkeypatch.setattr(module, 'get_settings', lambda: settings)
    requests = []
    def completion(**kwargs):
        requests.append(kwargs)
        return iter([chunk('ok', 'stop')])
    monkeypatch.setattr(module.litellm, 'completion', completion)
    assert module.LLMClient().chat('s', 'u') == 'ok'
    assert 'extra_body' not in requests[0]


def test_streamed_standard_answer_is_not_replayed_after_partial_failure(monkeypatch):
    from app.services.llm_profiles import request_scope
    settings = Settings(_env_file=None, llm_profile="standard",
                        llm_interactive_retry_attempts=2, llm_retry_backoff_seconds=0)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    calls = []
    def response(**kwargs):
        calls.append(True)
        yield chunk("partial")
        raise module.LLMTimeoutError("connection stalled")
    monkeypatch.setattr(module.litellm, "completion", response)
    shown = []
    with request_scope(on_text=shown.append):
        with pytest.raises(module.LLMTimeoutError):
            module.LLMClient(interactive=True).chat("s", "u")
    assert len(calls) == 1
    assert shown == ["partial"]


def test_scheduler_prioritizes_waiting_chat_and_removes_cancelled_waiter():
    from app.services.llm_scheduler import PriorityScheduler, LLMCancelled
    scheduler = PriorityScheduler()
    held = scheduler.acquire(False, 2)
    order = []
    threads = []
    def worker(interactive, name):
        release = scheduler.acquire(interactive, 2)
        order.append(name)
        release()
    for interactive, name in [(False, 'bulk'), (True, 'chat')]:
        thread = threading.Thread(target=worker, args=(interactive, name))
        thread.start()
        threads.append(thread)
    deadline = time.monotonic() + 1
    while scheduler.waiting < 2 and time.monotonic() < deadline:
        time.sleep(.005)
    assert scheduler.waiting == 2
    held()
    held()  # idempotent release
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    assert order == ['chat', 'bulk']
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(LLMCancelled):
        scheduler.acquire(True, 1, cancelled)
    assert scheduler.waiting == 0


def test_cancelled_stream_never_releases_running_worker(monkeypatch):
    from app.services.llm_profiles import request_scope
    from app.services.llm_scheduler import LLMCancelled, local_scheduler
    client = local_client(monkeypatch)
    cancel = threading.Event()
    unblock = threading.Event()
    def response(**kwargs):
        yield chunk('a')
        cancel.set()
        unblock.wait(2)
        yield chunk('b', 'stop')
    monkeypatch.setattr(module.litellm, 'completion', response)
    try:
        with request_scope(cancel=cancel):
            with pytest.raises(LLMCancelled):
                client.chat('s', 'u')
        with pytest.raises(TimeoutError):
            local_scheduler.acquire(False, .05)
    finally:
        unblock.set()
    release = local_scheduler.acquire(False, 2)
    release()
