"""Генерация OKF-файлов (YAML-фронтматтер + Markdown) из текста документа через LLM."""
import re
from datetime import date
from pathlib import Path
from typing import Protocol

import yaml

from app.config import get_settings
from app.models.schemas import Concept, OkfDocument
from app.prompts.store import get_store
from app.services.llm_client import LLMClient

VALID_TYPES = {"concept", "procedure", "reference", "example", "note"}


class LLMLike(Protocol):
    def chat_json(self, system: str, user: str) -> list | dict: ...


class OKFGenerator:
    def __init__(self, llm: LLMLike | None = None, bundle_root: Path | None = None):
        self.llm = llm or LLMClient()
        self.settings = get_settings()
        self.prompts = get_store()
        self.bundle_root = bundle_root

    def generate(self, markdown_text: str, filename: str) -> list[Concept]:
        chunks = self.chunk_text(markdown_text)
        concepts: list[Concept] = []
        for i, chunk in enumerate(chunks, start=1):
            concepts.extend(self.generate_chunk(chunk, filename, i, len(chunks)))
        return concepts

    def chunk_text(self, markdown_text: str) -> list[str]:
        return _chunk_text(markdown_text, self.settings.okf_max_chunk_chars)

    def generate_chunk(self, chunk: str, filename: str, index: int, total: int) -> list[Concept]:
        """Генерация OKF-концептов для одного чанка (индекс — 1-based)."""
        if total <= 1:
            prompt = self.prompts.format("okf_user", filename=filename, content=chunk)
        else:
            prompt = self.prompts.format("okf_chunk", filename=filename, index=index, total=total, content=chunk)
        raw = self.llm.chat_json(self.prompts.get("okf_system"), prompt)
        return _normalize(raw)

    def save_bundle(
        self,
        doc_id: str,
        filename: str,
        concepts: list[Concept],
        attachments: list[dict] | None = None,
        global_tags: list[str] | None = None,
        slugs: list[str] | None = None,
        bundle_root: Path | None = None,
    ) -> list[OkfDocument]:
        bundle_dir = bundle_root or self.bundle_root or self.settings.okf_dir / doc_id
        bundle_dir.mkdir(parents=True, exist_ok=True)
        global_tags = global_tags or []
        okf_docs: list[OkfDocument] = []
        seen: set[str] = set()
        for i, concept in enumerate(concepts):
            if not concept.title or not concept.content:
                continue
            if slugs is not None and i < len(slugs) and slugs[i]:
                slug = slugs[i]
            else:
                slug = _slugify(concept.title) or concept.id or f"concept-{i}"
            if slug in seen:
                slug = f"{slug}-{i}"
            seen.add(slug)
            filepath = bundle_dir / f"{slug}.md"
            markdown = _build_markdown(concept, filename, doc_id, attachments=attachments, global_tags=global_tags)
            filepath.write_text(markdown, encoding="utf-8")
            metadata = {
                "type": concept.type,
                "title": concept.title,
                "tags": concept.tags,
                "global_tags": global_tags,
                "source_document": {"filename": filename, "doc_id": doc_id},
                "relations": concept.relations,
                "attachments": attachments or [],
            }
            okf_docs.append(
                OkfDocument(filepath=str(filepath), metadata=metadata, content=concept.content, markdown=markdown)
            )
        return okf_docs


def _build_markdown(
    concept: Concept,
    filename: str,
    doc_id: str,
    attachments: list[dict] | None = None,
    global_tags: list[str] | None = None,
) -> str:
    meta = {
        "type": concept.type,
        "title": concept.title,
        "tags": concept.tags,
        "global_tags": global_tags or [],
        "source_document": {"filename": filename, "doc_id": doc_id},
        "relations": concept.relations,
        "attachments": attachments or [],
        "created_at": date.today().isoformat(),
    }
    frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)
    body = _truncate_content(concept.content, get_settings().okf_max_concept_chars)
    return f"---\n{frontmatter}---\n\n# {concept.title}\n\n{body}\n"


def _normalize(raw: list | dict) -> list[Concept]:
    items = raw if isinstance(raw, list) else [raw]
    concepts: list[Concept] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        ctype = str(item.get("type", "concept")).lower()
        if ctype not in VALID_TYPES:
            ctype = "concept"
        concepts.append(
            Concept(
                id=str(item.get("id", "")).strip(),
                title=str(item.get("title", "")).strip(),
                type=ctype,
                tags=[str(t).strip() for t in item.get("tags", []) if str(t).strip()],
                content=content,
                relations=[str(r).strip() for r in item.get("relations", []) if str(r).strip()],
            )
        )
    return concepts


def _chunk_text(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for unit in _split_units(text):
        if len(current) + len(unit) + 1 > max_chars and current:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{unit}" if current else unit
    if current:
        chunks.append(current)
    return chunks or [text]


def _split_units(text: str) -> list[str]:
    """Резка на неделимые единицы по строкам.

    Единица — группа строк без пустых строк. ```-фенс-блоки и таблицы (строки
    без пустых строк внутри) остаются целыми: пустые строки внутри ```-блока не
    разрывают его. Отступы строк сохраняются (bелые пробелы не срезаются).
    """
    lines = text.split("\n")
    units: list[str] = []
    current: list[str] = []
    in_fence = False

    def flush() -> None:
        if current:
            units.append("\n".join(current))
            current.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                current.append(line)
                flush()
                in_fence = False
            else:
                flush()
                current.append(line)
                in_fence = True
        elif in_fence:
            current.append(line)
        elif stripped:
            current.append(line)
        else:
            flush()
    flush()
    return units


def _truncate_content(text: str, max_chars: int) -> str:
    """Обрезает текст до max_chars по границе строки, не разрывая ```-блок кода.

    Если граница попадает внутрь открытого фенса, неполный блок отбрасывается
    целиком и обрезается по началу этого блока.
    """
    if len(text) <= max_chars:
        return text.rstrip()
    lines = text.split("\n")
    kept: list[str] = []
    chars = 0
    in_fence = False
    block_start = 0
    for line in lines:
        newline_offset = 1 if kept else 0
        if chars + newline_offset + len(line) > max_chars:
            if in_fence:
                kept = kept[:block_start]
            break
        if kept:
            chars += 1
        chars += len(line)
        kept.append(line)
        if line.strip().startswith("```"):
            if not in_fence:
                in_fence = True
                block_start = len(kept) - 1
            else:
                in_fence = False
    return "\n".join(kept).rstrip()


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9\s-]", "", text.lower())
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug or "concept"
