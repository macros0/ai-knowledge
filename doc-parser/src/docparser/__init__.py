# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""docparser — извлечение текста из DOCX / XLSX / PDF / EML в структурированные блоки.

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

from .archive_guard import ArchiveLimitError
from .blocks import Block
from .markdown import blocks_to_markdown, markdown_attachment_spans
from .parser import (
    SUPPORTED_EXTENSIONS,
    ParseError,
    parse_document,
    parse_document_result,
)
from .paths import portable_name
from .pdf_provider import (
    PdfParseError,
    PdfProviderUnavailable,
    get_pdf_provider_metadata,
)
from .source_model import PARSER_VERSION, ParseContext, ParseResult, SourceNode

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "ArchiveLimitError",
    "Block",
    "PARSER_VERSION",
    "ParseContext",
    "ParseContext",
    "ParseError",
    "ParseResult",
    "PdfParseError",
    "PdfProviderUnavailable",
    "SourceNode",
    "blocks_to_markdown",
    "get_pdf_provider_metadata",
    "markdown_attachment_spans",
    "parse_document",
    "parse_document_result",
    "portable_name",
]

__version__ = "0.1.0"
