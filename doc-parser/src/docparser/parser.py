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
) -> list[Block]:
    filename = filename or Path(path).name
    ext = Path(filename).suffix.lower()
    if ext == ".docx":
        return parse_docx(path, attachments_dir=attachments_dir)
    if ext == ".xlsx":
        return parse_xlsx(path, attachments_dir=attachments_dir)
    if ext == ".pdf":
        return parse_pdf(path, attachments_dir=attachments_dir)
    raise ParseError(f"Неподдерживаемый тип файла: {ext}. Допустимы: {sorted(SUPPORTED_EXTENSIONS)}")
