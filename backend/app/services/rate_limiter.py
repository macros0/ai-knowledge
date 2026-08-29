"""Rate limiting массовой перегенерации OKF (per-user, скользящее окно).

Защищает от случайного и намеренного «залпа» LLM-запросов массовой
перегенерацией: лимит операций/час и лимит документов/час на пользователя,
независимый от общесистемного лимита LLM (llm_max_concurrency в llm_client).

Хранение — in-memory (однопроцессное приложение). Для multi-worker прода
потребуется внешнее хранилище (Redis/БД) — задокументировано в README.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimitExceeded(Exception):
    def __init__(self, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.retry_after = retry_after


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ops: dict[str, deque] = defaultdict(deque)  # user_id -> deque[ts]
        self._docs: dict[str, deque] = defaultdict(deque)  # user_id -> deque[(ts, weight)]

    def check_bulk_regenerate(
        self,
        user_id: str,
        doc_count: int,
        *,
        max_ops_per_hour: int,
        max_docs_per_hour: int,
        window_seconds: float = 3600.0,
    ) -> None:
        """Проверяет и регистрирует массовую перегенерацию. Бросает RateLimitExceeded."""
        now = time.time()
        with self._lock:
            ops = self._ops[user_id]
            while ops and now - ops[0] >= window_seconds:
                ops.popleft()
            docs = self._docs[user_id]
            while docs and now - docs[0][0] >= window_seconds:
                docs.popleft()

            if len(ops) >= max_ops_per_hour:
                retry = max(1.0, window_seconds - (now - ops[0]))
                raise RateLimitExceeded(
                    "Превышен лимит массовых перегенераций в час", retry_after=retry
                )
            total_docs = sum(w for _, w in docs)
            if total_docs + doc_count > max_docs_per_hour:
                retry = max(1.0, window_seconds - (now - docs[0][0])) if docs else window_seconds
                raise RateLimitExceeded(
                    "Превышен лимит документов на перегенерацию в час", retry_after=retry
                )

            ops.append(now)
            docs.append((now, doc_count))

    def reset(self) -> None:
        """Очистка счётчиков (используется в тестах)."""
        with self._lock:
            self._ops.clear()
            self._docs.clear()


_INSTANCE: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = RateLimiter()
    return _INSTANCE
