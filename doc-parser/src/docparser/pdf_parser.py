"""Разбор PDF: текст по страницам + изображения страниц + вложенные файлы (reader.attachments)."""
import re
from pathlib import Path

from docparser.blocks import Block
from docparser.embedded import process_embedded, save_image_file


def parse_pdf(path: str | Path, attachments_dir: str | Path | None = None) -> list[Block]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    blocks: list[Block] = []
    image_count = 0
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for para in re.split(r"\n\s*\n", text):
            para = re.sub(r"[ \t]+", " ", para).strip()
            if para:
                blocks.append(Block("paragraph", para, meta={"page": i}))

        for image in _page_images(page):
            saved = save_image_file(
                image.data,
                attachments_dir,
                image_count,
                preferred_name=image.name or "",
            )
            image_count += 1
            meta: dict = {"kind": "image", "name": image.name or "", "caption": image.name or "", "page": i}
            if saved is not None:
                meta["name"] = saved.name
                meta["saved_path"] = str(saved)
            blocks.append(Block("image", "", meta=meta))

    for idx, (name, data) in enumerate(_attachments(reader)):
        blocks.extend(process_embedded(data, name, "", "", attachments_dir, idx))

    return blocks


def _page_images(page) -> list:
    try:
        return list(page.images)
    except Exception:
        return []


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
