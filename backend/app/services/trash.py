"""Корзина / soft delete (Этап 4a.2 roadmap): восстановление и автоочистка.

Мягкое удаление реализовано в Pipeline.soft_delete (Qdrant `deleted=true` +
`documents.deleted_at`). Здесь — обратная сторона жизненного цикла:

  - восстановление (снятие обоих флагов, без пере-эмбеддинга);
  - проверка дедупликации при восстановлении против активных документов;
  - фоновая физическая очистка после истечения окна хранения (trash_retention_days):
    Delete Points в Qdrant + DELETE из БД + удаление файлов (Pipeline.remove).

Автоочистка пишется в audit_log как системное действие (DOCUMENT_AUTO_DELETE,
user_id/username = "system"), см. SECURITY.md §5.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.services import audit
from app.services.deduplication import find_active_duplicates_for_document
from app.services.registry import get_registry

logger = logging.getLogger(__name__)

_registry = get_registry()


class RestoreConflictError(Exception):
    """Восстановление конфликтует с активным документом (дедупликация, Этап 4.2).

    `duplicates` — результат find_active_duplicates_for_document (level2/level3).
    """

    def __init__(self, duplicates: dict):
        self.duplicates = duplicates
        super().__init__("В системе уже есть документ, похожий на восстанавливаемый")


def restore_document(doc_id: str, user, ip_address: str | None = None, *, force: bool = False) -> dict:
    """Восстановление документа из корзины.

    Без force — прогоняет проверку дедупликации против АКТИВНЫХ документов и
    бросает RestoreConflictError при совпадении (уровень 2/3). force — пропускает
    проверку (восстановить как отдельный). Возвращает восстановленный документ.
    """
    doc = _registry.get(doc_id)
    if doc is None:
        raise ValueError("Документ не найден")
    if doc.get("deleted_at") is None:
        raise ValueError("Документ не находится в корзине")

    if not force:
        dup = find_active_duplicates_for_document(doc_id)
        if dup["level2"] or dup["level3"]:
            raise RestoreConflictError(dup)

    # Снятие обоих флагов (Qdrant payload + БД). Импорт локальный — Pipeline
    # тянет за собой embedder/LLM, не нужные для восстановления.
    from app.services.pipeline import Pipeline

    Pipeline().restore(doc_id)

    audit.record(
        user,
        audit.DOCUMENT_RESTORE,
        audit.TARGET_DOCUMENT,
        target_id=doc_id,
        old_value={"deleted_at": audit.iso_or_str(doc.get("deleted_at"))},
        new_value={"deleted_at": None},
        ip_address=ip_address,
    )
    return _registry.get(doc_id) or doc


def bulk_restore(doc_ids: list[str], user, ip_address: str | None = None) -> dict:
    """Массовое восстановление из корзины (симметрично массовому удалению).

    Без проверки дедупликации (массовый сценарий). Пропускает отсутствующие и
    не-удалённые документы. Каждый восстановленный — отдельная запись в audit.
    """
    from app.services.pipeline import Pipeline

    pipeline = Pipeline()
    restored: list[str] = []
    skipped: list[str] = []
    for doc_id in doc_ids:
        doc = _registry.get(doc_id)
        if doc is None or doc.get("deleted_at") is None:
            skipped.append(doc_id)
            continue
        pipeline.restore(doc_id)
        audit.record(
            user,
            audit.DOCUMENT_BULK_RESTORE,
            audit.TARGET_DOCUMENT,
            target_id=doc_id,
            ip_address=ip_address,
        )
        restored.append(doc_id)
    return {"restored": restored, "skipped": skipped}


def purge_expired_documents() -> int:
    """Физически удаляет документы с истёкшим окном корзины. Возвращает число удалённых.

    Единственный безвозвратный шаг жизненного цикла (Qdrant Delete Points +
    DELETE из БД + файлы). Пишет DOCUMENT_AUTO_DELETE в audit_log как system.
    """
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.trash_retention_days)
    doc_ids = _registry.purge_expired(cutoff)
    if not doc_ids:
        return 0

    from app.services.pipeline import Pipeline

    pipeline = Pipeline()
    removed = 0
    for doc_id in doc_ids:
        doc = _registry.get(doc_id)
        try:
            # Claim-first (pipeline.remove_if_deleted): строка БД удаляется
            # атомарно с precondition deleted_at IS NOT NULL. Если документ
            # успели восстановить после purge_expired() — он переживает очистку.
            if not pipeline.remove_if_deleted(doc_id):
                logger.info(
                    "Автоочистка корзины: документ %s восстановлен во время очистки — пропущен",
                    doc_id,
                )
                continue
            removed += 1
        except Exception:
            logger.warning("Автоочистка корзины: не удалось удалить %s", doc_id, exc_info=True)
            continue
        audit.record(
            audit.SystemUser(),
            audit.DOCUMENT_AUTO_DELETE,
            audit.TARGET_DOCUMENT,
            target_id=doc_id,
            old_value={
                "filename": (doc or {}).get("filename"),
                "deleted_at": audit.iso_or_str((doc or {}).get("deleted_at")),
            },
        )
    return removed


def start_purge_loop() -> threading.Thread | None:
    """Запускает фоновый демон-поток автоочистки корзины (однократно).

    Возвращает None, если очистка отключена настройкой trash_purge_enabled.
    """
    settings = get_settings()
    if not settings.trash_purge_enabled:
        return None

    def _loop() -> None:
        interval = max(60.0, settings.trash_purge_interval_seconds)
        while True:
            try:
                n = purge_expired_documents()
                if n:
                    logger.info("Автоочистка корзины: удалено %d документ(ов)", n)
            except Exception:
                logger.exception("Автоочистка корзины не удалась")
            time.sleep(interval)

    thread = threading.Thread(target=_loop, name="trash-purge", daemon=True)
    thread.start()
    return thread
