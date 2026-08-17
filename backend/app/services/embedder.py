"""Эмбеддинги: универсальный шлюз через LiteLLM (Ollama, TEI, vLLM, OpenAI, ...) или fake для демо.

Батчинг защищает от жёсткого лимита числа inputs на один запрос у некоторых
провайдеров (Ollama /v1/embeddings отвечает 400 на массивы больше ~330 текстов).
Пустой вход не отправляется в сеть вовсе.
"""
import hashlib
import logging
import math
import re

import litellm

from app.config import get_settings

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
            try:
                response = litellm.embedding(
                    model=self.settings.embedding_model,
                    input=batch,
                    api_base=self.settings.embedding_api_base,
                    api_key=self.settings.embedding_api_key,
                    timeout=int(self.settings.llm_timeout_seconds),
                )
            except Exception as exc:
                logger.error(
                    "Ошибка генерации эмбеддингов для батча %d..%d (%s): %s",
                    i,
                    i + len(batch),
                    self.settings.embedding_model,
                    exc,
                )
                raise
            results.extend(item["embedding"] for item in response.data)
        return results

    def _fake(self, text: str) -> list[float]:
        """Детерминированный вектор для демонстрации без embedding-сервера."""
        dim = self.settings.embedding_dimensions
        vec = [0.0] * dim
        for token in re.findall(r"\w+", text.lower()):
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            idx = h % dim
            vec[idx] += (h >> 16) % 7 - 3
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
