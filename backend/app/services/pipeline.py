"""Пайплайн обработки документа: parse -> OKF -> embed -> index (в фоновом потоке)."""
import logging
import shutil
import threading
import uuid
from pathlib import Path

from app.config import get_settings
from app.services.embedder import Embedder
from app.services.okf_generator import OKFGenerator
from app.services.registry import DocumentRegistry
from app.services.vector_store import VectorStore
from docparser import SUPPORTED_EXTENSIONS, blocks_to_markdown, parse_document

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self):
        self.settings = get_settings()
        self.registry = DocumentRegistry()
        self.embedder = Embedder()
        self.vector_store = VectorStore()
        self.okf_generator = OKFGenerator()

    def ingest(
        self,
        doc_id: str,
        filepath: str | Path,
        filename: str,
        user_tags: list[str] | None = None,
    ) -> None:
        thread = threading.Thread(
            target=self._run,
            args=(doc_id, str(filepath), filename, user_tags or []),
            daemon=True,
        )
        thread.start()

    def _run(self, doc_id: str, filepath: str, filename: str, user_tags: list[str]) -> None:
        try:
            self.registry.update(doc_id, status="processing", error=None)
            attachments_dir = self.settings.okf_dir / doc_id / "attachments"
            blocks = parse_document(filepath, filename, attachments_dir=attachments_dir)
            markdown = blocks_to_markdown(blocks)
            attachments = _collect_attachments(blocks, attachments_dir)

            self.registry.update(doc_id, status="splitting")
            concepts = self.okf_generator.generate(markdown, filename)
            if user_tags:
                for concept in concepts:
                    concept.tags = _merge_tags(concept.tags, user_tags)

            self.registry.update(doc_id, status="indexing")
            okf_docs = self.okf_generator.save_bundle(
                doc_id, filename, concepts, attachments=attachments, global_tags=user_tags
            )

            vectors = self.embedder.embed_texts([doc.content for doc in okf_docs])
            self.vector_store.ensure_collection()
            self.vector_store.index_concepts(doc_id, okf_docs, vectors)

            self.registry.update(doc_id, status="done", okf_file_count=len(okf_docs))
            logger.info("Документ %s обработан: %d OKF-концептов", filename, len(okf_docs))
        except Exception as exc:
            logger.exception("Ошибка обработки документа %s", filename)
            self.registry.update(doc_id, status="error", error=str(exc))

    def remove(self, doc_id: str) -> None:
        try:
            self.vector_store.delete_document(doc_id)
        except Exception as exc:
            logger.warning("Не удалось удалить векторы документа %s: %s", doc_id, exc)
        for base in (self.settings.uploads_dir, self.settings.okf_dir):
            target = base / doc_id
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
        for f in self.settings.uploads_dir.glob(f"{doc_id}.*"):
            f.unlink(missing_ok=True)
        self.registry.delete(doc_id)


def save_upload(file_bytes: bytes, original_filename: str) -> tuple[str, Path]:
    ext = Path(original_filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Неподдерживаемый тип файла: {ext}. Допустимы: {sorted(SUPPORTED_EXTENSIONS)}")
    settings = get_settings()
    doc_id = uuid.uuid4().hex[:16]
    dest = settings.uploads_dir / f"{doc_id}{ext}"
    dest.write_bytes(file_bytes)
    return doc_id, dest


def _merge_tags(base: list[str], extra: list[str]) -> list[str]:
    seen = set(base)
    merged = list(base)
    for tag in extra:
        tag = tag.strip()
        if tag and tag not in seen:
            seen.add(tag)
            merged.append(tag)
    return merged


def _collect_attachments(blocks, base_dir: Path) -> list[dict]:
    base = Path(base_dir).resolve()
    attachments = []
    for b in blocks:
        if getattr(b, "type", None) != "attachment":
            continue
        meta = b.meta or {}
        saved = meta.get("saved_path")
        relative = None
        if saved:
            try:
                relative = str(Path(saved).resolve().relative_to(base))
            except ValueError:
                relative = str(Path(saved))
        attachments.append(
            {
                "name": meta.get("name", ""),
                "kind": meta.get("kind", "other"),
                "caption": meta.get("caption", ""),
                "saved_path": relative,
            }
        )
    return attachments
