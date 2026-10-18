# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Диспетчер: выбирает парсер по расширению файла."""
from pathlib import Path

from docparser.attachment_limits import DEFAULT_ATTACHMENT_MAX_DEPTH
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
    if context.processing_policy is not None and source_id=='root' and depth==0:
        import hashlib
        with Path(path).open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=context.processing_policy.source_file_hash:
                raise ParseError('Processing scope does not match the source file')
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
    elif ext in {'.zip','.7z','.rar'} and context.processing_policy is not None:
        from docparser.archive_parser import parse_archive
        blocks=parse_archive(path,ext[1:],attachments_dir=attachments_dir,depth=depth,
                             budget=budget,context=context,source_id=source_id)
    else:
        raise ParseError(f"Неподдерживаемый тип файла: {ext}. Допустимы: {sorted(SUPPORTED_EXTENSIONS)}")
    for block in blocks:
        block.meta.setdefault("source_id", source_id)
    return blocks


def parse_document_result(
    path: str | Path,
    filename: str | None = None,
    attachments_dir: str | Path | None = None,
    *,
    table_sink=None,
    attachment_max_depth=DEFAULT_ATTACHMENT_MAX_DEPTH,
) -> ParseResult:
    """Разбирает корневой файл и возвращает блоки вместе с деревом источников."""
    filename = filename or Path(path).name
    context = ParseContext(filename, table_sink=table_sink, attachment_max_depth=attachment_max_depth)
    blocks = parse_document(path, filename=filename, attachments_dir=attachments_dir, context=context)
    return ParseResult(
        blocks=blocks, sources=context.sources, warnings=context.warnings,
        parser_version=context.parser_version,
    )
