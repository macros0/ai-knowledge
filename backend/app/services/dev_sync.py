"""Реиндексация dev_tags документов разработки (без пере-эмбеддинга).

При переименовании/изменении module разработки или связывании документа её
денормализованная проекция (number/name/module) обновляется в Qdrant через
set_payload по фильтру doc_id — dense/sparse-векторы не пересчитываются.

Запуск — фоновым потоком из DevelopmentRegistry._schedule_sync (не блокирует
запрос). Ошибки Qdrant не роняют вызов — логируются и не прерывают операцию.

ДОПУЩЕНИЕ (важно): фоновый реиндекс — best-effort, без ретраев.
  - Поток daemon=True: если процесс завершится раньше, чем поток отработает
    (деплой/рестарт), синхронизация обрывается молча, без ошибки и лога.
  - Если Qdrant недоступен в окне выполнения потока, попытка падает и НЕ
    повторяется — документ останется рассинхронизированным (dev_tags в Qdrant
    отстаёт от documents.development_id).
  - Расхождение лечится следующим обычным триггером: regenerate/resume документа
    ведут через pipeline._finalize, который перечитывает development_id → dev_tags
    и переписывает точки. Отдельного персистентного статуса «pending» нет —
    флаг dev_tags_sync_pending одноразовый (в ответе запроса привязки).
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import select

from app.db.models import Document
from app.db.session import session_scope
from app.services.development_registry import get_development_registry
from app.services.registry import get_registry
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)


def document_ids_for_development(dev_id: int) -> list[str]:
    with session_scope() as s:
        return list(
            s.execute(
                select(Document.id).where(Document.development_id == dev_id)
            ).scalars().all()
        )


def reindex_document_dev_tags(doc_id: str, dev_tags: list[str]) -> bool:
    """Синхронный реиндекс dev_tags документа. Возвращает False, если Qdrant недоступен."""
    try:
        VectorStore().reindex_document_dev_tags(doc_id, dev_tags)
        return True
    except Exception:
        logger.warning(
            "Не удалось обновить dev_tags документа %s в Qdrant",
            doc_id,
            exc_info=True,
        )
        return False


def reindex_document_dev_tags_from_db(doc_id: str) -> None:
    """Реиндексирует dev_tags документа по АКТУАЛЬНОМУ development_id из БД.

    Читает состояние в момент выполнения (а не из замыкания на момент постановки),
    чтобы при дублирующих потоках итог всегда соответствовал текущей привязке.
    """
    doc = get_registry().get(doc_id)
    dev_id = doc.get("development_id") if doc else None
    dev_tags = get_development_registry().dev_tags(dev_id) if dev_id else []
    reindex_document_dev_tags(doc_id, dev_tags)


def schedule_document_dev_tags_sync(doc_id: str) -> None:
    """Фоновый (однократный) реиндекс dev_tags одного документа.

    Однократная попытка без ретраев; читает актуальное состояние из БД в момент
    выполнения (см. reindex_document_dev_tags_from_db). При сбое Qdrant в окне
    потока расхождение остаётся до следующего _finalize (regenerate/resume) —
    см. допущение в docstring модуля.
    """
    threading.Thread(
        target=reindex_document_dev_tags_from_db, args=(doc_id,), daemon=True
    ).start()


def reindex_development_documents(dev_id: int) -> None:
    dev_reg = get_development_registry()
    dev_tags = dev_reg.dev_tags(dev_id)
    for doc_id in document_ids_for_development(dev_id):
        reindex_document_dev_tags(doc_id, dev_tags)


def schedule_dev_sync(dev_id: int) -> None:
    """Запускает реиндексацию dev_tags документов разработки в фоновом потоке."""
    thread = threading.Thread(
        target=reindex_development_documents, args=(dev_id,), daemon=True
    )
    thread.start()


def schedule_dev_tags_sync_many(doc_ids: list[str]) -> None:
    """Фоновый реиндекс dev_tags для списка документов (одним потоком).

    Используется при удалении разработки: документы уже отвязаны в БД
    (development_id = NULL), но их dev_tags в Qdrant нужно очистить. Каждый
    документ читает актуальное состояние из БД (см.
    reindex_document_dev_tags_from_db) и пишет пустую проекцию.
    """
    def _run() -> None:
        for doc_id in doc_ids:
            reindex_document_dev_tags_from_db(doc_id)

    threading.Thread(target=_run, daemon=True).start()
