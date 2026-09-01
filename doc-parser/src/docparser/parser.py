# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Диспетчер: выбирает парсер по расширению файла."""
from pathlib import Path

from docparser.blocks import Block
from docparser.docx_parser import parse_docx
from docparser.extensions import SUPPORTED_EXTENSIONS
from docparser.pdf_parser import parse_pdf
from docparser.xlsx_parser import parse_xlsx


class ParseError(Exception):
    """Ошибка разбора документа."""


def parse_document(
    path: str | Path,
    filename: str | None = None,
    attachments_dir: str | Path | None = None,
    depth: int = 0,
    budget=None,
) -> list[Block]:
    """depth — уровень вложенности (0 = документ верхнего уровня), budget —
    кумулятивный лимит распакованных вложений (docparser.embedded.AttachmentBudget);
    оба создаются здесь, если не заданы, и пробрасываются в рекурсию."""
    filename = filename or Path(path).name
    ext = Path(filename).suffix.lower()
    if budget is None:
        from docparser.embedded import AttachmentBudget

        budget = AttachmentBudget()
    if ext == ".docx":
        return parse_docx(path, attachments_dir=attachments_dir, depth=depth, budget=budget)
    if ext == ".xlsx":
        return parse_xlsx(path, attachments_dir=attachments_dir, depth=depth, budget=budget)
    if ext == ".pdf":
        return parse_pdf(path, attachments_dir=attachments_dir, depth=depth, budget=budget)
    raise ParseError(f"Неподдерживаемый тип файла: {ext}. Допустимы: {sorted(SUPPORTED_EXTENSIONS)}")
