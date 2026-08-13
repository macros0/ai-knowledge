"""Разбор PDF: текст по страницам + вложенные файлы (reader.attachments)."""
import re
from pathlib import Path

from docparser.blocks import Block
from docparser.embedded import process_embedded


def parse_pdf(path: str | Path, attachments_dir: str | Path | None = None) -> list[Block]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    blocks: list[Block] = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for para in re.split(r"\n\s*\n", text):
            para = re.sub(r"[ \t]+", " ", para).strip()
            if para:
                blocks.append(Block("paragraph", para, meta={"page": i}))

    for idx, (name, data) in enumerate(_attachments(reader)):
        blocks.extend(process_embedded(data, name, "", "", attachments_dir, idx))

    return blocks


def _attachments(reader) -> list[tuple[str, bytes]]:
    results: list[tuple[str, bytes]] = []
    try:
        attachments = getattr(reader, "attachments", None) or {}
    except Exception:
        return results
    for name, item in attachments.items():
        data = item.get("data") if isinstance(item, dict) else None
        if isinstance(data, list):
            data = data[0] if data else None
        if isinstance(data, bytes) and data:
            results.append((name, data))
    return results
