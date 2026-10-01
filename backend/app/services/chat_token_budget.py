"""Count the rendered local-model chat prompt before generation."""

from __future__ import annotations

import httpx


class ChatBudgetUnavailable(ValueError):
    """The selected generation model has no trusted token budget."""


class ChatTokenBudget:
    def __init__(self, settings, model: str):
        self.settings = settings
        self.model = model
        self._window: int | None = None
        self._cache: dict[tuple[str, str], int] = {}

    def _base(self) -> str:
        return self.settings.llm_base_url.rstrip("/").removesuffix("/v1")

    def _context_window(self) -> int:
        if self._window is None:
            try:
                response = httpx.get(self._base() + "/props", timeout=5)
                response.raise_for_status()
                self._window = int(response.json()["default_generation_settings"]["n_ctx"])
                if self._window <= 0:
                    raise ValueError("Invalid context window")
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                raise ChatBudgetUnavailable("Model context window unavailable") from exc
        return self._window

    def fits(self, system: str, user: str, *, output_tokens: int) -> bool:
        if self.settings.llm_profile != "local_qwen":
            raise ChatBudgetUnavailable("No matching token counter for this provider")
        if output_tokens < 1:
            raise ValueError("output_tokens must be positive")
        key = (system, user)
        if key not in self._cache:
            try:
                rendered = httpx.post(
                    self._base() + "/apply-template",
                    json={
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "add_generation_prompt": True,
                        "chat_template_kwargs": {
                            "enable_thinking": self.settings.llm_local_enable_thinking,
                        },
                    },
                    timeout=5,
                )
                rendered.raise_for_status()
                prompt = rendered.json()["prompt"]
                tokens = httpx.post(
                    self._base() + "/tokenize",
                    json={"content": prompt, "add_special": False},
                    timeout=5,
                )
                tokens.raise_for_status()
                self._cache[key] = len(tokens.json()["tokens"])
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                raise ChatBudgetUnavailable("Model tokenizer unavailable") from exc
        return self._cache[key] + output_tokens + 256 <= self._context_window()
