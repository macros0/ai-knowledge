"""Rate limiting массовой перегенерации OKF (per-user, скользящее окно).

Защищает от случайного и намеренного «залпа» LLM-запросов массовой
перегенерацией: лимит операций/час и лимит документов/час на пользователя,
независимый от общесистемного лимита LLM (llm_max_concurrency в llm_client).

Хранение — in-memory (однопроцессное приложение): при двух репликах лимит
множится на их число. Это одно из однопроцессных допущений сервиса, полный
список и последствия — docs/PRODUCTION_DEPLOYMENT.md §3.3 «Одна реплика
бэкенда». Переносить в Redis/БД имеет смысл только вместе с остальными
(очередь задач, purge-циклы, инвалидация кэшей): по отдельности они не
масштабируются всё равно.
"""
from __future__ import annotations

import threading
import time
import json
import math
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
        self._actions: dict[tuple[str, str], deque] = defaultdict(deque)

    @staticmethod
    def _shared():
        from app.config import get_settings
        from app.services.work.admission import durable_documents
        return durable_documents(get_settings())

    @staticmethod
    def _rate_key(domain, user_id, action=None):
        return json.dumps([domain, user_id, action], separators=(",", ":"))

    def check_action(
        self,
        user_id: str,
        action: str,
        *,
        max_requests: int,
        window_seconds: float = 60.0,
    ) -> None:
        """Admission limit for expensive interactive actions (search/chat/export)."""
        if self._shared():
            from app.db.session import session_scope
            from app.services.work.coordination import consume_rate
            window = math.ceil(window_seconds)
            with session_scope() as session:
                if not consume_rate(session, key=self._rate_key("action", user_id, action),
                        limit=max_requests, window_seconds=window):
                    raise RateLimitExceeded(f"Превышен лимит запросов для операции {action}", retry_after=window)
            return
        now = time.time()
        key = (user_id, action)
        with self._lock:
            requests = self._actions[key]
            while requests and now - requests[0] >= window_seconds:
                requests.popleft()
            if len(requests) >= max_requests:
                retry = max(1.0, window_seconds - (now - requests[0]))
                raise RateLimitExceeded(
                    f"Превышен лимит запросов для операции {action}", retry_after=retry
                )
            requests.append(now)

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
        if self._shared():
            from app.db.session import session_scope
            from app.services.work.coordination import consume_rate
            if type(doc_count) is not int or not 1 <= doc_count <= 10000:
                raise ValueError("Invalid bounded bulk rate weight")
            window = math.ceil(window_seconds)
            with session_scope() as session:
                if not consume_rate(session, key=self._rate_key("bulk_ops", user_id),
                        limit=max_ops_per_hour, window_seconds=window):
                    raise RateLimitExceeded("Превышен лимит массовых перегенераций в час", retry_after=window)
                if not consume_rate(session, key=self._rate_key("bulk_docs", user_id),
                        limit=max_docs_per_hour, window_seconds=window, weight=doc_count):
                    raise RateLimitExceeded("Превышен лимит документов на перегенерацию в час", retry_after=window)
            return
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
            self._actions.clear()


_INSTANCE: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = RateLimiter()
    return _INSTANCE
