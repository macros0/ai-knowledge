"""Чанки, которые никогда не пересекают границу source_id."""
from __future__ import annotations

from docparser import blocks_to_markdown, markdown_attachment_spans


def indexable_blocks(blocks: list) -> list:
    """Keep source content, excluding parser diagnostics and file metadata.

    Attachment markers are authored by the parser, not by the document. Their
    names/statuses remain in the source manifest; feeding them to the LLM can
    create concepts about internal filenames. Match structural metadata only,
    so literal document paragraphs and legacy extracted attachment text survive.
    """
    return [
        block for block in blocks
        if (getattr(block, "meta", {}) or {}).get("extraction_status") != "skipped_disabled"
        and not (
            getattr(block, "type", None) == "attachment"
            and (getattr(block, "meta", {}) or {}).get("attachment") is True
        )
    ]


def chunk_blocks_by_source(blocks: list, generator) -> list[dict]:
    """Рендерит последовательные участки одного источника и режет каждый отдельно.

    Корневой документ может продолжиться после вложения, поэтому один source_id
    допускает несколько участков. Склеивать его через дочерний источник нельзя:
    иначе один чанк теряет единственное происхождение.
    """
    chunks: list[dict] = []
    for source_id, source_blocks in _source_sections(blocks):
        text = blocks_to_markdown(source_blocks)
        if not text:
            continue
        for content in generator.chunk_text(text):
            if content:
                chunks.append({"source_id": source_id, "content": content})
    return chunks


def attachment_shares_by_source(blocks: list, generator) -> list[float]:
    """Attachment coverage in exactly the same source sections as canonical chunks."""
    shares = []
    for source_id, source_blocks in _source_sections(blocks):
        markdown, spans = markdown_attachment_spans(source_blocks)
        if not markdown:
            continue
        texts = [text for text in generator.chunk_text(markdown) if text]
        if source_id != "root":
            shares.extend([1.0] * len(texts))
        elif spans:
            shares.extend(generator.attachment_shares(markdown, spans))
        else:
            shares.extend([0.0] * len(texts))
    return shares


def _source_sections(blocks: list) -> list[tuple[str, list]]:
    sections: list[tuple[str, list]] = []
    current_id: str | None = None
    current_blocks: list = []
    for block in indexable_blocks(blocks):
        meta = (getattr(block, "meta", {}) or {})
        source_id = meta.get("source_id") or "root"
        if current_id is not None and source_id != current_id:
            sections.append((current_id, current_blocks))
            current_blocks = []
        current_id = source_id
        current_blocks.append(block)
    if current_id is not None:
        sections.append((current_id, current_blocks))

    return sections
