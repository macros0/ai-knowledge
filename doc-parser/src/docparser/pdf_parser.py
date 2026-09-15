# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""PDF-to-Block orchestration independent from a concrete PDF library."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable

from docparser.blocks import Block
from docparser.embedded import process_embedded, save_image_file
from docparser.pdf_provider import PdfParseError, PdfProvider, get_pdf_provider

logger = logging.getLogger(__name__)

_RENDER_DPI = 150
_RENDER_JPEG_QUALITY = 80
_RENDER_PAGE_LIMIT = 200


def parse_pdf(
    path: str | Path,
    attachments_dir: str | Path | None = None,
    depth: int = 0,
    budget=None,
    *,
    provider_factory: Callable[[], PdfProvider] = get_pdf_provider,
) -> list[Block]:
    """Extract stable blocks through the configured PDF provider."""
    try:
        provider = provider_factory()
        document = provider.open(path)
    except PdfParseError as exc:
        raise _parse_error(path, "open", exc) from exc

    blocks: list[Block] = []
    image_count = 0
    rendered_pages = 0
    render_limit_warned = False
    try:
        try:
            total_pages = document.page_count()
        except PdfParseError as exc:
            raise _parse_error(path, "count pages", exc) from exc

        for page_index in range(total_pages):
            page_number = page_index + 1
            try:
                text = document.extract_text(page_index)
                page_images = document.extract_images(page_index)
            except PdfParseError as exc:
                raise _parse_error(path, f"read page {page_number}", exc) from exc

            for paragraph in re.split(r"\n\s*\n", text):
                paragraph = re.sub(r"[ \t]+", " ", paragraph).strip()
                if paragraph:
                    blocks.append(Block("paragraph", paragraph, meta={"page": page_number}))

            for image in page_images:
                saved = save_image_file(
                    image.data,
                    attachments_dir,
                    image_count,
                    preferred_name=image.name,
                    ext=image.extension,
                )
                image_count += 1
                meta: dict = {
                    "kind": "image",
                    "name": image.name,
                    "caption": f"Страница {page_number} — {image.name or 'изображение'}",
                    "page": page_number,
                }
                if saved is not None:
                    meta["name"] = saved.name
                    meta["saved_path"] = str(saved)
                blocks.append(Block("image", "", meta=meta))

            if page_images or text.strip() or attachments_dir is None:
                continue
            if rendered_pages >= _RENDER_PAGE_LIMIT:
                if not render_limit_warned:
                    render_limit_warned = True
                    logger.warning(
                        "PDF %s: рендер сканов ограничен %d страницами (всего %d) — остальные пропущены",
                        path,
                        _RENDER_PAGE_LIMIT,
                        total_pages,
                    )
                continue

            try:
                jpeg = document.render_page_jpeg(
                    page_index,
                    dpi=_RENDER_DPI,
                    quality=_RENDER_JPEG_QUALITY,
                )
            except PdfParseError as exc:
                raise _parse_error(path, f"render page {page_number}", exc) from exc

            rendered_pages += 1
            saved = save_image_file(jpeg, attachments_dir, image_count, ext=".jpg")
            image_count += 1
            if page_number == 1 or page_number % 50 == 0 or page_number == total_pages:
                logger.info("Рендер скана: страница %d/%d", page_number, total_pages)
            meta = {
                "kind": "image",
                "name": f"page-{page_number}",
                "caption": f"Страница {page_number} — изображение страницы (скан)",
                "page": page_number,
            }
            if saved is not None:
                meta["name"] = saved.name
                meta["saved_path"] = str(saved)
            blocks.append(Block("image", "", meta=meta))

        try:
            attachments = document.attachments()
        except PdfParseError as exc:
            raise _parse_error(path, "read attachments", exc) from exc
        for index, attachment in enumerate(attachments):
            blocks.extend(
                process_embedded(
                    attachment.data,
                    attachment.name,
                    "",
                    "",
                    attachments_dir,
                    index,
                    depth=depth + 1,
                    budget=budget,
                )
            )
        return blocks
    finally:
        document.close()


def _parse_error(path: str | Path, operation: str, error: PdfParseError):
    from docparser.parser import ParseError

    return ParseError(f"PDF {Path(path).name}: cannot {operation}: {error}")
