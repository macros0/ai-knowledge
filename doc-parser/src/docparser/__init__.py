# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""docparser — извлечение текста из DOCX / XLSX / PDF в структурированные блоки.

Контракт (стабильная публичная API):
    Block                      — единица смыслового блока
    parse_document(path, filename) -> list[Block]
    blocks_to_markdown(blocks) -> str
    SUPPORTED_EXTENSIONS       — какие расширения поддерживаются
    ParseError                 — базовое исключение парсера
"""

from .blocks import Block
from .markdown import blocks_to_markdown
from .parser import SUPPORTED_EXTENSIONS, ParseError, parse_document

__all__ = [
    "Block",
    "ParseError",
    "SUPPORTED_EXTENSIONS",
    "blocks_to_markdown",
    "parse_document",
]

__version__ = "0.1.0"
