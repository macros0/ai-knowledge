"""Разбор XLSX: листы → Markdown-таблицы + встроенные объекты (xl/embeddings)."""
import zipfile
import zlib
from pathlib import Path

from openpyxl import load_workbook

from docparser.archive_guard import validate_member, validate_zip
from docparser.blocks import Block
from docparser.embedded import (
    MAX_ATTACHMENT_PAYLOAD,
    AttachmentBudget,
    attachment_count_marker,
    attachment_parse_marker,
    attachment_size_marker,
    process_embedded,
)


def parse_xlsx(
    path: str | Path,
    attachments_dir: str | Path | None = None,
    depth: int = 0,
    budget=None,
    context=None,
    source_id: str = "root",
) -> list[Block]:
    # Validate the central directory before openpyxl starts reading XML parts.
    validate_zip(path)
    budget = budget or AttachmentBudget()
    wb = load_workbook(str(path), read_only=True, data_only=True)
    blocks: list[Block] = []
    try:
        for ws in wb.worksheets:
            blocks.append(Block("heading", f"Таблица: {ws.title}", level=1))
            md = _sheet_to_markdown(ws)
            if md:
                blocks.append(Block("table", md))
    finally:
        wb.close()

    with zipfile.ZipFile(str(path)) as archive:
        members = (info for info in archive.infolist()
                   if info.filename.startswith("xl/embeddings/") and not info.is_dir())
        for index, member in enumerate(members):
            if not budget.reserve_node():
                blocks.append(attachment_count_marker(context, source_id))
                break
            if member.file_size > min(MAX_ATTACHMENT_PAYLOAD, budget.remaining):
                blocks.append(attachment_size_marker(Path(member.filename).name, context, source_id))
                continue
            validate_member(member)
            try:
                payload = archive.read(member)
            except (zipfile.BadZipFile, EOFError, zlib.error):
                blocks.append(attachment_parse_marker(Path(member.filename).name, context, source_id))
                continue
            blocks.extend(process_embedded(
                payload, Path(member.filename).name, "", "", attachments_dir, index,
                depth=depth + 1, budget=budget, context=context, parent_source_id=source_id,
                _node_reserved=True,
            ))

    return blocks


def _sheet_to_markdown(ws) -> str:
    rows = []
    cell_count = 0
    max_rows = 250_000
    max_cells = 5_000_000
    for row in ws.iter_rows(values_only=True):
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        if len(rows) >= max_rows or cell_count + len(row) > max_cells:
            raise ValueError("spreadsheet row/cell safety limit exceeded")
        cell_count += len(row)
        rows.append(["" if v is None else str(v).replace("\n", "<br>") for v in row])
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)
