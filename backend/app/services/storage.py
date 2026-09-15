"""Единая классификация исчерпанного дискового пространства."""
from __future__ import annotations

import errno
import threading

from app import error_codes as codes
from app.services.errors import DomainError


_POSIX_STORAGE_FULL = {errno.ENOSPC, getattr(errno, "EDQUOT", 122)}
_WINDOWS_STORAGE_FULL = {39, 112}  # ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL
_POSTGRES_DISK_FULL = "53100"
_SQLITE_FULL = 13
_STORAGE_FULL_MARKERS = (
    "no space left on device",
    "disk is full",
    "disk full",
    "enospc",
    "edquot",
    "not enough space",
    "недостаточно места",
    "нет места на диске",
)

_TRANSIENT_FAILURES: dict[str, int] = {}
_TRANSIENT_FAILURES_LOCK = threading.RLock()
_TRANSIENT_FAILURE_SEQUENCE = 0


class StorageFullError(DomainError):
    """Место закончилось: после освобождения можно повторить операцию."""

    code = codes.STORAGE_FULL

    def __init__(self) -> None:
        super().__init__("Недостаточно свободного места на диске", code=self.code)


def is_storage_full(exc: BaseException) -> bool:
    """Распознаёт disk-full в Linux, Windows, Docker и драйверах БД."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, StorageFullError):
            return True
        if isinstance(current, OSError):
            if current.errno in _POSIX_STORAGE_FULL:
                return True
            if getattr(current, "winerror", None) in _WINDOWS_STORAGE_FULL:
                return True
        if getattr(current, "sqlstate", None) == _POSTGRES_DISK_FULL:
            return True
        if getattr(current, "pgcode", None) == _POSTGRES_DISK_FULL:
            return True
        if getattr(current, "sqlite_errorcode", None) == _SQLITE_FULL:
            return True
        # Текст не является надёжным признаком на общем пути: например,
        # "Provider quota exceeded" относится к лимиту LLM, а не к диску.
        # Qdrant HTTP/gRPC разбирается в vector_store, где источник известен.
        current = current.__cause__ or current.__context__
    return False


def is_storage_full_text(value: object) -> bool:
    """Распознаёт текст ошибки Qdrant, когда код ОС не доходит до клиента."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return False
    normalized = value.casefold()
    return any(marker in normalized for marker in _STORAGE_FULL_MARKERS)


def storage_failure_lock() -> threading.RLock:
    """Сериализует ручное resume и отложенную фиксацию storage_full."""
    return _TRANSIENT_FAILURES_LOCK


def mark_transient_storage_failure(doc_id: str) -> int:
    """Держит ошибку видимой, когда сама БД пока не принимает UPDATE статуса."""
    global _TRANSIENT_FAILURE_SEQUENCE
    with _TRANSIENT_FAILURES_LOCK:
        active = _TRANSIENT_FAILURES.get(doc_id)
        if active is not None:
            return active
        _TRANSIENT_FAILURE_SEQUENCE += 1
        _TRANSIENT_FAILURES[doc_id] = _TRANSIENT_FAILURE_SEQUENCE
        return _TRANSIENT_FAILURE_SEQUENCE


def clear_transient_storage_failure(doc_id: str) -> None:
    with _TRANSIENT_FAILURES_LOCK:
        _TRANSIENT_FAILURES.pop(doc_id, None)


def transient_storage_failure_is_active(doc_id: str, token: int) -> bool:
    with _TRANSIENT_FAILURES_LOCK:
        return _TRANSIENT_FAILURES.get(doc_id) == token


def transient_storage_failure(doc_id: str) -> dict[str, str] | None:
    """Возвращает временный статус для API до успешной фиксации в БД."""
    with _TRANSIENT_FAILURES_LOCK:
        if doc_id not in _TRANSIENT_FAILURES:
            return None
    return {
        "status": "paused",
        "error": str(StorageFullError()),
        "error_code": codes.STORAGE_FULL,
    }
