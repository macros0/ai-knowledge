"""Library-neutral PDF provider contract and standard-provider factory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class PdfParseError(Exception):
    """Stable error raised by a PDF provider while reading a document."""


class PdfProviderUnavailable(PdfParseError):
    """The required standard PDF provider cannot be loaded."""


@dataclass(frozen=True)
class PdfImage:
    data: bytes
    name: str
    extension: str


@dataclass(frozen=True)
class PdfAttachment:
    name: str
    data: bytes


class PdfDocument(Protocol):
    def page_count(self) -> int: ...

    def extract_text(self, page_index: int) -> str: ...

    def extract_images(self, page_index: int) -> list[PdfImage]: ...

    def render_page_jpeg(self, page_index: int, *, dpi: int, quality: int) -> bytes: ...

    def attachments(self) -> list[PdfAttachment]: ...

    def close(self) -> None: ...


class PdfProvider(Protocol):
    name: str

    def open(self, path: str | Path) -> PdfDocument: ...

    def version(self) -> str: ...

    def metadata(self) -> dict[str, str]: ...


_METADATA_KEYS = {"provider", "pypdf_version", "pdfium_version"}


def get_pdf_provider() -> PdfProvider:
    """Return the sole standard PDF provider with a normalized load failure."""
    try:
        from .pypdf_pdfium_provider import PypdfPdfiumProvider
    except ImportError as exc:
        raise PdfProviderUnavailable(f"PDF provider pypdf-pdfium is unavailable: {exc}") from exc

    try:
        return PypdfPdfiumProvider()
    except PdfProviderUnavailable:
        raise
    except Exception as exc:
        raise PdfProviderUnavailable(f"PDF provider pypdf-pdfium is unavailable: {exc}") from exc


def get_pdf_provider_metadata() -> dict[str, str]:
    """Return the public, non-secret metadata of the standard provider."""
    metadata = get_pdf_provider().metadata()
    if set(metadata) != _METADATA_KEYS or not all(isinstance(value, str) for value in metadata.values()):
        raise PdfParseError("PDF provider returned invalid metadata")
    return metadata
