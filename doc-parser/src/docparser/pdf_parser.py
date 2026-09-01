# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Разбор PDF: текст по страницам + изображения страниц + вложенные файлы (reader.attachments)."""
import io
import logging
import re
from pathlib import Path

from docparser.blocks import Block
from docparser.embedded import process_embedded, save_image_file

logger = logging.getLogger(__name__)

_FORMAT_EXT = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "BMP": ".bmp",
    "GIF": ".gif",
    "TIFF": ".tiff",
    "WEBP": ".webp",
}

# Параметры рендера страниц-сканов (фолбэк PyMuPDF для JBIG2 и т.п.).
_RENDER_DPI = 150
_RENDER_JPEG_QUALITY = 80
# Анти-DoS (2026-09-01): скан на тысячи пустых страниц не рендерим целиком —
# каждое изображение это ~1-2 МБ JPEG на диск. Текст извлекается со всех страниц.
_RENDER_PAGE_LIMIT = 200


def parse_pdf(
    path: str | Path,
    attachments_dir: str | Path | None = None,
    depth: int = 0,
    budget=None,
) -> list[Block]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    blocks: list[Block] = []
    image_count = 0
    total_pages = len(reader.pages)
    render_doc = None
    rendered_pages = 0
    render_limit_warned = False
    try:
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            for para in re.split(r"\n\s*\n", text):
                para = re.sub(r"[ \t]+", " ", para).strip()
                if para:
                    blocks.append(Block("paragraph", para, meta={"page": i}))

            page_images = _page_images(page)
            for image in page_images:
                saved = save_image_file(
                    image.data,
                    attachments_dir,
                    image_count,
                    preferred_name=image.name or "",
                    ext=_image_format(image),
                )
                image_count += 1
                meta: dict = {
                    "kind": "image",
                    "name": image.name or "",
                    "caption": f"Страница {i} — {image.name or 'изображение'}",
                    "page": i,
                }
                if saved is not None:
                    meta["name"] = saved.name
                    meta["saved_path"] = str(saved)
                blocks.append(Block("image", "", meta=meta))

            if not page_images and not text.strip():
                if attachments_dir is None:
                    continue
                if rendered_pages >= _RENDER_PAGE_LIMIT:
                    if not render_limit_warned:
                        render_limit_warned = True
                        logger.warning(
                            "PDF %s: рендер сканов ограничен %d страницами (всего %d) — остальные пропущены",
                            path, _RENDER_PAGE_LIMIT, total_pages,
                        )
                    continue
                if render_doc is None:
                    render_doc = _open_render_doc(path)
                rendered_pages += 1
                saved = _render_page_image(render_doc, i - 1, attachments_dir, image_count)
                image_count += 1
                if i == 1 or i % 50 == 0 or i == total_pages:
                    logger.info("Рендер скана: страница %d/%d", i, total_pages)
                meta = {
                    "kind": "image",
                    "name": f"page-{i}",
                    "caption": f"Страница {i} — изображение страницы (скан)",
                    "page": i,
                }
                if saved is not None:
                    meta["name"] = saved.name
                    meta["saved_path"] = str(saved)
                blocks.append(Block("image", "", meta=meta))
    finally:
        if render_doc is not None:
            try:
                render_doc.close()
            except Exception:
                pass

    for idx, (name, data) in enumerate(_attachments(reader)):
        blocks.extend(
            process_embedded(data, name, "", "", attachments_dir, idx, depth=depth + 1, budget=budget)
        )

    return blocks


def _open_render_doc(path: str | Path):
    import pymupdf

    return pymupdf.open(str(path))


def _render_page_image(doc, page_index: int, attachments_dir, image_count: int) -> Path | None:
    """Рендерит страницу целиком в JPEG (фолбэк для сканов, где pypdf бессилен)."""
    page = doc.load_page(page_index)
    pix = page.get_pixmap(dpi=_RENDER_DPI, alpha=False)
    data = pix.tobytes("jpeg", jpg_quality=_RENDER_JPEG_QUALITY)
    return save_image_file(data, attachments_dir, image_count, ext=".jpg")


def _page_images(page) -> list:
    results: list = []
    try:
        keys = list(page.images.keys())
    except Exception:
        return results
    for key in keys:
        try:
            results.append(page.images[key])
        except Exception as exc:
            logger.debug(
                "Не удалось извлечь изображение %s со страницы %s: %s",
                key,
                getattr(page, "page_number", "?"),
                exc,
            )
    return results


def _image_format(image) -> str:
    """Определяет расширение по фактическому формату байтов (не по имени ресурса PDF)."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(image.data)) as img:
            return _FORMAT_EXT.get(img.format or "", "")
    except Exception:
        return ""


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
