"""Доменные исключения для сбоев внешних зависимостей (LLM, Ollama, Qdrant).

Каждое исключение несёт:
  - service: короткий идентификатор зависимости ("llm" | "ollama" | "qdrant")
  - user_message: человекочитаемое сообщение на русском
  - cause: оригинальное исключение (chained via raise ... from)

Centralized handler в main.py перехватывает DependencyUnavailableError и
возвращает JSON {"detail": ..., "code": "dependency_unavailable", "service": ...}
с HTTP 503, чтобы фронтенд мог показать осмысленное сообщение и кнопки.
"""
from __future__ import annotations

from app import error_codes as codes


class DependencyUnavailableError(Exception):
    """Базовое исключение: внешняя зависимость недоступна."""

    service: str = "unknown"
    default_message: str = "Внешний сервис временно недоступен."

    def __init__(self, message: str | None = None, *, cause: Exception | None = None):
        self.user_message = message or self.default_message
        if cause is not None:
            self.__cause__ = cause
        super().__init__(self.user_message)


class LLMError(DependencyUnavailableError):
    """LLM-провайдер (OpenRouter) недоступен или вернул фатальную ошибку."""

    service = "llm"
    default_message = (
        "Сервис генерации ответа недоступен. "
        "Проверьте подключение к провайдеру LLM (OpenRouter)."
    )


class EmbedderError(DependencyUnavailableError):
    """Embedding-сервис (Ollama или другой провайдер) недоступен."""

    service = "ollama"
    default_message = (
        "Сервис эмбеддингов недоступен. "
        "Проверьте, что embedding-сервис запущен и модель загружена."
    )


class VectorStoreError(DependencyUnavailableError):
    """Векторная БД (Qdrant) недоступна."""

    service = "qdrant"
    default_message = (
        "База знаний недоступна. "
        "Проверьте, что Qdrant запущен."
    )


class DomainError(ValueError):
    """Нарушение бизнес-правила, несущее стабильный код ошибки.

    Наследуется от ValueError намеренно: весь существующий код ловит доменные
    сбои сервисов как `except ValueError`, и менять это разом — лишний риск.
    Роутеры, которым код важен, ловят DomainError отдельно и первым.

    Сообщение остаётся русским: это диагностика для логов. Пользователю текст
    подбирает фронтенд по `code` на языке интерфейса (см. app/error_codes.py).
    """

    code: str = codes.INVALID_REQUEST

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code


class NotFoundError(DomainError):
    """Сущность не найдена.

    HTTP-статус НЕ хранится в исключении: его назначает роутер, как и раньше.
    Иначе у статуса стало бы два источника истины, и перевод сервиса на
    доменные ошибки молча менял бы контракт эндпоинта (см. /trash/restore:
    «документ не в корзине» отдаёт 404, а не 409).
    """

    code = codes.DOCUMENT_NOT_FOUND


class ConflictError(DomainError):
    """Операция противоречит текущему состоянию."""

    code = codes.CONFLICT
