"""Роуты загрузки и управления документами."""
import json
import mimetypes
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.models.schemas import Concept, ChunkOut, DocumentListOut, DocumentOut, OkfFileOut
from app.services.okf_generator import _build_markdown
from app.services.pipeline import Pipeline, save_upload
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


@router.post("/{doc_id}/regenerate", response_model=DocumentOut)
def regenerate_document(doc_id: str):
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
    if bundle_dir.is_dir():
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
                    chunk_index=meta.get("chunk_index"),
                )
            )
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
    try:
        _pipeline.ensure_chunks(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    chunks_dir = (get_settings().okf_dir / doc_id / "chunks").resolve()
    filepath = (chunks_dir / f"chunk_{chunk_index:02d}.md").resolve()
    if not str(filepath).startswith(str(chunks_dir)) or not filepath.is_file():
        raise HTTPException(status_code=404, detail="Чанк не найден")
    return Response(content=filepath.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")


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
