# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Роуты загрузки и управления документами."""
import json
import mimetypes
import re
from tempfile import TemporaryDirectory
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Annotated, Literal

from sqlalchemy import func, or_, select

from app.api import errors
from app.services.errors import ConflictError, DomainError
from app.services.artifact_response import OpenedFileResponse
from app.services.generation_store import lock_generation_read
from app.api.errors import ApiError
from fastapi import APIRouter, Depends, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from docparser import markdown_attachment_spans, parse_document, portable_name
from docparser.source_model import ParseContext

from app.auth.models import User
from app.auth.service import require_role, require_user
from app.config import get_settings
from app.db.models import Document, DocumentChunk, DocumentGenerationState, DocumentSource, OkfAttachment, OkfConcept
from app.db.session import session_scope
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
    DocumentUpdateCancel,
    DocumentSourceLocaleUpdate,
    DocumentStatsOut,
    DocumentSourceOut,
    DocumentSourcesOut,
    SourceLocaleFacetsOut,
    DocumentTagsUpdate,
    DocumentTextChunkOut,
    ReferenceLocale,
    OkfFileOut,
    SourceLocationOut,
    TrashListOut,
    UploaderListOut,
)
from app.services import audit
from app.services.deduplication import (
    file_hash_exists,
    file_hash_in_trash,
    find_duplicates_for_document,
    find_duplicates_for_text,
    index_document,
    refresh_duplicate_flags,
    upload_admission_lock,
    set_file_hash,
    sha256_file,
)
from app.services.parser_supervisor import (
    ParserBusyError, ParserIsolationError, ParserMemoryLimitError, ParserTimeoutError, parse_document_supervised,
)
from app.services.dev_detector import attach_development, detect
from app.services.dev_sync import reindex_document_dev_tags, schedule_document_dev_tags_sync
from app.services.development_registry import get_development_registry
from app.services.document_tag_service import bulk_update_tags, update_document_tags
from app.services.job_queue import BULK_DELETE, BULK_REGENERATE, BULK_RESUME, QueueOverloadedError, get_job_queue
from app.services.bulk_generation import generation_skip_code, reserved_generation_doc_ids
from app.services.export_queue import ExportAdmissionError, ExportAuditUnavailableError, get_export_queue
from app.services.okf_generator import _build_markdown
from app.services.pipeline import get_pipeline, save_upload_stream
from app.services.rate_limiter import RateLimitExceeded, get_rate_limiter
from app.services.registry import SERVER_RESTARTED_MESSAGE, get_registry
from app.services.locale_service import request_locale
from app.services.source_locale import is_valid_source_locale, normalize_source_locale
from app.services.source_locale_sync import reindex_document_source_locale, schedule_source_locale_sync
from app.services.source_location import get_document_text_chunks, get_source_location
from app.services.staging import StagingStore
from app.services.tag_registry import TagRegistry, normalize_tags
from app.services.trash import RestoreConflictError, bulk_restore, restore_document

router = APIRouter(prefix="/documents", tags=["documents"])

_registry = get_registry()
_tag_registry = TagRegistry()

EXPORT_ERROR_STATUS = {
    errors.BULK_EXPORT_DISABLED: 404,
    errors.EMPTY_DOCUMENT_LIST: 400,
    errors.INVALID_REQUEST: 400,
    errors.BULK_EXPORT_SOURCE_CONFLICT: 409,
    errors.BULK_EXPORT_USER_ACTIVE: 409,
    errors.BULK_EXPORT_SIZE_LIMIT: 413,
    errors.BULK_EXPORT_RATE_LIMITED: 429,
    errors.BULK_EXPORT_QUEUE_FULL: 503,
    errors.BULK_EXPORT_AUDIT_UNAVAILABLE: 503,
    errors.BULK_EXPORT_STORAGE_QUOTA: 507,
    errors.BULK_EXPORT_STORAGE_RESERVE: 507,
    errors.BULK_EXPORT_NOT_READY: 409,
    errors.BULK_EXPORT_GONE: 410,
    errors.BULK_EXPORT_PART_NOT_FOUND: 404,
}

# doc_id генерируется как uuid.uuid4().hex[:16] (16 hex-символов нижнего
# регистра). Строгий формат не даёт doc_id уйти из okf_bundles через `..`
# или разделители пути — все запросы с несоответствующим id получают 404.
_DOC_ID_RE = re.compile(r"^[0-9a-f]{16}$")

# Мягкая валидация кода языка для GET-фильтра (Этап 7 фаза D): отбрасывает явный
# мусор (пробелы, спецсимволы), но НЕ знает семантику кодов — allowlist остаётся
# только на PATCH, где человек вводит значение руками. Детектор (py3langid, 139
# языков) может поставить документу код вне allowlist (af/eo/ca…), и фильтр по
# нему не должен ломаться 422.
_LOCALE_CODE_RE = re.compile(r"^[a-z]{2,3}$")


def _valid_doc_id(doc_id: str) -> bool:
    return bool(_DOC_ID_RE.fullmatch(doc_id))


def _raise_export_error(exc: ExportAdmissionError) -> None:
    headers = None
    if exc.code == errors.BULK_EXPORT_RATE_LIMITED:
        headers = {"Retry-After": str(max(1, exc.retry_after_seconds or 1))}
    raise ApiError(
        status_code=EXPORT_ERROR_STATUS.get(exc.code, 400),
        code=exc.code,
        detail=str(exc),
        headers=headers,
    ) from exc


def _parse_source_locales(raw: str | None) -> list[str] | None:
    """Разбирает `source_locales=ru,en` → ['ru','en']; мусор → ApiError 422."""
    if raw is None:
        return None
    codes = [c.strip().lower() for c in raw.split(",") if c.strip()]
    if any(not _LOCALE_CODE_RE.fullmatch(c) for c in codes):
        raise ApiError(
            status_code=422,
            code=errors.SOURCE_LOCALE_INVALID,
            detail=f"Недопустимый код языка в фильтре: {raw}",
        )
    return codes or None


@router.post("", response_model=DocumentOut)
def upload_document(
    file: UploadFile,
    request: Request,
    tags: Annotated[list[str] | None, Form()] = None,
    development_id: Annotated[int | None, Form()] = None,
    canonical_locale: Annotated[ReferenceLocale | None, Form()] = None,
    allow_similar: Annotated[bool, Form()] = False,
    user: User = Depends(require_role("editor", "admin")),
):
    """Загрузка документа.

    Обычный def-эндпоинт: FastAPI уводит его в threadpool, поэтому синхронная
    потоковая запись файла не блокирует event loop. Размер ограничен настройкой
    max_upload_mb (проверка по факту дочитывания + по объявленному content-length).
    """
    settings = get_settings()
    extension = Path(file.filename or "").suffix.lower()
    if extension in {".eml", ".msg"} and not settings.mail_import_enabled:
        raise ApiError(
            status_code=503,
            code=errors.MAIL_IMPORT_DISABLED,
            detail="Импорт писем временно отключён до завершения приёмки",
        )
    # Валидация development_id ДО каких-либо побочных эффектов (файл, хеш,
    # строка БД, пул тегов): раньше 422 при невалидном id возникал после
    # сохранения файла и _registry.create — документ-«призрак» навсегда
    # оставался в статусе uploaded и блокировал повторную загрузку файла
    # (зарегистрированный file_hash → 409 duplicate).
    if development_id is not None and get_development_registry().get(development_id) is None:
        raise ApiError(
            status_code=422,
            code=errors.DEVELOPMENT_NOT_FOUND,
            detail="Разработка не найдена",
        )
    max_bytes = settings.max_upload_mb * 1024 * 1024
    declared = file.size or 0
    if declared > max_bytes:
        raise ApiError(
            status_code=413,
            code=errors.FILE_TOO_LARGE,
            detail=f"Файл превышает максимальный размер {settings.max_upload_mb} МБ",
        )
    try:
        doc_id, _dest, size = save_upload_stream(
            file.file, file.filename or "unknown", max_bytes=max_bytes
        )
    except DomainError as exc:
        # Статус выбирается по коду, а не по подстроке русского detail: текст —
        # диагностика и может меняться, код — контракт (services/pipeline.py).
        status = 507 if exc.code == errors.STORAGE_FULL else 413 if exc.code == errors.FILE_TOO_LARGE else 400
        raise errors.domain_error(exc, status) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=400,
            code=errors.INVALID_REQUEST,
            detail=str(exc),
        ) from exc
    if size == 0:
        _dest.unlink(missing_ok=True)
        raise ApiError(
            status_code=400,
            code=errors.EMPTY_FILE,
            detail="Файл пустой",
        )

    # Дедупликация, уровень 1: точное совпадение байтов (SHA-256 файла).
    settings = get_settings()
    file_hash = None
    preview_markdown = None
    preview_mail_fingerprint = None
    trash_twin: dict | None = None
    if settings.dedup_enabled:
        try:
            file_hash = sha256_file(_dest)
        except Exception:
            _dest.unlink(missing_ok=True)
            raise
        existing = file_hash_exists(file_hash)
        if existing is not None:
            _dest.unlink(missing_ok=True)
            return JSONResponse(
                status_code=409,
                content={
                    "detail": f"Файл уже загружен как «{existing['filename']}»",
                    "code": errors.DUPLICATE,
                    "duplicate": existing,
                },
            )
        # Близнец в корзине НЕ блокирует загрузку (осознанное решение,
        # см. deduplication.file_hash_exists) — информационно для тоста.
        trash_twin = file_hash_in_trash(file_hash)

    # Root mail validation is a format/admission check, not a dedup feature.
    # Disabling duplicate detection must not admit corrupt or unreadable mail.
    if settings.dedup_enabled or extension in {".eml", ".msg"}:
        # Extract with the same parser as the pipeline, but keep attachments
        # temporary. No document, tags, LLM calls or search index until consent.
        try:
            with TemporaryDirectory(prefix="upload-check-", dir=settings.uploads_dir) as preview_dir:
                parse_context = ParseContext(
                    file.filename or "unknown", mail_enabled=settings.mail_import_enabled,
                )
                if settings.parser_supervisor_enabled and parse_document.__module__.startswith("docparser"):
                    blocks, parse_context.sources = parse_document_supervised(
                        _dest,
                        file.filename or "unknown",
                        attachments_dir=Path(preview_dir),
                        timeout_seconds=settings.parser_timeout_seconds,
                        max_memory_mb=settings.parser_max_memory_mb,
                        max_concurrent=settings.parser_max_concurrent,
                        mail_enabled=settings.mail_import_enabled,
                    )
                else:
                    blocks = parse_document(
                        _dest, file.filename, attachments_dir=Path(preview_dir), context=parse_context
                    )
                if extension in {".eml", ".msg"}:
                    root_warnings = {
                        warning.get("code")
                        for source in parse_context.sources if source.source_id == "root"
                        for warning in source.warnings
                    }
                    for warning_codes, error_code, detail in (
                        ({"encrypted_mail", "protected_mail"}, errors.MAIL_PROTECTED, "Защищённое письмо не прочитано"),
                        ({"unsupported_mail_class"}, errors.MAIL_OBJECT_UNSUPPORTED, "Тип объекта Outlook не поддерживается"),
                        ({"unsupported_rtf_body"}, errors.MAIL_RTF_UNSUPPORTED, "RTF-only тело письма не поддерживается"),
                    ):
                        if root_warnings & warning_codes:
                            raise ApiError(status_code=422, code=error_code, detail=detail)
                if settings.dedup_enabled:
                    preview_markdown, _ = markdown_attachment_spans(blocks)
                    from app.services.mail_identity import mail_fingerprint_from_parse

                    preview_mail_fingerprint = mail_fingerprint_from_parse(
                        parse_context.sources, blocks, Path(preview_dir)
                    )
        except ParserBusyError as exc:
            _dest.unlink(missing_ok=True)
            raise ApiError(
                status_code=429,
                code=errors.RATE_LIMITED,
                detail="Все процессы разбора документов заняты",
                headers={"Retry-After": "1"},
            ) from exc
        except ParserIsolationError as exc:
            _dest.unlink(missing_ok=True)
            raise ApiError(
                status_code=503,
                code=errors.PARSER_ISOLATION_UNAVAILABLE,
                detail="Защищённый разбор документов недоступен",
            ) from exc
        except (ParserTimeoutError, ParserMemoryLimitError) as exc:
            _dest.unlink(missing_ok=True)
            raise ApiError(
                status_code=422,
                code=errors.PARSER_TIMEOUT if isinstance(exc, ParserTimeoutError) else errors.PARSER_RESOURCE_LIMIT,
                detail="Разбор документа превысил защитный лимит",
            ) from exc
        except ApiError:
            _dest.unlink(missing_ok=True)
            raise
        except Exception as exc:
            _dest.unlink(missing_ok=True)
            from app.services.storage import is_storage_full

            if is_storage_full(exc):
                raise ApiError(status_code=507, code=errors.STORAGE_FULL,
                               detail="Недостаточно места для проверки документа") from exc
            raise ApiError(status_code=422,
                           code=errors.MAIL_PARSE_FAILED if extension in {".eml", ".msg"} else errors.INVALID_REQUEST,
                           detail="Не удалось извлечь текст для проверки документа") from exc

    try:
        with upload_admission_lock():
            return _admit_upload(
                file=file, request=request, tags=tags, development_id=development_id,
                canonical_locale=canonical_locale, allow_similar=allow_similar, user=user,
                doc_id=doc_id, dest=_dest, size=size, file_hash=file_hash,
                preview_markdown=preview_markdown, preview_mail_fingerprint=preview_mail_fingerprint,
                trash_twin=trash_twin,
            )
    finally:
        if _registry.get(doc_id) is None:
            _dest.unlink(missing_ok=True)


def _admit_upload(*, file, request, tags, development_id, canonical_locale, allow_similar,
                  user, doc_id, dest, size, file_hash, preview_markdown, preview_mail_fingerprint, trash_twin):
    """Called under upload_admission_lock; publish signature before releasing it."""
    duplicates = {"level2": [], "level3": []}
    # Another upload may have finished parsing while this request was parsing.
    if file_hash:
        existing = file_hash_exists(file_hash)
        if existing is not None:
            return JSONResponse(status_code=409, content={
                "detail": f"Файл уже загружен как «{existing['filename']}»",
                "code": errors.DUPLICATE, "duplicate": existing,
            })
    if preview_markdown is not None:
        try:
            duplicates = find_duplicates_for_text(
                preview_markdown, mail_fingerprint=preview_mail_fingerprint
            )
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        if not allow_similar and (duplicates["level2"] or duplicates["level3"]):
            dest.unlink(missing_ok=True)
            return JSONResponse(status_code=409, content={
                "code": errors.SIMILAR_DOCUMENT,
                "detail": "Найдены похожие документы. Подтвердите загрузку.",
                "duplicates": duplicates,
            })

    user_tags = normalize_tags(tags)
    origin_locale = canonical_locale or request_locale(request, fallback="und")
    _tag_registry.add(user_tags, canonical_locale=origin_locale)
    doc = _registry.create(
        doc_id,
        file.filename or "unknown",
        file.content_type or "",
        size,
        tags=user_tags,
        canonical_locale=origin_locale,
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
            raise ApiError(
            status_code=422,
            code=errors.DEVELOPMENT_NOT_FOUND,
            detail="Разработка не найдена",
        )
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
    try:
        if preview_markdown is not None:
            index_document(doc_id, preview_markdown, preview_mail_fingerprint)
            doc = _registry.get(doc_id) or doc
        get_pipeline().ingest(
            doc_id,
            get_settings().uploads_dir / f"{doc_id}{Path(file.filename or '').suffix.lower()}",
            doc["filename"],
            user_tags=user_tags,
        )
    except Exception as exc:
        # Admission has not started a worker, so there are no vectors to delete.
        # Also undo badges already published by index_document on its neighbors.
        _registry.delete(doc_id)
        dest.unlink(missing_ok=True)
        neighbor_ids = {item["doc"]["id"] for group in duplicates.values() for item in group}
        refresh_duplicate_flags(doc_id, neighbor_ids)
        if isinstance(exc, DomainError):
            status = 503 if exc.code == errors.QUEUE_OVERLOADED else 400
            raise errors.domain_error(exc, status) from exc
        raise
    audit_value = {"filename": doc.get("filename"), "size": size}
    if bound_development_id is not None:
        audit_value["development_id"] = bound_development_id
    if trash_twin is not None:
        audit_value["duplicate_in_trash"] = {
            "id": trash_twin.get("id"),
            "filename": trash_twin.get("filename"),
        }
    audit.record(
        user,
        audit.DOCUMENT_UPLOAD,
        audit.TARGET_DOCUMENT,
        target_id=doc_id,
        new_value=audit_value,
        ip_address=_client_ip(request),
    )
    if trash_twin is not None:
        doc = dict(doc)
        doc["duplicate_in_trash"] = trash_twin
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
    source_locales: str | None = None,
    source_locale_unknown: bool = False,
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
    problem=true — объединённое «Проблемные» (остановившиеся + дубликаты +
    неполнота генерации/индексации); предупреждения дерева вложений не входят;
    date_from/date_to — диапазон дат загрузки (ISO YYYY-MM-DD, включительно,
    date_to трактуется как конец дня в UTC).

    source_locales=ru,en — язык документа (WHERE source_locale IN (...));
    source_locale_unknown=true — документы без языка (source_locale IS NULL);
    сочетание кодов и unknown — OR (см. _conditions). Мягкая валидация кода
    (regex ^[a-z]{2,3}$); мусор → 422 source_locale_invalid.

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
        source_locales=_parse_source_locales(source_locales),
        source_locale_unknown=source_locale_unknown,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return DocumentListOut(documents=docs, total=total, limit=limit, offset=offset)


@router.get("/stats", response_model=DocumentStatsOut)
def document_stats(user: User = Depends(require_user)):
    """Прогресс разметки по активной базе (Этап 4.1/5): total и с development_id."""
    return _registry.markup_stats()


@router.get("/queue-status", response_model=dict[str, int])
def document_queue_status(response: Response, user: User = Depends(require_role("editor", "admin"))):
    """Общая загрузка конвейера без сведений о чужих документах."""
    response.headers["Cache-Control"] = "no-store"
    return get_pipeline().queue_status()


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


@router.get("/source-locale-facets", response_model=SourceLocaleFacetsOut)
def source_locale_facets(
    uploader: str | None = None,
    user: User = Depends(require_user),
):
    """Счётчики языков документа для фасет-фильтра (Этап 7 фаза D).

    Visibility-ограничения те же, что у списка: только активные документы
    (deleted_at IS NULL) + опционально scope по uploader («mine» резолвится на
    фронтенде в username). Прочие фильтры списка НЕ учитываются — фасеты отвечают
    «какие языки есть в видимом корпусе», счётчики стабильны.

    Объявлен ДО `/{doc_id}` (иначе «source-locale-facets» попало бы в doc_id).
    """
    return SourceLocaleFacetsOut(items=_registry.source_locale_facets(uploaded_by=uploader))


@router.get("/{doc_id}/sources", response_model=DocumentSourcesOut)
def get_document_sources(doc_id: str, user: User = Depends(require_user)):
    """Возвращает зарегистрированное дерево исходников без абсолютных путей."""
    _require_active_document(doc_id)
    with session_scope() as session:
        rows = session.query(DocumentSource).filter(DocumentSource.doc_id == doc_id).all()
    sources = [
        DocumentSourceOut(
            source_id=row.source_id,
            parent_source_id=row.parent_source_id,
            ordinal=row.ordinal,
            kind=row.kind,
            display_name=row.display_name,
            metadata=row.metadata_json or {},
            saved_path=row.saved_path,
            extraction_status=row.extraction_status,
            artifact_kind=row.artifact_kind,
            container_source_id=row.container_source_id,
            container_locator=row.container_locator,
            parser_version=row.parser_version,
            warnings=row.warnings or [],
        )
        for row in sorted(rows, key=_source_sort_key)
    ]
    return DocumentSourcesOut(sources=sources)


@router.get("/{doc_id}/sources/download")
def download_document_source(
    doc_id: str,
    source_id: str = Query(min_length=1, max_length=255),
    user: User = Depends(require_user),
):
    """Скачивает только зарегистрированный байтовый артефакт источника."""
    doc = _require_active_document(doc_id)
    with session_scope() as session:
        lock_generation_read(session, [doc_id])
        doc = session.get(Document, doc_id)
        if doc is None or doc.deleted_at is not None:
            raise ApiError(status_code=404, code=errors.DOCUMENT_NOT_FOUND, detail="Документ не найден")
        source = session.get(DocumentSource, {"doc_id": doc_id, "source_id": source_id})
        if source is None:
            raise ApiError(status_code=404, code=errors.DOCUMENT_NOT_FOUND, detail="Источник не найден")
        target, download_name = _source_download_path(session, doc, source)
        if target is None or not target.is_file():
            raise ApiError(status_code=404, code=errors.FILE_NOT_FOUND, detail="Файл источника не найден")
        return OpenedFileResponse(
            target,
            filename=download_name or doc.filename,
            media_type="application/octet-stream",
            headers={"X-Content-Type-Options": "nosniff"},
        )


@router.get("/{doc_id}", response_model=DocumentOut)
def get_document(doc_id: str):
    if not _valid_doc_id(doc_id):
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документ не найден",
        )
    doc = _registry.get(doc_id)
    # Корзина закрывает доступ к документу так же, как /download: карточка
    # удалённого документа доступна только через GET /documents/trash.
    if not doc or doc.get("deleted_at") is not None:
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документ не найден",
        )
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
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документ не найден",
        )
    new_dev_id = body.development_id
    if new_dev_id is not None and get_development_registry().get(new_dev_id) is None:
        raise ApiError(
            status_code=422,
            code=errors.DEVELOPMENT_NOT_FOUND,
            detail="Разработка не найдена",
        )

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
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документ не найден",
        )
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
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документ не найден",
        )
    try:
        result = update_document_tags(
            doc_id, body.tags, user, ip_address=_client_ip(request),
            canonical_locale=body.canonical_locale or request_locale(request, fallback="und"),
        )
    except DomainError as exc:
        raise errors.domain_error(exc, 404) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail=str(exc),
        ) from exc
    doc = result["doc"]
    doc["dev_tags_sync_pending"] = result["dev_tags_sync_pending"]
    return doc


@router.patch("/{doc_id}/source-locale", response_model=DocumentOut)
def update_document_source_locale_endpoint(
    doc_id: str,
    body: DocumentSourceLocaleUpdate,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Ручная правка языка исходного документа (Этап 7 фаза D).

    Ставит `source_locale_source='manual'` — значение защищено от перезаписи
    следующим `regenerate` (guard в pipeline._finalize). `null` — сброс: и
    значение, и признак → None (документ снова под авто-детекцией). Код
    валидируется по `KNOWN_SOURCE_LOCALES` ∪ `locales` (иначе 422).
    """
    if not _valid_doc_id(doc_id):
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документ не найден",
        )
    doc = _registry.get(doc_id)
    if not doc:
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документ не найден",
        )

    if body.source_locale is None:
        fields = {"source_locale": None, "source_locale_source": None}
    else:
        code = normalize_source_locale(body.source_locale)
        if not is_valid_source_locale(code):
            raise ApiError(
                status_code=422,
                code=errors.SOURCE_LOCALE_INVALID,
                detail=f"Недопустимый код языка: {body.source_locale}",
            )
        fields = {"source_locale": code, "source_locale_source": "manual"}

    audit.record(
        user,
        audit.DOCUMENT_SOURCE_LOCALE_UPDATE,
        audit.TARGET_DOCUMENT,
        target_id=doc_id,
        old_value={
            "source_locale": doc.get("source_locale"),
            "source_locale_source": doc.get("source_locale_source"),
        },
        new_value=fields,
        ip_address=_client_ip(request),
    )
    _registry.update(doc_id, **fields)

    # Синк payload Qdrant (concept + chunk точки) — денормализованная проекция
    # source_locale для фильтра поиска. Синхронная попытка + фоновый фолбэк
    # (паттерн dev_sync); расхождение лечится следующим _finalize.
    sync_pending = False
    if not reindex_document_source_locale(doc_id, fields.get("source_locale")):
        schedule_source_locale_sync(doc_id)
        sync_pending = True

    result = _registry.get(doc_id)
    if result is None:
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документ не найден",
        )
    result["source_locale_sync_pending"] = sync_pending
    return result


@router.post("/{doc_id}/detect-development", response_model=DetectDevelopmentOut)
def detect_document_development(
    doc_id: str,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """On-demand автоопределение номера разработки (regex + LLM) и возврат кандидата."""
    doc = _registry.get(doc_id)
    if not doc:
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документ не найден",
        )
    old_dev_id = doc.get("development_id")
    markdown = _document_head(doc_id, doc)
    detection = detect(markdown, doc["filename"], doc_id)
    if detection.confidence is not None:
        attach_development(doc_id, detection)
        # Мутация development_id/suggestion автоопределением — та же запись
        # ИБ, что и ручная привязка (раньше путь был вовсе без audit).
        audit.record(
            user,
            audit.DOCUMENT_DEVELOPMENT_SET,
            audit.TARGET_DOCUMENT,
            target_id=doc_id,
            old_value={"development_id": old_dev_id},
            new_value={"development_id": detection.development_id},
            ip_address=_client_ip(request),
            meta={"source": "auto"},
        )
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
    _require_active_document(doc_id)
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
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документ не найден",
        )
    if doc.get("deleted_at") is not None:
        raise ApiError(
            status_code=409,
            code=errors.ALREADY_IN_TRASH,
            detail="Документ уже находится в корзине",
        )
    get_pipeline().soft_delete(doc_id, deleted_by=user.username)
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
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документ не найден",
        )
    try:
        return restore_document(doc_id, user, ip_address=_client_ip(request), force=force)
    except RestoreConflictError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "code": errors.DUPLICATE,
                "duplicates": exc.duplicates,
            },
        )
    except DomainError as exc:
        raise errors.domain_error(exc, 404) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail=str(exc),
        ) from exc


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
        raise ApiError(
            status_code=400,
            code=errors.EMPTY_DOCUMENT_LIST,
            detail="Список документов пуст",
        )
    if len(doc_ids) > get_settings().bulk_tags_max_docs:
        raise ApiError(
            status_code=400,
            code=errors.DOCUMENT_LIMIT_EXCEEDED,
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
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документ не найден",
        )
    if doc.get("deleted_at") is not None:
        raise ApiError(status_code=404, code=errors.DOCUMENT_NOT_FOUND, detail="Документ удалён")
    if doc.get("status") not in ("paused", "failed") and not (
        doc.get("status") == "done" and doc.get("partial_chunks")
    ):
        raise ApiError(
            status_code=400,
            code=errors.NOT_RESUMABLE,
            detail="Документ не требует возобновления",
        )
    try:
        get_pipeline().resume(doc_id)
    except DomainError as exc:
        status = 503 if exc.code == errors.QUEUE_OVERLOADED else 400
        raise errors.domain_error(exc, status) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=400,
            code=errors.INVALID_REQUEST,
            detail=str(exc),
        ) from exc
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
    """Готовит новое поколение через LLM, сохраняя опубликованный результат."""
    doc = _registry.get(doc_id)
    if not doc:
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документ не найден",
        )
    if doc.get("status") in ("uploaded", "queued", "processing", "splitting", "indexing"):
        raise ApiError(
            status_code=409,
            code=errors.ALREADY_PROCESSING,
            detail="Документ уже обрабатывается",
        )
    try:
        get_pipeline().regenerate(doc_id)
    except DomainError as exc:
        status = 503 if exc.code == errors.QUEUE_OVERLOADED else 400
        raise errors.domain_error(exc, status) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=400,
            code=errors.INVALID_REQUEST,
            detail=str(exc),
        ) from exc
    audit.record(
        user,
        audit.DOCUMENT_REGENERATE,
        audit.TARGET_DOCUMENT,
        target_id=doc_id,
        old_value={"filename": doc.get("filename")},
        ip_address=_client_ip(request),
    )
    return _registry.get(doc_id)


@router.post("/{doc_id}/cancel-update", response_model=DocumentOut)
def cancel_document_update(
    doc_id: str,
    body: DocumentUpdateCancel,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Abandon the exact update displayed by the UI and retain its published base."""
    try:
        get_pipeline().cancel_update(
            doc_id, body.update_id, user=user, ip_address=_client_ip(request),
        )
    except ConflictError as exc:
        raise errors.domain_error(exc, 409) from exc
    except DomainError as exc:
        raise errors.domain_error(exc, 404 if exc.code == errors.DOCUMENT_NOT_FOUND else 400) from exc
    return _registry.get(doc_id)


@router.post("/bulk-preview", response_model=BulkPreviewOut)
def bulk_preview(
    body: BulkOperationRequest,
    user: User = Depends(require_role("admin")),
    operation: Literal["regenerate", "resume"] | None = Query(default=None),
):
    """Предпросмотр масштаба массовой операции без её выполнения."""
    doc_ids = list(dict.fromkeys(body.doc_ids))
    documents = []
    missing = []
    eligible = []
    skipped = []
    with session_scope() as session:
        reserved = reserved_generation_doc_ids(session) if operation == "resume" else set()
    for doc_id in doc_ids:
        doc = _registry.get(doc_id)
        if doc is None:
            missing.append(doc_id)
        else:
            documents.append(
                {"id": doc["id"], "filename": doc["filename"], "status": doc["status"]}
            )
        if operation:
            skip_code = generation_skip_code(doc, resume=operation == "resume")
            if not skip_code and doc_id in reserved:
                skip_code = errors.ALREADY_PROCESSING
            if skip_code:
                skipped.append({"doc_id": doc_id, "error_code": skip_code})
            else:
                eligible.append(doc_id)
    estimated = len(documents) * get_settings().bulk_regenerate_est_minutes_per_doc
    return BulkPreviewOut(
        requested=len(doc_ids),
        matched=len(documents),
        missing=missing,
        documents=documents,
        estimated_minutes=estimated,
        eligible_doc_ids=eligible if operation else None,
        skipped=skipped,
        max_docs=(get_settings().bulk_resume_max_docs if operation == "resume" else get_settings().bulk_regenerate_max_docs),
    )


@router.get("/bulk-resume/interrupted")
def preview_interrupted_documents(user: User = Depends(require_role("admin"))):
    """Snapshot across all uploaders/filters, capped without silently losing count."""
    limit = get_settings().bulk_resume_max_docs
    conditions = (
        Document.deleted_at.is_(None),
        Document.status == "paused",
        or_(Document.error_code == errors.SERVER_RESTARTED,
            (Document.error_code.is_(None)) & (Document.error == SERVER_RESTARTED_MESSAGE)),
    )
    with session_scope() as session:
        conditions += (Document.id.not_in(reserved_generation_doc_ids(session)),)
        total = session.scalar(select(func.count()).select_from(Document).where(*conditions))
        rows = session.execute(
            select(Document.id, Document.filename, Document.status)
            .where(*conditions).order_by(Document.id).limit(limit)
        ).all()
    documents = [dict(row._mapping) for row in rows]
    return {"requested": total, "matched": len(documents), "documents": documents,
            "eligible_doc_ids": [d["id"] for d in documents], "skipped": [], "max_docs": limit}


@router.post("/bulk-resume")
def bulk_resume(
    body: BulkOperationRequest,
    request: Request,
    user: User = Depends(require_role("admin")),
):
    """Resume selected documents through the queue, preserving saved checkpoints."""
    settings = get_settings()
    # Missing/deleted documents are reported individually by the worker.
    doc_ids = list(dict.fromkeys(body.doc_ids))
    if not doc_ids:
        raise ApiError(status_code=400, code=errors.EMPTY_DOCUMENT_LIST, detail="Список документов пуст")
    if len(doc_ids) > settings.bulk_resume_max_docs:
        raise ApiError(status_code=400, code=errors.DOCUMENT_LIMIT_EXCEEDED, detail="Превышен лимит документов")
    try:
        get_rate_limiter().check_action(user.user_id, BULK_RESUME,
            max_requests=settings.bulk_resume_max_ops_per_hour, window_seconds=3600)
        return get_job_queue().submit(BULK_RESUME, doc_ids, user, ip_address=_client_ip(request))
    except RateLimitExceeded as exc:
        raise ApiError(status_code=429, code=errors.RATE_LIMITED, detail=str(exc),
                       headers={"Retry-After": str(max(1, int(exc.retry_after)))}) from exc
    except QueueOverloadedError as exc:
        raise ApiError(status_code=503, code=errors.QUEUE_OVERLOADED, detail=str(exc)) from exc
    except ConflictError as exc:
        raise errors.domain_error(exc, 409) from exc


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
        raise ApiError(
            status_code=503,
            code=errors.QUEUE_OVERLOADED,
            detail=str(exc),
        ) from exc
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
        raise ApiError(
            status_code=429,
            code=errors.RATE_LIMITED,
            detail=str(exc),
            headers={"Retry-After": str(max(1, int(exc.retry_after)))},
        ) from exc
    try:
        job = get_job_queue().submit(
            BULK_REGENERATE, doc_ids, user, ip_address=_client_ip(request)
        )
    except QueueOverloadedError as exc:
        raise ApiError(
            status_code=503,
            code=errors.QUEUE_OVERLOADED,
            detail=str(exc),
        ) from exc
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
    result = bulk_update_tags(doc_ids, body.add, body.remove, user, ip_address=_client_ip(request),
                              canonical_locale=body.canonical_locale or request_locale(request, fallback="und"))
    result["total"] = len(doc_ids)
    return result


@router.get("/{doc_id}/download")
def download_document(doc_id: str, user: User = Depends(require_user)):
    """Скачивает активный root-файл по тем же правилам, что source download."""
    if not _valid_doc_id(doc_id):
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документ не найден",
        )
    doc = _require_active_document(doc_id)
    matches = sorted(get_settings().uploads_dir.glob(f"{doc_id}.*"))
    if not matches:
        raise ApiError(
            status_code=404,
            code=errors.FILE_NOT_FOUND,
            detail="Исходный файл не найден на диске",
        )
    return FileResponse(
        matches[0],
        media_type="application/octet-stream",
        filename=doc.filename or matches[0].name,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.post("/{doc_id}/export-okf")
def export_okf_document(
    doc_id: str,
    request: Request,
    user: User = Depends(require_user),
):
    """Экспорт OKF-бандла (YAML/Markdown + _files.json + chunks + attachments) из БД в ZIP.

    Этап 2b / Фаза 5: бандл — производная проекция PostgreSQL, генерируется по
    явному запросу; в штатной работе файлы не требуются.
    """
    import shutil
    import tempfile
    import zipfile

    from app.services.export_okf import export_okf_bundle
    from starlette.background import BackgroundTask

    _require_active_document(doc_id)

    tmp = Path(tempfile.mkdtemp(prefix=f"okf-export-{doc_id}-"))
    dest = tmp / "bundle"
    zip_path = tmp / f"okf_{doc_id}.zip"

    def cleanup() -> None:
        shutil.rmtree(tmp, ignore_errors=True)

    try:
        export_okf_bundle(doc_id, dest)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(dest.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(dest).as_posix())
        audit.record(
            user,
            audit.DOCUMENT_EXPORT,
            audit.TARGET_DOCUMENT,
            target_id=doc_id,
            ip_address=_client_ip(request),
        )
    except DomainError as exc:
        cleanup()
        raise errors.domain_error(exc, 404) from exc
    except ValueError as exc:
        cleanup()
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception:
        cleanup()
        raise

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"okf_{doc_id}.zip",
        background=BackgroundTask(cleanup),
    )


@router.post("/bulk-export", status_code=202)
def bulk_export(
    body: BulkOperationRequest,
    request: Request,
    user: User = Depends(require_role("admin")),
):
    """Queue an audited raw-document export without reading sources in HTTP."""
    if not get_settings().bulk_export_enabled:
        raise ApiError(
            status_code=404,
            code=errors.BULK_EXPORT_DISABLED,
            detail="Массовый экспорт отключён",
        )
    try:
        return get_export_queue().submit(body.doc_ids, user, ip_address=_client_ip(request))
    except ExportAuditUnavailableError as exc:
        raise ApiError(status_code=503, code=exc.code, detail=str(exc)) from exc
    except ExportAdmissionError as exc:
        _raise_export_error(exc)


@router.get("/{doc_id}/okf", response_model=list[OkfFileOut])
def list_okf_files(doc_id: str):
    # DB-first (Этап 2b): список концептов — канонически в okf_concepts, файлы
    # бандла — производная проекция. Валидация формата обязательна до любого
    # доступа к ФС (staging-fallback использует doc_id как путь).
    _require_active_document(doc_id)
    files = _okf_files_from_db(doc_id)
    if files:
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
        raise ApiError(
            status_code=404,
            code=errors.FILE_NOT_FOUND,
            detail="Файл не найден",
        )
    _require_active_document(doc_id)
    slug = filename.removesuffix(".md")
    md = _concept_markdown_from_db(doc_id, slug)
    if md is not None:
        return Response(content=md, media_type="text/plain; charset=utf-8")
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
    raise ApiError(
            status_code=404,
            code=errors.FILE_NOT_FOUND,
            detail="Файл не найден",
        )


@router.get("/{doc_id}/okf/attachments/{filename}")
def get_okf_attachment(doc_id: str, filename: str):
    if not _valid_doc_id(doc_id) or portable_name(filename) != filename or filename in {".", ".."}:
        raise ApiError(
            status_code=404,
            code=errors.FILE_NOT_FOUND,
            detail="Файл не найден",
        )
    # Этап 2b: бинарники вложений — в uploads/<doc_id>/attachments/ (байты-источники
    # в FS, описанные в okf_attachments), а не в производном бандле.
    attach_dir = (get_settings().uploads_dir / doc_id / "attachments").resolve()
    with session_scope() as session:
        lock_generation_read(session, [doc_id])
        # Корзина закрывает и legacy-путь без активной генерации: байты вложений
        # лежат в uploads/ до физической очистки.
        document = session.get(Document, doc_id)
        if document is None or document.deleted_at is not None:
            raise ApiError(status_code=404, code=errors.FILE_NOT_FOUND, detail="Файл не найден")
        state = session.get(DocumentGenerationState, doc_id)
        active_id = state.active_generation_id if state else None
        if active_id:
            from app.services.generation_files import generation_paths

            saved_path = f"generations/{active_id}/attachments/{filename}"
            registered = session.scalar(select(OkfAttachment.id).where(
                OkfAttachment.doc_id == doc_id, OkfAttachment.saved_path == saved_path,
            ))
            if registered is None:
                raise ApiError(status_code=404, code=errors.FILE_NOT_FOUND, detail="Файл не найден")
            attach_dir = generation_paths(get_settings(), doc_id, active_id).attachments.resolve()
        filepath = (attach_dir / filename).resolve()
        if not filepath.is_relative_to(attach_dir) or not filepath.is_file():
            raise ApiError(status_code=404, code=errors.FILE_NOT_FOUND, detail="Файл не найден")
        media_type = mimetypes.guess_type(filepath.name)[0] or "application/octet-stream"
        return OpenedFileResponse(
            filepath,
            media_type=media_type,
            headers={"Content-Disposition": "attachment", "X-Content-Type-Options": "nosniff"},
        )


@router.get("/{doc_id}/chunks", response_model=list[ChunkOut])
def list_chunks(doc_id: str):
    # Гейт корзины до ensure_chunks: иначе удалённый документ не только читался
    # бы, но и запускал ленивый пере-парсинг исходника.
    _require_active_document(doc_id)
    return _ensure_chunks_for_read(doc_id)


@router.get("/{doc_id}/chunks/{chunk_index}")
def get_chunk(doc_id: str, chunk_index: int):
    if not _valid_doc_id(doc_id):
        raise ApiError(
            status_code=404,
            code=errors.CHUNK_NOT_FOUND,
            detail="Чанк не найден",
        )
    _require_active_document(doc_id)
    content = _chunk_content_from_db(doc_id, chunk_index)
    if content is not None:
        return Response(content=content, media_type="text/plain; charset=utf-8")
    try:
        staging = StagingStore(doc_id)
        if staging.exists():
            staging_path = staging.dir / f"chunk_{chunk_index:02d}.md"
            if staging_path.is_file():
                return Response(content=staging_path.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")
    except Exception:
        pass
    raise ApiError(
            status_code=404,
            code=errors.CHUNK_NOT_FOUND,
            detail="Чанк не найден",
        )


@router.get("/{doc_id}/concepts/{slug}/source-location", response_model=SourceLocationOut)
def get_concept_source_location(doc_id: str, slug: str):
    if not _valid_doc_id(doc_id) or not slug or len(slug) > 255:
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Источник концепта не найден",
        )
    _require_active_document(doc_id)
    location = get_source_location(doc_id, slug)
    if location is None:
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Источник концепта не найден",
        )
    return location


@router.get("/{doc_id}/fulltext/chunks", response_model=list[DocumentTextChunkOut])
def get_document_text_chunks_endpoint(doc_id: str):
    _require_active_document(doc_id)
    chunks = get_document_text_chunks(doc_id)
    if not chunks:
        _ensure_chunks_for_read(doc_id)
        chunks = get_document_text_chunks(doc_id)
    if not chunks:
        raise ApiError(
            status_code=404,
            code=errors.TEXT_NOT_FOUND,
            detail="Текст документа не найден",
        )
    return chunks


@router.get("/{doc_id}/fulltext")
def get_document_fulltext(doc_id: str):
    # Этап 2b: полный текст — конкатенация document_chunks (БД), а не чтение
    # chunk_XX.md из бандла.
    _require_active_document(doc_id)
    parts = _fulltext_from_db(doc_id)
    if not parts:
        _ensure_chunks_for_read(doc_id)
        parts = _fulltext_from_db(doc_id)
    if not parts:
        raise ApiError(
            status_code=404,
            code=errors.TEXT_NOT_FOUND,
            detail="Текст документа не найден",
        )
    return Response(content="\n\n".join(parts), media_type="text/plain; charset=utf-8")


# Статусы ошибок ленивого построения текста при чтении. Прочие доменные коды
# (например, file_not_found) сохраняют прежний контракт — 400.
_CHUNK_READ_ERROR_STATUS = {
    errors.RATE_LIMITED: 429,
    errors.PARSER_TIMEOUT: 422,
    errors.PARSER_RESOURCE_LIMIT: 422,
    errors.TEXT_NOT_FOUND: 422,
    errors.PARSER_ISOLATION_UNAVAILABLE: 503,
}


def _ensure_chunks_for_read(doc_id: str) -> list[dict]:
    """Чанки для GET-эндпоинта: ленивый разбор без ожидания и без повторов."""
    try:
        return get_pipeline().ensure_chunks(doc_id, interactive=True)
    except DomainError as exc:
        status = _CHUNK_READ_ERROR_STATUS.get(exc.code, 400)
        headers = {"Retry-After": "5"} if status == 429 else None
        raise ApiError(status_code=status, code=exc.code, detail=str(exc), headers=headers) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=400,
            code=errors.INVALID_REQUEST,
            detail=str(exc),
        ) from exc


def _require_active_document(doc_id: str) -> Document:
    if not _valid_doc_id(doc_id):
        raise ApiError(status_code=404, code=errors.DOCUMENT_NOT_FOUND, detail="Документ не найден")
    with session_scope() as session:
        document = session.get(Document, doc_id)
        if document is None or document.deleted_at is not None:
            raise ApiError(status_code=404, code=errors.DOCUMENT_NOT_FOUND, detail="Документ не найден")
        return document


def _source_sort_key(row: DocumentSource) -> tuple:
    def segment(value: str) -> tuple[int, int | str]:
        return (0, int(value)) if value.isdigit() else (1, value)

    return tuple(segment(value) for value in row.source_id.split("/"))


def _source_download_path(
    session, document: Document, source: DocumentSource
) -> tuple[Path | None, str | None]:
    """Разрешает зарегистрированный файл и его честное имя контейнера."""
    seen: set[str] = set()
    current = source
    document_root = (get_settings().uploads_dir / document.id).resolve()
    while current is not None and current.source_id not in seen:
        seen.add(current.source_id)
        if current.source_id == "root":
            suffix = Path(document.filename).suffix.lower()
            return get_settings().uploads_dir / f"{document.id}{suffix}", document.filename
        if current.saved_path:
            candidate = (document_root / current.saved_path).resolve()
            try:
                candidate.relative_to(document_root)
            except ValueError:
                return None, None
            return candidate, current.display_name
        if not current.container_source_id:
            return None, None
        current = session.get(
            DocumentSource,
            {"doc_id": document.id, "source_id": current.container_source_id},
        )
    return None, None


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

    Этап 2b: читает chunk 0 из document_chunks (БД); если нет — ленивый backfill
    через ensure_chunks. Возвращает до dev_title_page_chars.
    """
    settings = get_settings()
    content = _chunk_content_from_db(doc_id, 0)
    if content is None:
        try:
            get_pipeline().ensure_chunks(doc_id, interactive=True)
        except Exception:
            pass
        content = _chunk_content_from_db(doc_id, 0)
    if content:
        return content[: settings.dev_title_page_chars]
    return ""


def _concept_list_sort_key(chunk_index: int | None, source_spans: list | None, slug: str) -> tuple:
    """Порядок списка концептов по документу, с честным fallback внутри чанка."""
    if chunk_index is None:
        return (1, 0, 1, 0, str(slug))

    starts: list[int] = []
    for span in source_spans or []:
        start = span.get("start") if isinstance(span, dict) else getattr(span, "start", None)
        end = span.get("end") if isinstance(span, dict) else getattr(span, "end", None)
        if isinstance(start, int) and not isinstance(start, bool) and isinstance(end, int) and end > start >= 0:
            starts.append(start)
    if starts:
        return (0, int(chunk_index), 0, min(starts), str(slug))
    return (0, int(chunk_index), 1, 0, str(slug))


def _okf_files_from_db(doc_id: str) -> list[OkfFileOut]:
    """OkfFileOut[] из okf_concepts (БД) — канонический список концептов документа."""
    okf_dir = get_settings().okf_dir / doc_id
    with session_scope() as s:
        rows = (
            s.query(OkfConcept)
            .filter(OkfConcept.doc_id == doc_id)
            .all()
        )
    rows.sort(key=lambda c: _concept_list_sort_key(c.chunk_index, c.source_spans, c.slug))
    return [
        OkfFileOut(
            filename=f"{c.slug}.md",
            filepath=str(okf_dir / f"{c.slug}.md"),
            title=c.title,
            type=c.type,
            tags=list(c.tags or []),
            size=len((c.content or "").encode("utf-8")),
            chunk_index=c.chunk_index,
        )
        for c in rows
    ]


def _concept_markdown_from_db(doc_id: str, slug: str) -> str | None:
    """Рендерит markdown концепта из okf_concepts (БД) — зеркало бандл-файла."""
    doc = _registry.get(doc_id)
    if doc is None:
        return None
    with session_scope() as s:
        c = (
            s.query(OkfConcept)
            .filter(OkfConcept.doc_id == doc_id, OkfConcept.slug == slug)
            .first()
        )
        if c is None:
            return None
        attachments = [
            {"name": a.name, "kind": a.kind, "caption": a.caption, "saved_path": a.saved_path}
            for a in s.query(OkfAttachment).filter(OkfAttachment.doc_id == doc_id).all()
        ]
        concept = Concept(
            id="",
            title=c.title,
            type=c.type,
            tags=list(c.tags or []),
            content=c.content or "",
            relations=list(c.relations or []),
        )
        generated_at = c.generated_at.date().isoformat() if c.generated_at else None
        chunk_index = c.chunk_index
    return _build_markdown(
        concept,
        doc.get("filename", ""),
        doc_id,
        attachments=attachments,
        global_tags=list(doc.get("tags") or []),
        chunk_index=chunk_index,
        generated_at=generated_at,
    )


def _chunk_content_from_db(doc_id: str, chunk_index: int) -> str | None:
    with session_scope() as s:
        row = (
            s.query(DocumentChunk.content)
            .filter(DocumentChunk.doc_id == doc_id, DocumentChunk.chunk_index == chunk_index)
            .first()
        )
    return row[0] if row else None


def _fulltext_from_db(doc_id: str) -> list[str]:
    with session_scope() as s:
        rows = (
            s.query(DocumentChunk.content)
            .filter(DocumentChunk.doc_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        )
    return [r[0] for r in rows]


def _resolve_doc_ids(doc_ids: list[str], max_docs: int) -> list[str]:
    """Проверяет и дедуплицирует список ID документов для массовой операции."""
    unique = list(dict.fromkeys(doc_ids))
    if not unique:
        raise ApiError(
            status_code=400,
            code=errors.EMPTY_DOCUMENT_LIST,
            detail="Список документов пуст",
        )
    if len(unique) > max_docs:
        raise ApiError(
            status_code=400,
            code=errors.DOCUMENT_LIMIT_EXCEEDED,
            detail=f"Превышен лимит {max_docs} документов на одну операцию",
        )
    missing = [d for d in unique if _registry.get(d) is None]
    if missing:
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail="Документы не найдены: " + ", ".join(missing,
        )
        )
    return unique


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
        for pos, item in sorted(enumerate(raw), key=lambda pair: _concept_list_sort_key(
            index,
            pair[1].get("source_spans") if isinstance(pair[1], dict) else None,
            slugs[pair[0]] if pair[0] < len(slugs) else f"concept-{index}-{pair[0]}",
        )):
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
