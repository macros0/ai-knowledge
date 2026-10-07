"""Count the rendered local-model chat prompt before generation."""

from __future__ import annotations

import httpx
import time
import threading

from app.services.llm_scheduler import LLMCancelled
from app import error_codes


class ChatBudgetUnavailable(ValueError):
    """The selected generation model has no trusted token budget."""

    @property
    def code(self):
        cause = self.__cause__
        if isinstance(cause, httpx.RequestError) or (
            isinstance(cause, httpx.HTTPStatusError)
            and (cause.response.status_code >= 500 or cause.response.status_code == 429)
        ):
            return error_codes.DEPENDENCY_UNAVAILABLE
        return error_codes.CHAT_BUDGET_UNAVAILABLE


class ChatTokenBudget:
    def __init__(self, settings, model: str, *, deadline: float | None = None, cancel=None, max_input_tokens: int | None = None):
        self.settings = settings
        self.model = model
        self.deadline = deadline
        self.cancel = cancel
        self.max_input_tokens = max_input_tokens
        self._window: int | None = None
        self._cache: dict[tuple[str, str], int] = {}

    def _timeout(self):
        if self.cancel is not None and self.cancel.is_set():
            raise LLMCancelled()
        if self.deadline is None:
            return 5
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("Token counting deadline exceeded")
        return min(5, left)

    def _request(self, method, url, **kwargs):
        timeout = self._timeout()
        if self.deadline is None and self.cancel is None:
            return getattr(httpx, method.lower())(url, timeout=timeout, **kwargs)

        # httpx timeouts bound individual I/O operations, not a slow-drip body.
        # Keep the caller bounded by the whole stage deadline and cancellation,
        # then close the dedicated request without blocking that caller.
        done = threading.Event()
        state = {}

        def request():
            client = None
            try:
                self._timeout()
                client = httpx.Client(timeout=self._timeout())
                state["client"] = client
                with client.stream(method, url, timeout=self._timeout(), **kwargs) as response:
                    state["response"] = response
                    self._timeout()
                    response.read()
                    self._timeout()
                    state["result"] = response
            except BaseException as exc:
                state["error"] = exc
            finally:
                try:
                    if client is not None:
                        client.close()
                finally:
                    done.set()

        def close_request():
            for key in ("response", "client"):
                resource = state.get(key)
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass

        threading.Thread(target=request, daemon=True).start()
        try:
            while not done.wait(min(0.02, self._timeout())):
                self._timeout()
            self._timeout()
        except (TimeoutError, LLMCancelled):
            threading.Thread(target=close_request, daemon=True).start()
            raise
        if "error" in state:
            raise state["error"]
        return state["result"]

    def _base(self) -> str:
        return self.settings.llm_base_url.rstrip("/").removesuffix("/v1")

    def _context_window(self) -> int:
        if self._window is None:
            try:
                response = self._request("GET", self._base() + "/props")
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
                rendered = self._request(
                    "POST",
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
                )
                rendered.raise_for_status()
                prompt = rendered.json()["prompt"]
                tokens = self._request(
                    "POST",
                    self._base() + "/tokenize",
                    json={"content": prompt, "add_special": False},
                )
                tokens.raise_for_status()
                self._cache[key] = len(tokens.json()["tokens"])
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                raise ChatBudgetUnavailable("Model tokenizer unavailable") from exc
        fits_input = self.max_input_tokens is None or self._cache[key] <= self.max_input_tokens
        fits_window = self._cache[key] + output_tokens + 256 <= self._context_window()
        self._timeout()
        return fits_input and fits_window
