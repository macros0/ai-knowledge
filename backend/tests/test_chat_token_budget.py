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
