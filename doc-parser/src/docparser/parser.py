# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Диспетчер: выбирает парсер по расширению файла."""
from pathlib import Path

from docparser.blocks import Block
from docparser.docx_parser import parse_docx
from docparser.eml_parser import parse_eml
from docparser.extensions import SUPPORTED_EXTENSIONS
from docparser.msg_parser import parse_msg
from docparser.pdf_parser import parse_pdf
from docparser.source_model import ParseContext, ParseResult
from docparser.xlsx_parser import parse_xlsx


class ParseError(Exception):
    """Ошибка разбора документа."""


def parse_document(
    path: str | Path,
    filename: str | None = None,
    attachments_dir: str | Path | None = None,
    depth: int = 0,
    budget=None,
    context: ParseContext | None = None,
    source_id: str = "root",
) -> list[Block]:
    """depth — уровень вложенности (0 = документ верхнего уровня), budget —
    кумулятивный лимит распакованных вложений (docparser.embedded.AttachmentBudget);
    оба создаются здесь, если не заданы, и пробрасываются в рекурсию."""
    filename = filename or Path(path).name
    ext = Path(filename).suffix.lower()
    if budget is None:
        from docparser.embedded import AttachmentBudget

        budget = AttachmentBudget()
    context = context or ParseContext(filename)
    if ext == ".docx":
        blocks = parse_docx(path, attachments_dir=attachments_dir, depth=depth, budget=budget,
                            context=context, source_id=source_id)
    elif ext == ".xlsx":
        blocks = parse_xlsx(path, attachments_dir=attachments_dir, depth=depth, budget=budget,
                            context=context, source_id=source_id)
    elif ext == ".pdf":
        blocks = parse_pdf(path, attachments_dir=attachments_dir, depth=depth, budget=budget,
                           context=context, source_id=source_id)
    elif ext == ".eml":
        blocks = parse_eml(path, attachments_dir=attachments_dir, depth=depth, budget=budget,
                           context=context, source_id=source_id)
    elif ext == ".msg":
        blocks = parse_msg(path, attachments_dir=attachments_dir, depth=depth, budget=budget,
                           context=context, source_id=source_id)
    else:
        raise ParseError(f"Неподдерживаемый тип файла: {ext}. Допустимы: {sorted(SUPPORTED_EXTENSIONS)}")
    for block in blocks:
        block.meta.setdefault("source_id", source_id)
    # The shared budget is applied only by the root dispatcher. Nested calls
    # share the same object but must not debit already-produced child blocks a
    # second time when their parent returns them.
    if depth == 0:
        blocks = _limit_canonical_text(blocks, budget, context)
    return blocks


def parse_document_result(
    path: str | Path,
    filename: str | None = None,
    attachments_dir: str | Path | None = None,
) -> ParseResult:
    """Разбирает корневой файл и возвращает блоки вместе с деревом источников."""
    filename = filename or Path(path).name
    context = ParseContext(filename)
    blocks = parse_document(path, filename=filename, attachments_dir=attachments_dir, context=context)
    return ParseResult(
        blocks=blocks, sources=context.sources, warnings=context.warnings,
        parser_version=context.parser_version,
    )


def _limit_canonical_text(blocks: list[Block], budget, context: ParseContext) -> list[Block]:
    """Keep a bounded canonical text projection without losing source metadata."""
    limited: list[Block] = []
    warned = False
    for block in blocks:
        permitted = budget.consume_text(len(block.text))
        if permitted == len(block.text):
            limited.append(block)
            continue
        if permitted:
            limited.append(Block(block.type, block.text[:permitted], block.level, dict(block.meta)))
        elif block.type in {"attachment", "image"}:
            # These blocks carry registered original bytes and source links.
            # Keep their metadata while excluding their human label from the
            # bounded canonical text projection.
            limited.append(Block(block.type, "", block.level, dict(block.meta)))
        if not warned:
            context.warn(block.meta.get("source_id", "root"), "text_limit_exceeded")
            warned = True
    return limited
