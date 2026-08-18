"""Роуты загрузки и управления документами."""
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.models.schemas import DocumentListOut, DocumentOut, OkfFileOut
from app.services.pipeline import Pipeline, save_upload
from app.services.registry import get_registry
from app.services.tag_registry import TagRegistry, normalize_tags

router = APIRouter(prefix="/documents", tags=["documents"])

_registry = get_registry()
_pipeline = Pipeline()
_tag_registry = TagRegistry()


@router.post("", response_model=DocumentOut)
async def upload_document(
    file: UploadFile,
    tags: Annotated[list[str] | None, Form()] = None,
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
    doc = _registry.create(doc_id, file.filename or "unknown", file.content_type or "", len(content), tags=user_tags)
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
def delete_document(doc_id: str):
    if not _registry.get(doc_id):
        raise HTTPException(status_code=404, detail="Документ не найден")
    _pipeline.remove(doc_id)
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
    if not bundle_dir.is_dir():
        return []
    files = []
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
            )
        )
    return files


@router.get("/{doc_id}/okf/{filename}")
def get_okf_file(doc_id: str, filename: str):
    bundle_dir = get_settings().okf_dir / doc_id
    filepath = (bundle_dir / filename).resolve()
    if not str(filepath).startswith(str(bundle_dir.resolve())) or not filepath.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return filepath.read_text(encoding="utf-8")


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
