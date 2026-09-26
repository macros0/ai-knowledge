"""Offline CID references scoped to one mail and admitted local raster files."""
from __future__ import annotations

import io
import re
import warnings
from urllib.parse import unquote

from PIL import Image

from docparser.blocks import Block
from docparser.paths import portable_name


def escape_mail_text(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>!|#&])", r"\\\1", text)


def raster_extension(payload: bytes) -> str | None:
    """Recognize a bounded raster without trusting MIME type or filename.

    Called only after attachment admission. SVG and other active formats are
    kept as downloadable originals and never used as an inline image.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                extension = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WEBP": ".webp"}.get(image.format)
                if extension:
                    image.verify()
                    return extension
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        pass
    return None


def _content_id(value: str) -> str:
    value = value.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    return value if value and not any(char.isspace() or ord(char) < 32 for char in value) else ""


class ContentIdImages:
    """One instance per message; duplicates never choose an arbitrary image."""

    def __init__(self, context=None, source_id: str = "root"):
        self.context = context
        self.source_id = source_id
        self._files: dict[str, str | None] = {}
        self._warned = False

    def register(self, content_id: str, marker: Block | None) -> None:
        key = _content_id(content_id)
        if not key:
            return
        meta = marker.meta if marker is not None else {}
        saved = meta.get("saved_path") if meta.get("image_format") else None
        self._files[key] = None if key in self._files else saved

    def markdown(self, src: str, alt: str) -> str | None:
        if not src.lower().startswith("cid:"):
            return None
        caption = escape_mail_text(re.sub(r"\s+", " ", alt).strip())
        saved = self._files.get(_content_id(unquote(src[4:])))
        if saved:
            return f"![{caption}](attachments/{portable_name(saved)})"
        if not self._warned and self.context is not None:
            self.context.warn(self.source_id, "cid_image_unavailable")
            self._warned = True
        return caption
