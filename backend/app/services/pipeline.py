"""Пайплайн обработки документа: parse -> OKF (staging) -> embed -> index (в фоновом потоке).

Генерация OKF идёт инкрементально: результат каждого чанка LLM сразу пишется в
staging-каталог (data/staging/{doc_id}/) с manifest.json. При сбое на любом чанке
обработанные данные сохраняются (status="paused"), повторный запуск (resume)
пропускает уже готовые чанки. Финальный бандл собирается в okf_bundles через
атомарный перенос, затем концепты индексируются в Qdrant.
"""
import logging
import os
import shutil
import threading
import uuid
from pathlib import Path

from app.config import get_settings
from app.services.embedder import Embedder
from app.services.llm_client import LLMTruncationError, is_fatal_error
from app.services.okf_generator import OKFGenerator
from app.services.registry import get_registry
from app.services.staging import StagingStore
from app.services.vector_store import VectorStore
from docparser import SUPPORTED_EXTENSIONS, blocks_to_markdown, parse_document

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self):
        self.settings = get_settings()
        self.registry = get_registry()
        self.embedder = Embedder()
        self.vector_store = VectorStore()
        self.okf_generator = OKFGenerator()
        self._abort_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

    def ingest(
        self,
        doc_id: str,
        filepath: str | Path,
        filename: str,
        user_tags: list[str] | None = None,
    ) -> None:
        self._start(doc_id, str(filepath), filename, user_tags or [], resume=False)

    def resume(self, doc_id: str) -> None:
        doc = self.registry.get(doc_id)
        if not doc:
            raise ValueError("Документ не найден")
        filename = doc["filename"]
        ext = Path(filename).suffix.lower()
        filepath = self.settings.uploads_dir / f"{doc_id}{ext}"
        if not filepath.is_file():
            raise ValueError("Исходный файл документа не найден")
        self._start(doc_id, str(filepath), filename, doc.get("tags") or [], resume=True)

    def _start(self, doc_id: str, filepath: str, filename: str, user_tags: list[str], resume: bool) -> None:
        self._abort_events[doc_id] = threading.Event()
        thread = threading.Thread(
            target=self._run,
            args=(doc_id, filepath, filename, user_tags, resume),
            daemon=True,
        )
        self._threads[doc_id] = thread
        thread.start()

    def _run(self, doc_id: str, filepath: str, filename: str, user_tags: list[str], resume: bool) -> None:
        try:
            self._process(doc_id, filepath, filename, user_tags, resume=resume)
        except Exception as exc:
            logger.exception("Ошибка обработки документа %s", filename)
            self.registry.update(doc_id, status="error", error=str(exc))
        finally:
            self._abort_events.pop(doc_id, None)
            self._threads.pop(doc_id, None)

    def _process(self, doc_id: str, filepath: str, filename: str, user_tags: list[str], resume: bool) -> None:
        self.registry.update(doc_id, status="processing", error=None)
        attachments_dir = self.settings.okf_dir / doc_id / "attachments"
        blocks = parse_document(filepath, filename, attachments_dir=attachments_dir)
        markdown = blocks_to_markdown(blocks)
        attachments = _collect_attachments(blocks, attachments_dir)

        chunks = self.okf_generator.chunk_text(markdown)
        total = len(chunks)
        staging = StagingStore(doc_id)
        if resume and staging.exists():
            manifest = staging.load()
            done = len(manifest.get("processed_chunks", [])) if manifest else 0
            logger.info("Resume документа %s: продолжено с %d/%d чанков", doc_id, done, total)
        else:
            if resume:
                logger.warning(
                    "[%s] Возобновление без чекпоинтов: staging отсутствует, генерация начнётся с 0",
                    doc_id,
                )
            if staging.exists():
                staging.remove()
            staging.create(total, global_tags=user_tags)
        self.registry.update(doc_id, status="splitting", total_chunks=total, processed_chunks=len(staging.processed_chunks))

        max_chunk_retries = self.settings.llm_chunk_retry_attempts
        chunk_backoff = self.settings.llm_chunk_retry_backoff_seconds

        try:
            for i, chunk in enumerate(chunks):
                if staging.has_chunk(i):
                    continue
                if self._abort_events.get(doc_id, threading.Event()).is_set():
                    logger.info("Генерация %s прервана по запросу удаления", doc_id)
                    return

                concepts = None
                for chunk_attempt in range(1, max_chunk_retries + 1):
                    try:
                        self.registry.update(doc_id, current_chunk=i + 1)
                        concepts = self.okf_generator.generate_chunk(chunk, filename, i + 1, total, doc_id=doc_id)
                        if self._abort_events.get(doc_id, threading.Event()).is_set():
                            logger.info("Генерация %s прервана после чанка %d", doc_id, i + 1)
                            return
                        self.registry.update(doc_id, error=None)
                        break
                    except Exception as exc:
                        if (
                            isinstance(exc, LLMTruncationError)
                            or is_fatal_error(exc)
                            or chunk_attempt == max_chunk_retries
                        ):
                            self.registry.update(doc_id, error=str(exc))
                            raise
                        delay = chunk_backoff * chunk_attempt
                        msg = (
                            f"Сбой генерации чанка ({exc}). "
                            f"Повтор {chunk_attempt}/{max_chunk_retries} через {int(delay)}с..."
                        )
                        logger.warning("Чанк %d/%d: %s", i + 1, total, msg)
                        self.registry.update(doc_id, error=msg)
                        if self._abort_events.get(doc_id, threading.Event()).wait(timeout=delay):
                            return

                if user_tags and concepts:
                    for concept in concepts:
                        concept.tags = _merge_tags(concept.tags, user_tags)
                if concepts is not None:
                    staging.append_chunk(i, concepts)
                self.registry.update(doc_id, processed_chunks=len(staging.processed_chunks), current_chunk=None)
        except Exception as exc:
            logger.warning("Генерация OKF прервана на документе %s: %s", doc_id, exc)
            self.registry.update(
                doc_id,
                status="paused",
                error=str(exc),
                processed_chunks=len(staging.processed_chunks),
            )
            return

        self.registry.update(doc_id, status="indexing")
        try:
            self._finalize(doc_id, filename, staging, attachments=attachments, global_tags=user_tags)
        except Exception as exc:
            # Staging не удаляем: чекпоинты всех чанков сохраняются, чтобы
            # повторный resume повторил только финализацию (embed+index),
            # не перегенерируя концепты через LLM.
            logger.exception("Финализация документа %s не удалась", doc_id)
            self.registry.update(doc_id, status="failed", error=str(exc))
            return

    def _finalize(
        self,
        doc_id: str,
        filename: str,
        staging: StagingStore,
        attachments: list[dict],
        global_tags: list[str],
    ) -> None:
        concepts = staging.concepts()
        slugs = staging.slugs()
        target = self.settings.okf_dir / doc_id
        tmp_dir = self.settings.okf_dir / f".tmp-{doc_id}"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        attach_src = self.settings.okf_dir / doc_id / "attachments"
        if attach_src.is_dir():
            shutil.copytree(attach_src, tmp_dir / "attachments")

        okf_docs = self.okf_generator.save_bundle(
            doc_id,
            filename,
            concepts,
            attachments=attachments,
            global_tags=global_tags,
            bundle_root=tmp_dir,
            slugs=slugs,
        )
        _atomic_move(tmp_dir, target)
        for doc in okf_docs:
            doc.filepath = str(target / Path(doc.filepath).name)

        if not okf_docs:
            logger.warning("[%s] Документ %s не содержит концептов, индексация пропущена", doc_id, filename)
            staging.remove()
            self.registry.update(doc_id, status="done", okf_file_count=0, error=None)
            return

        vectors = self.embedder.embed_texts([doc.content for doc in okf_docs])
        self.vector_store.ensure_collection()
        self.vector_store.index_concepts(doc_id, okf_docs, vectors)

        staging.remove()
        self.registry.update(doc_id, status="done", okf_file_count=len(okf_docs), error=None)
        logger.info("Документ %s обработан: %d OKF-концептов", filename, len(okf_docs))

    def remove(self, doc_id: str) -> None:
        event = self._abort_events.get(doc_id)
        if event:
            event.set()
        thread = self._threads.get(doc_id)
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
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
        StagingStore(doc_id).remove()
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


def _atomic_move(src: Path, dst: Path) -> None:
    """Атомарный перенос каталога (src -> dst) в рамках одной ФС.

    Если staging и okf_bundles на разных файловых системах (EXDEV), os.replace
    недоступен — используется shutil.move (copy + delete).
    """
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst, ignore_errors=True)
        else:
            dst.unlink(missing_ok=True)
    try:
        os.replace(src, dst)
    except OSError:
        shutil.move(str(src), str(dst))


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