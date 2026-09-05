"""Правка глобальных тегов документа после загрузки (Этап 4a roadmap).

Canonical-набор — `document_tags` (DocumentRegistry). Правка обновляет проекции,
чтобы они не расходились (MIGRATION_PLAN.md §10, «Дрейк тегов»):
  - `okf_concepts.tags` (БД) — user-часть тегов каждого концепта;
  - `.md` frontmatter бандла (`tags` + `global_tags`) — для reindex/rebuild_tags;
  - Qdrant payload `tags` на всех точках документа (для поиска по тегам).

Тег, равный номеру разработки из справочника, переиспользует механизм Этапа 4:
обновление `development_id` + реиндекс `dev_tags` через services.dev_sync
(reindex_document_dev_tags / schedule_document_dev_tags_sync) — параллельный
механизм не вводится.

Qdrant-синхронизация — best-effort в ФОНОВОМ потоке (как dev_sync): set_payload по
фильтру doc_id на документ с десятками concept-точек синхронно в HTTP-ответе
замораживал бы его (до 05.09.2026 к этому добавлялась ещё и задержка ~2с на КАЖДЫЙ
вызов из-за `localhost`-квирка — см. AGENTS.md; после перевода URL на 127.0.0.1
вызовы ~мс). Недоступность Qdrant не роняет запрос, расхождение лечится следующим
regenerate/resume (pipeline читает doc.tags как user_tags) или следующей правкой.

Каждое изменение тегов фиксируется в audit_log (append-only, services.audit):
отдельная запись на документ; при массовой операции — запись на каждый
затронутый документ с action_type=DOCUMENT_BULK_TAGS_UPDATE.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from app.config import get_settings
from app.db.models import OkfConcept
from app.db.session import session_scope
from app.services import audit
from app.services.dev_sync import (
    reindex_document_dev_tags,
    schedule_document_dev_tags_sync,
)
from app.services.development_registry import get_development_registry
from app.services.registry import get_registry
from app.services.tag_registry import TagRegistry, normalize_tags

logger = logging.getLogger(__name__)

_registry = get_registry()
_tag_registry = TagRegistry()


def _reconcile_development(
    doc: dict, removed: list[str], added: list[str], username: str
) -> dict:
    """Согласует development_id с тегами, равными номерам разработок (Этап 4a).

    Правила (порядок важен):
      - если среди добавленных тегов есть номер разработки — привязать к ней
        (переопределяет текущую привязку, в т.ч. сделанную через DevelopmentPicker);
      - иначе, если удалён тег, равный номеру привязанной разработки — отвязать;
      - иначе — привязку не трогать.
    Возвращает dict полей для DocumentRegistry.update (пустой — без изменений).
    """
    dev_reg = get_development_registry()
    current_dev_id = doc.get("development_id")
    current_dev_number = doc.get("development_number")

    link_candidate = None
    for tag in added:
        dev = dev_reg.find_by_number(tag)
        if dev is not None:
            link_candidate = dev
            break

    new_dev_id = current_dev_id
    if link_candidate is not None:
        new_dev_id = link_candidate["id"]
    elif current_dev_number and current_dev_number in removed:
        new_dev_id = None

    if new_dev_id == current_dev_id:
        return {}

    fields: dict = {"development_id": new_dev_id, "development_suggestion": None}
    if new_dev_id is None:
        fields["development_confidence"] = None
        fields["development_confirmed_by"] = None
    else:
        fields["development_confidence"] = 1.0
        fields["development_confirmed_by"] = username
    return fields


def _merge_concept_tags(current: list[str], removed: set[str], added: list[str]) -> list[str]:
    """Убирает removed и добавляет added, сохраняя порядок и без дублей."""
    result = [t for t in current if t not in removed]
    for t in added:
        if t not in result:
            result.append(t)
    return result


def _update_concept_tags_in_db(doc_id: str, removed: set[str], added: list[str]) -> None:
    """Обновляет user-часть тегов в okf_concepts.tags (canonical per-concept)."""
    with session_scope() as s:
        for concept in s.query(OkfConcept).filter(OkfConcept.doc_id == doc_id).all():
            concept.tags = _merge_concept_tags(list(concept.tags or []), removed, added)


def _rewrite_bundle_frontmatter(
    doc_id: str, new_tags: list[str], removed: set[str], added: list[str]
) -> None:
    """Обновляет `tags`/`global_tags` во frontmatter .md-бандлов (backup/inspect).

    Тело концепта сохраняется байт-в-байт; переписывается только frontmatter.
    Файлы без frontmatter (legacy) пропускаются. Ошибки парсинга не роняют правку.
    """
    import yaml

    bundle_dir = get_settings().okf_dir / doc_id
    if not bundle_dir.is_dir():
        return
    for md in sorted(bundle_dir.glob("*.md")):
        if md.parent.name == "chunks":
            continue
        try:
            text = md.read_text(encoding="utf-8")
            if not text.startswith("---"):
                continue
            end = text.find("\n---", 4)
            if end == -1:
                continue
            meta = yaml.safe_load(text[4:end])
            if not isinstance(meta, dict):
                continue
            current = [str(t) for t in (meta.get("tags") or [])]
            meta["tags"] = _merge_concept_tags(current, removed, added)
            meta["global_tags"] = list(new_tags)
            frontmatter = yaml.safe_dump(
                meta, allow_unicode=True, sort_keys=False, default_flow_style=False
            )
            body = text[end + 4 :]
            tmp = md.with_name(f".{md.name}.tmp")
            tmp.write_text(f"---\n{frontmatter}---{body}", encoding="utf-8")
            os.replace(tmp, md)
        except Exception:
            logger.warning("[%s] Не удалось обновить frontmatter %s", doc_id, md.name, exc_info=True)


def _sync_qdrant_tags(doc_id: str) -> bool:
    """Обновляет payload `tags` в Qdrant из АКТУАЛЬНОГО состояния БД. False при сбое.

    Состояние читается из БД (уже пересчитано правкой): глобальные теги — из
    `document_tags`, per-concept — из `okf_concepts.tags`. point_id концепта
    детерминирован (uuid5 от filepath бандла) — scroll Qdrant не нужен.
    Идемпотентно: приводит Qdrant к состоянию БД, порядок правок не важен.
    """
    import uuid
    from pathlib import Path

    from app.db.models import OkfConcept
    from app.db.session import session_scope
    from app.services.vector_store import VectorStore, concept_point_id

    doc = _registry.get(doc_id)
    if doc is None:
        return False
    global_tags = list(doc.get("tags") or [])
    concept_points: list[tuple[str, list[str]]] = []
    with session_scope() as s:
        rows = (
            s.query(OkfConcept)
            .filter(OkfConcept.doc_id == doc_id)
            .all()
        )
        for c in rows:
            point_id = concept_point_id(doc_id, c.slug)
            concept_points.append((point_id, list(c.tags or [])))
    try:
        VectorStore().set_document_tags_payload(
            doc_id, global_tags, concept_points=concept_points
        )
        return True
    except Exception:
        logger.warning(
            "Не удалось обновить tags документа %s в Qdrant", doc_id, exc_info=True
        )
        return False


# --- Фоновый синк тегов (best-effort, как dev_sync) ---
#
# На этой машине до 05.09.2026 каждый Qdrant-вызов (в т.ч. set_payload) занимал
# ~2с из-за `localhost`-квирка (connect к ::1 висит перед фолбэком на IPv4; URL
# переведён на 127.0.0.1 — вызовы теперь ~мс). Архитектурно синк всё равно
# остаётся в daemon-потоке: десятки concept-точек с разными тегами на документ
# синхронно в HTTP-запросе замораживали бы ответ. Qdrant обновляется фоном:
# ответ возвращается мгновенно, поиск по тегам становится консистентным через
# несколько секунд. Синк идемпотентен (читает актуальное состояние БД) и
# сериализован по документу с dirty-флагом: если за время выполнения пришла
# новая правка — поток выполняет синк ещё раз.

_tags_locks: dict[str, threading.Lock] = {}
_tags_locks_guard = threading.Lock()
_tags_pending: set[str] = set()


def _tags_lock(doc_id: str) -> threading.Lock:
    with _tags_locks_guard:
        return _tags_locks.setdefault(doc_id, threading.Lock())


def _tags_worker(doc_id: str) -> None:
    lock = _tags_lock(doc_id)
    try:
        while True:
            with _tags_locks_guard:
                _tags_pending.discard(doc_id)
            try:
                _sync_qdrant_tags(doc_id)
            except Exception:
                # Воркер не должен умирать молча со стек-трейсом в stderr:
                # логируем, расхождение лечится следующим триггером.
                logger.warning(
                    "Фоновый синк тегов документа %s в Qdrant не удался", doc_id, exc_info=True
                )
            with _tags_locks_guard:
                if doc_id not in _tags_pending:
                    return
    finally:
        lock.release()
        # Гонка «проверка pending → release»: schedule_document_tags_sync успел
        # добавить dirty-флаг после нашей проверки, но его acquire(blocking=False)
        # неудался (лок ещё держали мы) — воркер не запущен. Само-возрождаемся,
        # иначе правка зависла бы в pending до следующего триггера.
        with _tags_locks_guard:
            respawn = doc_id in _tags_pending
        if respawn:
            schedule_document_tags_sync(doc_id)


def schedule_document_tags_sync(doc_id: str) -> None:
    """Фоновый (однократный/повторный) синк payload `tags` документа в Qdrant.

    Ленивый, не падает, если Qdrant недоступен (см. _sync_qdrant_tags). При
    недоступности Qdrant расхождение лечится следующим regenerate/resume
    (pipeline читает doc.tags как user_tags) или следующей правкой тегов.
    """
    lock = _tags_lock(doc_id)
    with _tags_locks_guard:
        _tags_pending.add(doc_id)
    if not lock.acquire(blocking=False):
        # Уже выполняется — dirty-флаг заставит рабочий поток перезапустить синк.
        return
    threading.Thread(target=_tags_worker, args=(doc_id,), daemon=True).start()


def update_document_tags(
    doc_id: str,
    new_tags: list[str],
    user,
    ip_address: str | None = None,
    *,
    bulk: bool = False,
    bulk_context: dict | None = None,
) -> dict:
    """Полная замена набора глобальных тегов документа + синхронизация проекций.

    Возвращает {"doc": <DocumentOut-совместимый dict>, "changed": bool,
    "dev_tags_sync_pending": bool}. При отсутствии документа бросает ValueError
    (API конвертирует в 404). При неизменном наборе — no-op без записи в audit.
    """
    doc = _registry.get(doc_id)
    if doc is None:
        raise ValueError("Документ не найден")

    new_tags = normalize_tags(new_tags)
    old_tags = list(doc.get("tags") or [])
    if set(new_tags) == set(old_tags):
        return {"doc": doc, "changed": False, "dev_tags_sync_pending": False}

    removed = [t for t in old_tags if t not in set(new_tags)]
    added = [t for t in new_tags if t not in set(old_tags)]

    dev_fields = _reconcile_development(doc, removed, added, getattr(user, "username", None) or "anonymous")

    _registry.update(doc_id, tags=new_tags, **dev_fields)
    _tag_registry.add(new_tags)

    if removed or added:
        removed_set = set(removed)
        _update_concept_tags_in_db(doc_id, removed_set, added)
        # Фаза 5: frontmatter .md-бандлов — экспорт, не рабочее состояние. При
        # okf_write_bundles=false файлов нет/они read-only, перезаписывать нечего;
        # бандл для экспорта генерируется из БД (export_okf), не из этих файлов.
        if get_settings().okf_write_bundles:
            _rewrite_bundle_frontmatter(doc_id, new_tags, removed_set, added)

    # Qdrant: tags payload — фоновый best-effort синк; dev_tags — через dev_sync (Этап 4).
    schedule_document_tags_sync(doc_id)

    sync_pending = False
    if dev_fields:
        new_dev_id = dev_fields.get("development_id")
        dev_tags = (
            get_development_registry().dev_tags(new_dev_id) if new_dev_id is not None else []
        )
        if not reindex_document_dev_tags(doc_id, dev_tags):
            schedule_document_dev_tags_sync(doc_id)
            sync_pending = True

    meta = None
    if bulk:
        meta = {"bulk": True, **(bulk_context or {})}
    # Смена development_id через тег (номер разработки) фиксируется в той же
    # записи ИБ — раньше правка, отвязывающая/привязывающая разработку, была
    # невидима в журнале (old/new_value содержали только tags).
    audit_old: dict = {"tags": old_tags}
    audit_new: dict = {"tags": new_tags}
    if "development_id" in dev_fields:
        audit_old["development_id"] = doc.get("development_id")
        audit_new["development_id"] = dev_fields.get("development_id")
    audit.record(
        user,
        audit.DOCUMENT_BULK_TAGS_UPDATE if bulk else audit.DOCUMENT_TAGS_UPDATE,
        audit.TARGET_DOCUMENT,
        target_id=doc_id,
        old_value=audit_old,
        new_value=audit_new,
        ip_address=ip_address,
        meta=meta,
    )

    return {
        "doc": _registry.get(doc_id) or doc,
        "changed": True,
        "dev_tags_sync_pending": sync_pending,
    }


def bulk_update_tags(
    doc_ids: list[str],
    add: list[str],
    remove: list[str],
    user,
    ip_address: str | None = None,
) -> dict:
    """Массовое редактирование тегов (delta add/remove) с записью audit на документ.

    Возвращает {"updated": [doc_id...], "unchanged": [doc_id...]} — только для
    существующих документов (отсутствующие отсеиваются вызывающим _resolve_doc_ids).
    """
    add = normalize_tags(add)
    remove_set = set(normalize_tags(remove))
    context = {"add": add, "remove": sorted(remove_set)}

    updated: list[str] = []
    unchanged: list[str] = []
    for doc_id in doc_ids:
        doc = _registry.get(doc_id)
        if doc is None:
            continue
        current = list(doc.get("tags") or [])
        new_tags = [t for t in current if t not in remove_set]
        for t in add:
            if t not in new_tags:
                new_tags.append(t)
        result = update_document_tags(
            doc_id, new_tags, user, ip_address=ip_address, bulk=True, bulk_context=context
        )
        (updated if result["changed"] else unchanged).append(doc_id)

    return {"updated": updated, "unchanged": unchanged}