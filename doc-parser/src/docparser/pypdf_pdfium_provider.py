"""Standard pypdf + PDFium implementation of the PDF provider contract."""

from __future__ import annotations

import io
import logging
import threading
from importlib.metadata import version
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader

from .pdf_provider import PdfAttachment, PdfDocument, PdfImage, PdfParseError

logger = logging.getLogger(__name__)

_PDFIUM_LOCK = threading.RLock()
_FORMAT_EXTENSIONS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "BMP": ".bmp",
    "GIF": ".gif",
    "TIFF": ".tiff",
    "WEBP": ".webp",
}


class PypdfPdfiumProvider:
    name = "pypdf-pdfium"

    def open(self, path: str | Path) -> PdfDocument:
        try:
            return PypdfPdfiumDocument(path)
        except PdfParseError:
            raise
        except Exception as exc:
            raise PdfParseError(f"Unable to open PDF: {exc}") from exc

    def version(self) -> str:
        metadata = self.metadata()
        return f"pypdf={metadata['pypdf_version']}; pdfium={metadata['pdfium_version']}"

    def metadata(self) -> dict[str, str]:
        return {
            "provider": self.name,
            "pypdf_version": version("pypdf"),
            "pdfium_version": str(pdfium.version.PDFIUM_INFO),
        }


class PypdfPdfiumDocument:
    def __init__(self, path: str | Path):
        self._path = str(path)
        try:
            self._reader = PdfReader(self._path)
        except Exception as exc:
            raise PdfParseError(f"Unable to read PDF: {exc}") from exc
        self._pdfium_document: pdfium.PdfDocument | None = None

    def page_count(self) -> int:
        return len(self._reader.pages)

    def extract_text(self, page_index: int) -> str:
        try:
            return self._reader.pages[page_index].extract_text() or ""
        except Exception as exc:
            raise PdfParseError(f"Unable to extract text from page {page_index + 1}: {exc}") from exc

    def extract_images(self, page_index: int) -> list[PdfImage]:
        try:
            page = self._reader.pages[page_index]
            keys = list(page.images.keys())
        except Exception as exc:
            logger.debug("Unable to enumerate images on PDF page %s: %s", page_index + 1, exc)
            return []

        images: list[PdfImage] = []
        for key in keys:
            try:
                image = page.images[key]
                images.append(
                    PdfImage(
                        data=image.data,
                        name=str(key),
                        extension=_extension_for_image(image.data),
                    )
                )
            except Exception as exc:
                logger.debug("Unable to extract PDF image %s on page %s: %s", key, page_index + 1, exc)
        return images

    def render_page_jpeg(self, page_index: int, *, dpi: int, quality: int) -> bytes:
        with _PDFIUM_LOCK:
            return self._render_page_unlocked(page_index, dpi, quality)

    def _render_page_unlocked(self, page_index: int, dpi: int, quality: int) -> bytes:
        try:
            if self._pdfium_document is None:
                self._pdfium_document = pdfium.PdfDocument(self._path)
            page = bitmap = source_image = converted_image = None
            try:
                page = self._pdfium_document[page_index]
                bitmap = page.render(scale=dpi / 72)
                source_image = bitmap.to_pil()
                image = source_image
                if source_image.mode != "RGB":
                    converted_image = source_image.convert("RGB")
                    image = converted_image
                with io.BytesIO() as output:
                    image.save(output, format="JPEG", quality=quality)
                    return output.getvalue()
            finally:
                for resource in (converted_image, source_image, bitmap, page):
                    if resource is not None:
                        resource.close()
        except PdfParseError:
            raise
        except Exception as exc:
            raise PdfParseError(f"Unable to render page {page_index + 1}: {exc}") from exc

    def attachments(self) -> list[PdfAttachment]:
        try:
            attachments = self._reader.attachments
        except Exception as exc:
            logger.debug("Unable to enumerate PDF attachments: %s", exc)
            return []

        results: list[PdfAttachment] = []
        for name, item in attachments.items():
            for data in _attachment_bytes(item):
                results.append(PdfAttachment(name=str(name), data=data))
        return results

    def close(self) -> None:
        with _PDFIUM_LOCK:
            if self._pdfium_document is not None:
                self._pdfium_document.close()
                self._pdfium_document = None
        close_reader = getattr(self._reader, "close", None)
        if close_reader is not None:
            close_reader()


def _extension_for_image(data: bytes) -> str:
    try:
        with Image.open(io.BytesIO(data)) as image:
            return _FORMAT_EXTENSIONS.get(image.format or "", "")
    except Exception:
        return ""


def _attachment_bytes(item: object) -> list[bytes]:
    if isinstance(item, dict):
        item = item.get("data", [])
    if isinstance(item, bytes):
        return [item] if item else []
    if isinstance(item, list):
        return [data for data in item if isinstance(data, bytes) and data]
    return []
