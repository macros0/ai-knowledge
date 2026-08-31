# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Роуты загрузки и управления документами."""
import json
import mimetypes
import os
import re
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.auth.models import User
from app.auth.service import require_role, require_user
from app.config import get_settings
from app.models.schemas import (
    BulkOperationRequest,
    BulkPreviewOut,
    BulkTagsRequest,
    Concept,
    ChunkOut,
    DetectDevelopmentOut,
    DocumentDevelopmentSet,
    DocumentListOut,
    DocumentOut,
    DocumentStatsOut,
    DocumentTagsUpdate,
    OkfFileOut,
    TrashItemOut,
    TrashListOut,
    UploaderListOut,
)
from app.services import audit
from app.services.deduplication import file_hash_exists, find_duplicates_for_document, set_file_hash, sha256_file
from app.services.dev_detector import attach_development, detect
from app.services.dev_sync import reindex_document_dev_tags, schedule_document_dev_tags_sync
from app.services.development_registry import get_development_registry
from app.services.document_tag_service import bulk_update_tags, update_document_tags
from app.services.job_queue import BULK_DELETE, BULK_REGENERATE, QueueOverloadedError, get_job_queue
from app.services.okf_generator import _build_markdown
from app.services.pipeline import Pipeline, save_upload_stream
from app.services.rate_limiter import RateLimitExceeded, get_rate_limiter
from app.services.registry import get_registry
from app.services.staging import StagingStore
from app.services.tag_registry import TagRegistry, normalize_tags
from app.services.trash import RestoreConflictError, bulk_restore, restore_document

router = APIRouter(prefix="/documents", tags=["documents"])

_registry = get_registry()
_pipeline = Pipeline()
_tag_registry = TagRegistry()

# doc_id генерируется как uuid.uuid4().hex[:16] (16 hex-символов нижнего
# регистра). Строгий формат не даёт doc_id уйти из okf_bundles через `..`
# или разделители пути — все запросы с несоответствующим id получают 404.
_DOC_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def _valid_doc_id(doc_id: str) -> bool:
    return bool(_DOC_ID_RE.fullmatch(doc_id))


@router.post("", response_model=DocumentOut)
def upload_document(
    file: UploadFile,
    request: Request,
    tags: Annotated[list[str] | None, Form()] = None,
    development_id: Annotated[int | None, Form()] = None,
    user: User = Depends(require_role("editor", "admin")),
):
    """Загрузка документа.

    Обычный def-эндпоинт: FastAPI уводит его в threadpool, поэтому синхронная
    потоковая запись файла не блокирует event loop. Размер ограничен настройкой
    max_upload_mb (проверка по факту дочитывания + по объявленному content-length).
    """
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    declared = file.size or 0
    if declared > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Файл превышает максимальный размер {settings.max_upload_mb} МБ",
        )
    try:
        doc_id, _dest, size = save_upload_stream(
            file.file, file.filename or "unknown", max_bytes=max_bytes
        )
    except ValueError as exc:
        if "максимальный размер" in str(exc):
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if size == 0:
        raise HTTPException(status_code=400, detail="Файл пустой")

    # Дедупликация, уровень 1: точное совпадение байтов (SHA-256 файла).
    settings = get_settings()
    file_hash = None
    if settings.dedup_enabled:
        file_hash = sha256_file(_dest)
        existing = file_hash_exists(file_hash)
        if existing is not None:
            _dest.unlink(missing_ok=True)
            return JSONResponse(
                status_code=409,
                content={
                    "detail": f"Файл уже загружен как «{existing['filename']}»",
                    "code": "duplicate",
                    "duplicate": existing,
                },
            )

    user_tags = normalize_tags(tags)
    _tag_registry.add(user_tags)
    doc = _registry.create(
        doc_id,
        file.filename or "unknown",
        file.content_type or "",
        size,
        tags=user_tags,
        uploaded_by=user.username,
    )
    if file_hash:
        set_file_hash(doc_id, file_hash)
    # Явная привязка разработки из контекста загрузки (Этап 4a.1, «поиск →
    # загрузка»): важнее автоопределения — при заданном development_id regex/
    # LLM-детект не запускается, dev_tags проставит пайплайн в _finalize.
    bound_development_id: int | None = None
    if development_id is not None:
        if get_development_registry().get(development_id) is None:
            raise HTTPException(status_code=422, detail="Разработка не найдена")
        _registry.update(
            doc_id,
            development_id=development_id,
            development_confidence=1.0,
            development_confirmed_by=user.username,
            development_suggestion=None,
        )
        doc = _registry.get(doc_id) or doc
        bound_development_id = development_id
    # Автоопределение номера разработки по имени файла (regex, без LLM) до
    # запуска пайплайна. LLM-детекция с титульного листа — позже, в pipeline.
    if bound_development_id is None:
        detection = detect("", file.filename or "unknown", doc_id)
        if detection.confidence is not None:
            attach_development(doc_id, detection)
            doc = _registry.get(doc_id) or doc
    _pipeline.ingest(doc_id, get_settings().uploads_dir / f"{doc_id}{Path(file.filename or '').suffix.lower()}", doc["filename"], user_tags=user_tags)
    audit_value = {"filename": doc.get("filename"), "size": size}
    if bound_development_id is not None:
        audit_value["development_id"] = bound_development_id
    audit.record(
        user,
        audit.DOCUMENT_UPLOAD,
        audit.TARGET_DOCUMENT,
        target_id=doc_id,
        new_value=audit_value,
        ip_address=_client_ip(request),
    )
    return doc


@router.get("", response_model=DocumentListOut)
def list_documents(
    uploader: str | None = None,
    status: str | None = None,
    has_duplicates: bool | None = None,
    development_id: int | None = None,
    development_number: str | None = None,
    module: str | None = None,
    problem: bool | None = None,
    search: str | None = None,
    tag: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str = "date_desc",
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    user: User = Depends(require_user),
):
    """Список документов.

    uploader=None/отсутствует — полный список; uploader=<username> — только
    документы этого пользователя (uploaded_by == username). Значения «mine»/«all»
    бэкенду неизвестны — выбор «мои документы» фронтенд резолвит в конкретный
    username текущего пользователя.

    Остальные фильтры — дешёвые WHERE-pushdown по хранимым полям (см.
    DocumentRegistry.list_page): status — одно значение или через запятую
    (paused,failed,error); has_duplicates — булев флаг; development_id /
    development_number — по разработке; module — по модулю разработки;
    problem=true — объединённое «Проблемные» (остановившиеся + дубликаты);
    date_from/date_to — диапазон дат загрузки (ISO YYYY-MM-DD, включительно,
    date_to трактуется как конец дня в UTC).

    search — текстовый поиск по filename / uploaded_by / тегам / разработке
    (подстрока; * и ? — glob только для filename); sort — ключ сортировки
    (date_desc/date_asc/name_asc/name_desc/uploader_asc/uploader_desc);
    limit/offset — серверная пагинация, limit=None — вернуть всё.
    """
    statuses = None
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
    docs, total = _registry.list_page(
        uploaded_by=uploader,
        statuses=statuses,
        has_duplicates=has_duplicates,
        development_id=development_id,
        development_number=development_number,
        module=module,
        problem=problem,
        search=search,
        tag=tag,
        date_from=_parse_date_boundary(date_from, end_of_day=False),
        date_to=_parse_date_boundary(date_to, end_of_day=True),
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return DocumentListOut(documents=docs, total=total, limit=limit, offset=offset)


@router.get("/stats", response_model=DocumentStatsOut)
def document_stats(user: User = Depends(require_user)):
    """Прогресс разметки по активной базе (Этап 4.1/5): total и с development_id."""
    return _registry.markup_stats()


@router.get("/uploaders", response_model=UploaderListOut)
def list_uploaders(user: User = Depends(require_user)):
    """Отдельные username загрузчиков (для дропдауна фильтра на фронтенде)."""
    return UploaderListOut(uploaders=_registry.distinct_uploaders())


@router.get("/trash", response_model=TrashListOut)
def list_trash(
    uploader: str | None = None,
    search: str | None = None,
    sort: str = "date_desc",
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    user: User = Depends(require_user),
):
    """Список корзины: удалённые документы с индикацией срока до автоочистки.

    Объявлен ДО `/{doc_id}` (иначе «trash» попал бы в параметр doc_id). Видимость
    «своя»/«все» решается uploader-фильтром тем же способом, что и в списке
    документов (frontend резолвит «mine» в username).
    """
    settings = get_settings()
    docs, total = _registry.list_trash(
        uploaded_by=uploader,
        search=search,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    items = [_to_trash_item(d, settings.trash_retention_days) for d in docs]
    return TrashListOut(
        documents=items,
        total=total,
        limit=limit,
        offset=offset,
        retention_days=settings.trash_retention_days,
    )


@router.get("/{doc_id}", response_model=DocumentOut)
def get_document(doc_id: str):
    doc = _registry.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return doc


@router.post("/{doc_id}/development", response_model=DocumentOut)
def set_document_development(
    doc_id: str,
    body: DocumentDevelopmentSet,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Ручная привязка/отвязка разработки и подтверждение автоопределения.

    Связывает документ с разработкой из справочника (или отвязывает при
    development_id=None) и обновляет денормализованные dev_tags в Qdrant.
    """
    doc = _registry.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    new_dev_id = body.development_id
    if new_dev_id is not None and get_development_registry().get(new_dev_id) is None:
        raise HTTPException(status_code=422, detail="Разработка не найдена")

    old_dev_id = doc.get("development_id")
    fields: dict = {
        "development_id": new_dev_id,
        "development_suggestion": None,
    }
    if new_dev_id is None:
        fields["development_confidence"] = None
        fields["development_confirmed_by"] = None
    elif body.confirmed:
        fields["development_confidence"] = 1.0
        fields["development_confirmed_by"] = user.username
    _registry.update(doc_id, **fields)

    dev_tags = get_development_registry().dev_tags(new_dev_id) if new_dev_id else []
    ok = reindex_document_dev_tags(doc_id, dev_tags)
    sync_pending = False
    if not ok:
        schedule_document_dev_tags_sync(doc_id)
        sync_pending = True

    audit.record(
        user,
        audit.DOCUMENT_DEVELOPMENT_SET,
        audit.TARGET_DOCUMENT,
        target_id=doc_id,
        old_value={"development_id": old_dev_id},
        new_value={"development_id": new_dev_id},
        ip_address=_client_ip(request),
    )
    result = _registry.get(doc_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    result["dev_tags_sync_pending"] = sync_pending
    return result


@router.patch("/{doc_id}/tags", response_model=DocumentOut)
def update_document_tags_endpoint(
    doc_id: str,
    body: DocumentTagsUpdate,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Полная замена набора глобальных тегов документа (Этап 4a).

    Обновляет canonical (`document_tags`) и проекции (`okf_concepts.tags`,
    frontmatter .md-бандлов, Qdrant payload). Тег, равный номеру разработки,
    синхронизируется через dev_sync (Этап 4). Каждое изменение — в audit_log.
    """
    if not _valid_doc_id(doc_id):
        raise HTTPException(status_code=404, detail="Документ не найден")
    try:
        result = update_document_tags(
            doc_id, body.tags, user, ip_address=_client_ip(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    doc = result["doc"]
    doc["dev_tags_sync_pending"] = result["dev_tags_sync_pending"]
    return doc


@router.post("/{doc_id}/detect-development", response_model=DetectDevelopmentOut)
def detect_document_development(
    doc_id: str,
    user: User = Depends(require_role("editor", "admin")),
):
    """On-demand автоопределение номера разработки (regex + LLM) и возврат кандидата."""
    doc = _registry.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    markdown = _document_head(doc_id, doc)
    detection = detect(markdown, doc["filename"], doc_id)
    if detection.confidence is not None:
        attach_development(doc_id, detection)
    return DetectDevelopmentOut(
        development_id=detection.development_id,
        number=detection.number,
        name=detection.name,
        module=detection.module,
        confidence=detection.confidence,
        matched=detection.matched,
    )


@router.get("/{doc_id}/duplicates")
def list_document_duplicates(doc_id: str, user: User = Depends(require_user)):
    """Кандидаты-дубликаты документа (Level 2 — почти идентичные, Level 3 — похожие)."""
    if not _registry.get(doc_id):
        raise HTTPException(status_code=404, detail="Документ не найден")
    return find_duplicates_for_document(doc_id)


@router.delete("/{doc_id}")
def delete_document(
    doc_id: str,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Удаление документа в корзину (мягкое, Этап 4a.2).

    Документ скрывается из списков немедленно, но точки Qdrant и файлы не
    удаляются — помечаются `deleted=true` (payload) + `deleted_at` (БД).
    Окончательное физическое удаление — фоновой автоочисткой корзины.
    """
    doc = _registry.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if doc.get("deleted_at") is not None:
        raise HTTPException(status_code=409, detail="Документ уже находится в корзине")
    _pipeline.soft_delete(doc_id, deleted_by=user.username)
    audit.record(
        user,
        audit.DOCUMENT_DELETE,
        audit.TARGET_DOCUMENT,
        target_id=doc_id,
        old_value={"filename": doc.get("filename")},
        ip_address=_client_ip(request),
    )
    return {"status": "deleted"}


@router.post("/{doc_id}/restore", response_model=DocumentOut)
def restore_document_endpoint(
    doc_id: str,
    request: Request,
    force: bool = False,
    user: User = Depends(require_role("editor", "admin")),
):
    """Восстановление документа из корзины (снятие обоих флагов).

    Без force — при конфликте дедупликации с активным документом возвращает 409
    с code=duplicate и кандидатами; frontend показывает тот же UI конфликта, что
    при загрузке. force=true — восстановить как отдельный без проверки.
    """
    if not _valid_doc_id(doc_id):
        raise HTTPException(status_code=404, detail="Документ не найден")
    try:
        return restore_document(doc_id, user, ip_address=_client_ip(request), force=force)
    except RestoreConflictError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "code": "duplicate",
                "duplicates": exc.duplicates,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/bulk-restore")
def bulk_restore_documents(
    body: BulkOperationRequest,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Массовое восстановление из корзины (симметрично массовому удалению).

    Синхронная операция: восстановление дешёвое (set_payload + UPDATE), не
    требует очереди. Каждый восстановленный документ — отдельная запись в audit.
    """
    doc_ids = list(dict.fromkeys(body.doc_ids))
    if not doc_ids:
        raise HTTPException(status_code=400, detail="Список документов пуст")
    if len(doc_ids) > get_settings().bulk_tags_max_docs:
        raise HTTPException(
            status_code=400,
            detail=f"Превышен лимит {get_settings().bulk_tags_max_docs} документов на одну операцию",
        )
    result = bulk_restore(doc_ids, user, ip_address=_client_ip(request))
    result["total"] = len(doc_ids)
    return result


@router.post("/{doc_id}/resume", response_model=DocumentOut)
def resume_document(
    doc_id: str,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    doc = _registry.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if doc.get("status") not in ("paused", "failed"):
        raise HTTPException(status_code=400, detail="Документ не требует возобновления")
    try:
        _pipeline.resume(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(
        user,
        audit.DOCUMENT_RESUME,
        audit.TARGET_DOCUMENT,
        target_id=doc_id,
        ip_address=_client_ip(request),
    )
    return _registry.get(doc_id)


@router.post("/{doc_id}/regenerate", response_model=DocumentOut)
def regenerate_document(
    doc_id: str,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Полная перегенерация концептов документа через LLM (с текущими промптами).

    Удаляет старый OKF-бандл, staging и векторы, затем запускает пайплайн с нуля.
    """
    doc = _registry.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if doc.get("status") in ("uploaded", "processing", "splitting", "indexing"):
        raise HTTPException(status_code=409, detail="Документ уже обрабатывается")
    try:
        _pipeline.regenerate(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(
        user,
        audit.DOCUMENT_REGENERATE,
        audit.TARGET_DOCUMENT,
        target_id=doc_id,
        old_value={"filename": doc.get("filename")},
        ip_address=_client_ip(request),
    )
    return _registry.get(doc_id)


@router.post("/bulk-preview", response_model=BulkPreviewOut)
def bulk_preview(
    body: BulkOperationRequest,
    user: User = Depends(require_role("admin")),
):
    """Предпросмотр масштаба массовой операции без её выполнения."""
    doc_ids = list(dict.fromkeys(body.doc_ids))
    documents = []
    missing = []
    for doc_id in doc_ids:
        doc = _registry.get(doc_id)
        if doc is None:
            missing.append(doc_id)
        else:
            documents.append(
                {"id": doc["id"], "filename": doc["filename"], "status": doc["status"]}
            )
    estimated = len(documents) * get_settings().bulk_regenerate_est_minutes_per_doc
    return BulkPreviewOut(
        requested=len(doc_ids),
        matched=len(documents),
        missing=missing,
        documents=documents,
        estimated_minutes=estimated,
    )


@router.post("/bulk-delete")
def bulk_delete(
    body: BulkOperationRequest,
    request: Request,
    user: User = Depends(require_role("admin")),
):
    """Массовое удаление документов (ставится в очередь, four-eyes выше порога)."""
    doc_ids = _resolve_doc_ids(body.doc_ids, get_settings().bulk_delete_max_docs)
    try:
        job = get_job_queue().submit(
            BULK_DELETE, doc_ids, user, ip_address=_client_ip(request)
        )
    except QueueOverloadedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return job


@router.post("/bulk-regenerate")
def bulk_regenerate(
    body: BulkOperationRequest,
    request: Request,
    user: User = Depends(require_role("admin")),
):
    """Массовая перегенерация концептов (очередь + per-user rate limit)."""
    settings = get_settings()
    doc_ids = _resolve_doc_ids(body.doc_ids, settings.bulk_regenerate_max_docs)
    try:
        get_rate_limiter().check_bulk_regenerate(
            user.user_id,
            len(doc_ids),
            max_ops_per_hour=settings.bulk_regenerate_max_ops_per_hour,
            max_docs_per_hour=settings.bulk_regenerate_max_docs_per_hour,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(max(1, int(exc.retry_after)))},
        ) from exc
    try:
        job = get_job_queue().submit(
            BULK_REGENERATE, doc_ids, user, ip_address=_client_ip(request)
        )
    except QueueOverloadedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return job


@router.post("/bulk-tags")
def bulk_tags(
    body: BulkTagsRequest,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Массовое редактирование тегов (Этап 4a): delta add/remove по списку документов.

    Синхронная операция (правки тегов дешёвые, в отличие от bulk-delete/regenerate).
    Синхронно с `set_document_development`-семантикой: тег-номер разработки
    реиндексируется через dev_sync. Каждый затронутый документ — отдельная запись
    в audit_log (action_type=document_bulk_tags_update).
    """
    doc_ids = _resolve_doc_ids(body.doc_ids, get_settings().bulk_tags_max_docs)
    result = bulk_update_tags(doc_ids, body.add, body.remove, user, ip_address=_client_ip(request))
    result["total"] = len(doc_ids)
    return result


@router.get("/{doc_id}/download")
def download_document(doc_id: str):
    doc = _registry.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    matches = sorted(get_settings().uploads_dir.glob(f"{doc_id}.*"))
    if not matches:
        raise HTTPException(status_code=404, detail="Исходный файл не найден на диске")
    return FileResponse(
        matches[0],
        media_type="application/octet-stream",
        filename=doc.get("filename") or matches[0].name,
    )


@router.get("/{doc_id}/okf", response_model=list[OkfFileOut])
def list_okf_files(doc_id: str):
    bundle_dir = get_settings().okf_dir / doc_id
    manifest_path = bundle_dir / "_files.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            return [
                OkfFileOut(
                    filename=entry["filename"],
                    filepath=str(bundle_dir / entry["filename"]),
                    title=entry.get("title", entry["filename"]),
                    type=entry.get("type", "concept"),
                    tags=entry.get("tags", []),
                    size=entry.get("size", 0),
                    chunk_index=entry.get("chunk_index"),
                )
                for entry in manifest
            ]
        except Exception:
            pass
    if bundle_dir.is_dir():
        files = []
        manifest = []
        for f in sorted(bundle_dir.glob("*.md")):
            meta = _read_frontmatter(f)
            files.append(
                OkfFileOut(
                    filename=f.name,
                    filepath=str(f),
                    title=meta.get("title", f.stem),
                    type=meta.get("type", "concept"),
                    tags=meta.get("tags", []),
                    size=f.stat().st_size,
                    chunk_index=meta.get("chunk_index"),
                )
            )
            manifest.append(
                {
                    "filename": f.name,
                    "title": meta.get("title", f.stem),
                    "type": meta.get("type", "concept"),
                    "tags": meta.get("tags", []),
                    "size": files[-1].size,
                    "chunk_index": meta.get("chunk_index"),
                }
            )
        if files:
            _write_okf_manifest(bundle_dir, manifest)
            return files
    try:
        staging = StagingStore(doc_id)
        if staging.exists():
            return _staging_to_okf_files(staging)
    except Exception:
        pass
    return []


@router.get("/{doc_id}/okf/{filename}")
def get_okf_file(doc_id: str, filename: str):
    if not _valid_doc_id(doc_id):
        raise HTTPException(status_code=404, detail="Файл не найден")
    bundle_dir = (get_settings().okf_dir / doc_id).resolve()
    filepath = (bundle_dir / filename).resolve()
    if filepath.is_relative_to(bundle_dir) and filepath.is_file():
        return Response(content=filepath.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")
    slug = filename.removesuffix(".md")
    try:
        staging = StagingStore(doc_id)
        if staging.exists():
            hit = _find_concept_in_staging(staging, slug)
            if hit:
                concept, chunk_index = hit
                doc = _registry.get(doc_id) or {}
                global_tags = (staging.load() or {}).get("global_tags", [])
                md = _build_markdown(
                    concept,
                    doc.get("filename", ""),
                    doc_id,
                    global_tags=global_tags,
                    chunk_index=chunk_index,
                )
                return Response(content=md, media_type="text/plain; charset=utf-8")
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="Файл не найден")


@router.get("/{doc_id}/okf/attachments/{filename}")
def get_okf_attachment(doc_id: str, filename: str):
    if not _valid_doc_id(doc_id):
        raise HTTPException(status_code=404, detail="Файл не найден")
    attach_dir = (get_settings().okf_dir / doc_id / "attachments").resolve()
    filepath = (attach_dir / filename).resolve()
    if not filepath.is_relative_to(attach_dir) or not filepath.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    media_type = mimetypes.guess_type(filepath.name)[0] or "application/octet-stream"
    return FileResponse(filepath, media_type=media_type)


@router.get("/{doc_id}/chunks", response_model=list[ChunkOut])
def list_chunks(doc_id: str):
    try:
        meta = _pipeline.ensure_chunks(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return meta


@router.get("/{doc_id}/chunks/{chunk_index}")
def get_chunk(doc_id: str, chunk_index: int):
    if not _valid_doc_id(doc_id):
        raise HTTPException(status_code=404, detail="Чанк не найден")
    chunks_dir = (get_settings().okf_dir / doc_id / "chunks").resolve()
    filepath = (chunks_dir / f"chunk_{chunk_index:02d}.md").resolve()
    if filepath.is_relative_to(chunks_dir) and filepath.is_file():
        return Response(content=filepath.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")
    try:
        staging = StagingStore(doc_id)
        if staging.exists():
            staging_path = staging.dir / f"chunk_{chunk_index:02d}.md"
            if staging_path.is_file():
                return Response(content=staging_path.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="Чанк не найден")


@router.get("/{doc_id}/fulltext")
def get_document_fulltext(doc_id: str):
    try:
        _pipeline.ensure_chunks(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    chunks_dir = (get_settings().okf_dir / doc_id / "chunks").resolve()
    if not chunks_dir.is_dir():
        raise HTTPException(status_code=404, detail="Текст документа не найден")
    parts = []
    for f in sorted(chunks_dir.glob("chunk_*.md"), key=lambda p: int(p.stem.split("_")[-1])):
        parts.append(f.read_text(encoding="utf-8"))
    if not parts:
        raise HTTPException(status_code=404, detail="Текст документа не найден")
    return Response(content="\n\n".join(parts), media_type="text/plain; charset=utf-8")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _parse_date_boundary(value: str | None, end_of_day: bool) -> datetime | None:
    """Парсит ISO-дату (YYYY-MM-DD) в timezone-aware UTC-границу для фильтра.

    start_of_day → 00:00:00, end_of_day → 23:59:59.999999 (включительно). Некорректное
    значение возвращает None (фильтр не применяется) — мягкая деградация, не 422.
    """
    if not value:
        return None
    try:
        d = date.fromisoformat(value.strip())
    except ValueError:
        return None
    boundary = time.max if end_of_day else time.min
    return datetime.combine(d, boundary, tzinfo=timezone.utc)


def _to_trash_item(doc: dict, retention_days: int) -> dict:
    """Обогащает документ корзины индикацией срока до окончательного удаления."""
    from datetime import datetime, timedelta, timezone

    deleted_at = doc.get("deleted_at")
    purge_at = None
    days_left = 0
    if deleted_at is not None:
        if deleted_at.tzinfo is None:
            deleted_at = deleted_at.replace(tzinfo=timezone.utc)
        purge_at = deleted_at + timedelta(days=retention_days)
        delta = purge_at - datetime.now(timezone.utc)
        days_left = max(0, int(delta.total_seconds() // 86400) + (1 if delta.total_seconds() % 86400 else 0))
    item = dict(doc)
    item["days_left"] = days_left
    item["purge_at"] = purge_at
    return item


def _document_head(doc_id: str, doc: dict) -> str:
    """Голова текста документа («титульный лист») для автоопределения разработки.

    Читает первый чанк из бандла; если бандла нет — строит чанки из исходника
    через ensure_chunks (ленивый backfill без LLM). Возвращает до dev_title_page_chars.
    """
    settings = get_settings()
    chunks_dir = settings.okf_dir / doc_id / "chunks"
    chunk0 = chunks_dir / "chunk_00.md"
    if not chunk0.is_file():
        try:
            _pipeline.ensure_chunks(doc_id)
        except Exception:
            pass
    if chunk0.is_file():
        return chunk0.read_text(encoding="utf-8")[: settings.dev_title_page_chars]
    return ""


def _resolve_doc_ids(doc_ids: list[str], max_docs: int) -> list[str]:
    """Проверяет и дедуплицирует список ID документов для массовой операции."""
    unique = list(dict.fromkeys(doc_ids))
    if not unique:
        raise HTTPException(status_code=400, detail="Список документов пуст")
    if len(unique) > max_docs:
        raise HTTPException(
            status_code=400,
            detail=f"Превышен лимит {max_docs} документов на одну операцию",
        )
    missing = [d for d in unique if _registry.get(d) is None]
    if missing:
        raise HTTPException(
            status_code=404, detail="Документы не найдены: " + ", ".join(missing)
        )
    return unique


def _read_frontmatter(path: Path) -> dict:
    import yaml

    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        try:
            _, fm, _ = text.split("---", 2)
            return yaml.safe_load(fm) or {}
        except Exception:
            return {}
    return {}


def _write_okf_manifest(bundle_dir: Path, manifest: list[dict]) -> None:
    tmp_path = bundle_dir / "._files.json.tmp"
    try:
        tmp_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp_path, bundle_dir / "_files.json")
    except Exception:
        tmp_path.unlink(missing_ok=True)


def _staging_to_okf_files(staging: StagingStore) -> list[OkfFileOut]:
    """Строит OkfFileOut[] из staging (живая генерация) — концепты по мере создания."""
    manifest = staging.load()
    if not manifest:
        return []
    files: list[OkfFileOut] = []
    chunks_data = manifest.get("chunks_data", {})
    for index in sorted(int(k) for k in chunks_data):
        info = chunks_data[str(index)]
        slugs: list[str] = info.get("slugs", [])
        chunk_path = staging.dir / info.get("file", f"chunk_{index:02d}.json")
        if not chunk_path.is_file():
            continue
        try:
            raw = json.loads(chunk_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for pos, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            slug = slugs[pos] if pos < len(slugs) else f"concept-{index}-{pos}"
            content = item.get("content", "")
            files.append(
                OkfFileOut(
                    filename=f"{slug}.md",
                    filepath=str(chunk_path),
                    title=item.get("title", slug),
                    type=item.get("type", "concept"),
                    tags=item.get("tags", []),
                    size=len(content.encode("utf-8")),
                    chunk_index=index,
                )
            )
    return files


def _find_concept_in_staging(staging: StagingStore, slug: str) -> tuple[Concept, int] | None:
    """Ищет концепт в staging по slug. Возвращает (Concept, chunk_index) или None."""
    manifest = staging.load()
    if not manifest:
        return None
    chunks_data = manifest.get("chunks_data", {})
    for index in sorted(int(k) for k in chunks_data):
        info = chunks_data[str(index)]
        slugs: list[str] = info.get("slugs", [])
        if slug not in slugs:
            continue
        chunk_path = staging.dir / info.get("file", f"chunk_{index:02d}.json")
        if not chunk_path.is_file():
            continue
        try:
            raw = json.loads(chunk_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        pos = slugs.index(slug)
        if pos < len(raw) and isinstance(raw[pos], dict):
            try:
                return Concept(**raw[pos]), index
            except Exception:
                continue
    return None
