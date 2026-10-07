from types import SimpleNamespace

import pytest

from app.services.chat_token_budget import ChatBudgetUnavailable, ChatTokenBudget


def test_full_prompt_and_output_reserve_are_counted(monkeypatch):
    calls = []

    def fake_post(url, *, json, timeout):
        calls.append((url, json))
        if url.endswith("/apply-template"):
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"prompt": json["messages"][1]["content"]})
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"tokens": list(range(int(json["content"])))})

    def fake_get(url, *, timeout):
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"default_generation_settings": {"n_ctx": 1000}})

    monkeypatch.setattr("app.services.chat_token_budget.httpx.post", fake_post)
    monkeypatch.setattr("app.services.chat_token_budget.httpx.get", fake_get)
    budget = ChatTokenBudget(SimpleNamespace(llm_profile="local_qwen", llm_base_url="http://local/v1", llm_local_enable_thinking=False), "model")
    assert budget.fits("system", "500", output_tokens=244)
    assert not budget.fits("system", "501", output_tokens=244)
    assert any(url.endswith("/apply-template") for url, _ in calls)


def test_unknown_provider_never_guesses_window():
    budget = ChatTokenBudget(SimpleNamespace(llm_profile="standard", llm_base_url="http://local/v1"), "model")
    with pytest.raises(ChatBudgetUnavailable):
        budget.fits("system", "hello", output_tokens=100)


@pytest.mark.parametrize("endpoint", ["/props", "/apply-template", "/tokenize"])
def test_unavailable_model_is_not_reported_as_unknown_context(monkeypatch, endpoint):
    import httpx

    def response(method, url, **kwargs):
        payload = ({"prompt": "test"} if url.endswith("/apply-template") else
                   {"tokens": [1]} if url.endswith("/tokenize") else
                   {"default_generation_settings": {"n_ctx": 32768}})
        status = 503 if url.endswith(endpoint) else 200
        return httpx.Response(status, json=payload, request=httpx.Request(method, url))

    monkeypatch.setattr("app.services.chat_token_budget.httpx.post", lambda url, **kw: response("POST", url, **kw))
    monkeypatch.setattr("app.services.chat_token_budget.httpx.get", lambda url, **kw: response("GET", url, **kw))
    budget = ChatTokenBudget(SimpleNamespace(llm_profile="local_qwen", llm_base_url="http://local/v1", llm_local_enable_thinking=False), "model")
    with pytest.raises(ChatBudgetUnavailable) as caught:
        budget.fits("system", "test", output_tokens=128)
    assert caught.value.code == "dependency_unavailable"


def test_missing_window_keeps_context_error(monkeypatch):
    monkeypatch.setattr("app.services.chat_token_budget.httpx.get", lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {}))
    budget = ChatTokenBudget(SimpleNamespace(llm_base_url="http://local/v1"), "model")
    with pytest.raises(ChatBudgetUnavailable) as caught:
        budget._context_window()
    assert caught.value.code == "chat_budget_unavailable"
