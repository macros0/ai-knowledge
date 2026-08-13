"""Клиент LLM через LiteLLM — модель настраивается в .env, код не меняется."""
import json
import re

import litellm

from app.config import get_settings


class LLMClient:
    def __init__(self):
        self.settings = get_settings()

    def chat(self, system: str, user: str) -> str:
        response = litellm.completion(
            model=self.settings.llm_model,
            api_base=self.settings.llm_base_url or None,
            api_key=self.settings.llm_api_key or None,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.settings.llm_temperature,
            max_tokens=self.settings.llm_max_tokens,
        )
        return response.choices[0].message.content or ""

    def chat_json(self, system: str, user: str) -> list | dict:
        raw = self.chat(system, user)
        return _parse_json(raw)


def _parse_json(text: str) -> list | dict:
    """Извлекает JSON из ответа LLM (устойчив к markdown-обёртке и мусору)."""
    if not text:
        raise ValueError("LLM вернул пустой ответ")
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = min(
        (cleaned.find("[") if "[" in cleaned else len(cleaned)),
        (cleaned.find("{") if "{" in cleaned else len(cleaned)),
    )
    end = max(cleaned.rfind("]"), cleaned.rfind("}"))
    if start < end:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"Не удалось распарсить JSON из ответа LLM: {exc}") from exc
    raise ValueError("LLM не вернул JSON")
