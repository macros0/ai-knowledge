"""Эмбеддинги: универсальный шлюз через LiteLLM (Ollama, TEI, vLLM, OpenAI, ...) или fake для демо.

Батчинг защищает от жёсткого лимита числа inputs на один запрос у некоторых
провайдеров (Ollama /v1/embeddings отвечает 400 на массивы больше ~330 текстов).
Пустой вход не отправляется в сеть вовсе.

При сбое сети/провайдера бросается EmbedderError с человекочитаемым сообщением,
которое перехватывается centralized handler и доходит до пользователя.
"""
import hashlib
import logging
import math
import re
import time

import litellm

from app.config import get_settings
from app.services.errors import EmbedderError

logger = logging.getLogger(__name__)


class Embedder:
    def __init__(self):
        self.settings = get_settings()

    def embed(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.settings.embedding_provider == "fake":
            return [self._fake(text) for text in texts]

        results: list[list[float]] = []
        batch_size = self.settings.embedding_batch_size
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            vectors = self._embed_batch_with_retry(batch, i)
            results.extend(vectors)
        return results

    def _embed_batch_with_retry(self, batch: list[str], start_idx: int) -> list[list[float]]:
        max_attempts = max(1, self.settings.embedding_retry_attempts)
        backoff = self.settings.embedding_retry_backoff_seconds
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                response = litellm.embedding(
                    model=self.settings.embedding_model,
                    input=batch,
                    api_base=self.settings.embedding_api_base,
                    api_key=self.settings.embedding_api_key,
                    timeout=int(self.settings.embedding_timeout_seconds),
                )
                return [item["embedding"] for item in response.data]
            except Exception as exc:
                last_exc = exc
                if attempt < max_attempts:
                    delay = backoff * attempt
                    logger.warning(
                        "Сбой эмбеддингов батча %d..%d (попытка %d/%d): %s. Повтор через %.0fs",
                        start_idx,
                        start_idx + len(batch),
                        attempt,
                        max_attempts,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "Ошибка генерации эмбеддингов для батча %d..%d (%s): %s",
                        start_idx,
                        start_idx + len(batch),
                        self.settings.embedding_model,
                        exc,
                    )

        model = self.settings.embedding_model or ""
        provider = model.split("/", 1)[0] if "/" in model else (model or "embedding")
        base = self.settings.embedding_api_base or ""
        raise EmbedderError(
            f"Сервис эмбеддингов ({provider}) недоступен."
            + (f" Адрес: {base}." if base else "")
            + " Проверьте, что сервис запущен и модель загружена.",
            cause=last_exc,
        )

    def ping(self) -> bool:
        """Лёгкая проверка доступности embedding-сервиса (для /health)."""
        if self.settings.embedding_provider == "fake":
            return True
        try:
            litellm.embedding(
                model=self.settings.embedding_model,
                input=["ping"],
                api_base=self.settings.embedding_api_base,
                api_key=self.settings.embedding_api_key,
                timeout=5,
            )
            return True
        except Exception:
            return False

    def _fake(self, text: str) -> list[float]:
        """Детерминированный вектор для демонстрации без embedding-сервера."""
        dim = self.settings.embedding_dimensions
        vec = [0.0] * dim
        for token in re.findall(r"\w+", text.lower()):
            h = int(hashlib.md5(token.encode("utf-8"), usedforsecurity=False).hexdigest(), 16)
            idx = h % dim
            vec[idx] += (h >> 16) % 7 - 3
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
