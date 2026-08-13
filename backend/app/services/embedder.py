"""Эмбеддинги: OpenAI-совместимый endpoint (Ollama, TEI, vLLM, OpenAI...) или fake для демо."""
import hashlib
import math
import re

import httpx

from app.config import get_settings


class Embedder:
    def __init__(self):
        self.settings = get_settings()
        self._client = httpx.Client(timeout=120)

    def embed(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self.settings.embedding_provider == "fake":
            return [self._fake(text) for text in texts]
        url = f"{self.settings.embedding_base_url.rstrip('/')}/embeddings"
        headers = {"Authorization": f"Bearer {self.settings.embedding_api_key}"}
        payload = {"model": self.settings.embedding_model, "input": texts}
        resp = self._client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in items]

    def _fake(self, text: str) -> list[float]:
        """Детерминированный вектор для демонстрации без embedding-сервера."""
        dim = self.settings.embedding_dim
        vec = [0.0] * dim
        for token in re.findall(r"\w+", text.lower()):
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            idx = h % dim
            vec[idx] += (h >> 16) % 7 - 3
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
