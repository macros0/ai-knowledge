# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""docparser — извлечение текста из DOCX / XLSX / PDF в структурированные блоки.

Контракт (стабильная публичная API):
    Block                      — единица смыслового блока
    parse_document(path, filename) -> list[Block]
    blocks_to_markdown(blocks) -> str
    markdown_attachment_spans(blocks) -> (str, list[(start, end)])
    portable_name(saved_path)  -> basename без привязки к ОС-разделителю
    SUPPORTED_EXTENSIONS       — какие расширения поддерживаются
    ParseError                 — базовое исключение парсера
    ArchiveLimitError         — небезопасный OOXML ZIP-контейнер
"""

from .blocks import Block
from .archive_guard import ArchiveLimitError
from .markdown import blocks_to_markdown, markdown_attachment_spans
from .parser import SUPPORTED_EXTENSIONS, ParseError, parse_document
from .paths import portable_name
from .pdf_provider import PdfParseError, PdfProviderUnavailable, get_pdf_provider_metadata

__all__ = [
    "Block",
    "ArchiveLimitError",
    "ParseError",
    "PdfParseError",
    "PdfProviderUnavailable",
    "SUPPORTED_EXTENSIONS",
    "blocks_to_markdown",
    "markdown_attachment_spans",
    "parse_document",
    "portable_name",
    "get_pdf_provider_metadata",
]

__version__ = "0.1.0"
