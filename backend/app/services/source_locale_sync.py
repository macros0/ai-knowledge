"""Синхронизация payload Qdrant `source_locale` документа (без пере-эмбеддинга).

При ручной правке языка документа (PATCH /documents/{id}/source-locale) его
денормализованная проекция обновляется в Qdrant через set_payload по фильтру
doc_id (concept + chunk точки) — dense/sparse-векторы не пересчитываются.

Запуск — фоновым потоком (не блокирует запрос). Ошибки Qdrant не роняют вызов.

ДОПУЩЕНИЕ (важно): фоновый синк — best-effort, без ретраев.
  - Поток daemon=True: при рестарте процесса синхронизация обрывается молча.
  - Если Qdrant недоступен в окне выполнения потока, попытка падает и НЕ
    повторяется — payload отстаёт от documents.source_locale.
  - Расхождение лечится следующим обычным триггером: regenerate/resume документа
    ведут через pipeline._finalize, который пишет source_locale в payload при
    upsert. Отдельного персистентного статуса «pending» нет — флаг
    source_locale_sync_pending одноразовый (в ответе PATCH).
"""
from __future__ import annotations

import logging
import threading

from app.services.registry import get_registry
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)


def reindex_document_source_locale(doc_id: str, source_locale: str | None) -> bool:
    """Синхронное обновление payload source_locale. False, если Qdrant недоступен."""
    try:
        VectorStore().set_document_source_locale_payload(doc_id, source_locale)
        return True
    except Exception:
        logger.warning(
            "Не удалось обновить source_locale документа %s в Qdrant",
            doc_id,
            exc_info=True,
        )
        return False


def reindex_document_source_locale_from_db(doc_id: str) -> None:
    """Обновляет payload source_locale по АКТУАЛЬНОМУ значению из БД.

    Читает состояние в момент выполнения (а не из замыкания на момент постановки),
    чтобы при дублирующих потоках итог всегда соответствовал текущей метке.
    """
    doc = get_registry().get(doc_id)
    source_locale = doc.get("source_locale") if doc else None
    reindex_document_source_locale(doc_id, source_locale)


def schedule_source_locale_sync(doc_id: str) -> None:
    """Фоновый (однократный) синк payload source_locale одного документа.

    Однократная попытка без ретраев; читает актуальное состояние из БД в момент
    выполнения. При сбое Qdrant расхождение остаётся до следующего _finalize —
    см. допущение в docstring модуля.
    """
    threading.Thread(
        target=reindex_document_source_locale_from_db, args=(doc_id,), daemon=True
    ).start()
