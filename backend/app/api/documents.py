# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Роуты загрузки и управления документами."""
import json
import mimetypes
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

from app.auth.models import User
from app.auth.service import require_role, require_user
from app.config import get_settings
from app.models.schemas import (
    BulkOperationRequest,
    BulkPreviewOut,
    Concept,
    ChunkOut,
    DocumentListOut,
    DocumentOut,
    OkfFileOut,
)
from app.services import audit
from app.services.job_queue import BULK_DELETE, BULK_REGENERATE, QueueOverloadedError, get_job_queue
from app.services.okf_generator import _build_markdown
from app.services.pipeline import Pipeline, save_upload
from app.services.rate_limiter import RateLimitExceeded, get_rate_limiter
from app.services.registry import get_registry
from app.services.staging import StagingStore
from app.services.tag_registry import TagRegistry, normalize_tags

router = APIRouter(prefix="/documents", tags=["documents"])

_registry = get_registry()
_pipeline = Pipeline()
_tag_registry = TagRegistry()


@router.post("", response_model=DocumentOut)
async def upload_document(
    file: UploadFile,
    tags: Annotated[list[str] | None, Form()] = None,
    user: User = Depends(require_user),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Файл пустой")
    user_tags = normalize_tags(tags)
    try:
        doc_id, _ = save_upload(content, file.filename or "unknown")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _tag_registry.add(user_tags)
    doc = _registry.create(
        doc_id,
        file.filename or "unknown",
        file.content_type or "",
        len(content),
        tags=user_tags,
        uploaded_by=user.username,
    )
    _pipeline.ingest(doc_id, get_settings().uploads_dir / f"{doc_id}{Path(file.filename or '').suffix.lower()}", doc["filename"], user_tags=user_tags)
    return doc


@router.get("", response_model=DocumentListOut)
def list_documents():
    docs = _registry.list()
    docs.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    return DocumentListOut(documents=docs)


@router.get("/{doc_id}", response_model=DocumentOut)
def get_document(doc_id: str):
    doc = _registry.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return doc


@router.delete("/{doc_id}")
def delete_document(
    doc_id: str,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    doc = _registry.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    _pipeline.remove(doc_id)
    audit.record(
        user,
        audit.DOCUMENT_DELETE,
        audit.TARGET_DOCUMENT,
        target_id=doc_id,
        old_value={"filename": doc.get("filename")},
        ip_address=_client_ip(request),
    )
    return {"status": "deleted"}


@router.post("/{doc_id}/resume", response_model=DocumentOut)
def resume_document(doc_id: str):
    doc = _registry.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if doc.get("status") not in ("paused", "failed"):
        raise HTTPException(status_code=400, detail="Документ не требует возобновления")
    try:
        _pipeline.resume(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    bundle_dir = get_settings().okf_dir / doc_id
    filepath = (bundle_dir / filename).resolve()
    if str(filepath).startswith(str(bundle_dir.resolve())) and filepath.is_file():
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
    attach_dir = (get_settings().okf_dir / doc_id / "attachments").resolve()
    filepath = (attach_dir / filename).resolve()
    if not str(filepath).startswith(str(attach_dir)) or not filepath.is_file():
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
    chunks_dir = (get_settings().okf_dir / doc_id / "chunks").resolve()
    filepath = (chunks_dir / f"chunk_{chunk_index:02d}.md").resolve()
    if str(filepath).startswith(str(chunks_dir)) and filepath.is_file():
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
