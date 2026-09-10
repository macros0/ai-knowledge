"""Контракты локали ответа чата."""

from app.models.schemas import ChatRequest
from app.prompts.okf import SYSTEM_CHAT_PROMPT
from app.prompts.store import PromptStore


def test_chat_request_defaults_response_locale_to_russian():
    assert ChatRequest(query="ИТ 0003").locale == "ru"


def test_chat_system_prompt_receives_locale_fallback_instruction():
    store = PromptStore()
    prompt = store.format("chat_system", locale="ru")

    assert "{locale}" in SYSTEM_CHAT_PROMPT
    assert "ИТ 0003" not in prompt
    assert "locale" in prompt
    assert "ru" in prompt
    assert "The provided context does not directly state" not in prompt
