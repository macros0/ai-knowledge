"""Compact LLM-only table views; canonical chunks and coordinates stay intact."""
from __future__ import annotations

import re

_SEPARATOR = re.compile(r":?-{3,}:?")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")


def _row_cells(row: str) -> list[str] | None:
    """Read a complete physical row, preserving cell syntax and whitespace."""
    text = row.rstrip("\r\n").strip()
    if "\n" in text or "\r" in text or not text.startswith("|") or not text.endswith("|"):
        return None
    boundaries = []
    code_run = 0
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and not code_run:
            i += 2
            continue
        if char == "`":
            end = i + 1
            while end < len(text) and text[end] == "`":
                end += 1
            run = end - i
            if not code_run:
                code_run = run
            elif run == code_run:
                code_run = 0
            i = end
            continue
        if char == "|" and not code_run:
            boundaries.append(i)
        i += 1
    if code_run or not boundaries or boundaries[0] != 0 or boundaries[-1] != len(text) - 1:
        return None
    return [text[left + 1:right] for left, right in zip(boundaries, boundaries[1:])]


def _retained_width(header: list[str], rows: list[list[str]]) -> int:
    if any(len(row) != len(header) for row in rows):
        return len(header)
    width = max((i + 1 for i, cell in enumerate(header) if cell.strip()), default=0)
    for row in rows:
        width = max(width, max((i + 1 for i, cell in enumerate(row) if cell.strip()), default=0))
    # An entirely empty table has no useful projection.
    return width or len(header)


def _trim_row(row: str, cells: list[str], width: int) -> str:
    stripped = row.strip()
    prefix = row[:len(row) - len(row.lstrip())]
    suffix = row[len(row.rstrip()):]
    return prefix + "|" + "|".join(cells[:width]) + "|" + suffix if stripped else row


def compact_table_columns(header: list[str], raw_rows: list[str]) -> tuple[list[str], list[str]]:
    """Remove only a contiguous tail empty in the header AND every source row.

    Prefix column indices remain unchanged. Ambiguous/ragged/multiline rows
    retain the original representation; input lists are never modified.
    """
    parsed = [_row_cells(row) for row in raw_rows]
    if any(row is None for row in parsed):
        return header, raw_rows
    width = _retained_width(header, parsed)
    if width == len(header):
        return header, raw_rows
    return header[:width], [_trim_row(raw, cells, width) for raw, cells in zip(raw_rows, parsed)]


def compact_markdown_tables(text: str) -> str:
    """Project complete, rectangular pipe tables outside code fences for LLM."""
    lines = text.splitlines(keepends=True)
    result = []
    i = 0
    fence_char = ""
    fence_size = 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            run = fence.group(1)
            if not fence_char:
                fence_char, fence_size = run[0], len(run)
            elif run[0] == fence_char and len(run) >= fence_size and not line[fence.end():].strip():
                fence_char = ""
            result.append(line)
            i += 1
            continue
        if fence_char or line.startswith(("    ", "\t")) or i + 1 >= len(lines):
            result.append(line)
            i += 1
            continue
        header = _row_cells(line)
        separator = _row_cells(lines[i + 1])
        if not header or not separator or len(header) != len(separator) or not all(_SEPARATOR.fullmatch(cell.strip()) for cell in separator):
            result.append(line)
            i += 1
            continue
        end = i + 2
        rows = []
        valid = True
        while end < len(lines) and lines[end].lstrip().startswith("|"):
            cells = _row_cells(lines[end])
            if cells is None or len(cells) != len(header):
                valid = False
            rows.append(cells)
            end += 1
        # An unseparated continuation may belong to a multiline cell.
        if end < len(lines) and lines[end].strip() and not lines[end].lstrip().startswith("#") and not _FENCE.match(lines[end]):
            valid = False
        if valid and rows:
            width = _retained_width(header, rows)
            if width < len(header):
                for raw, cells in zip(lines[i:end], [header, separator, *rows]):
                    result.append(_trim_row(raw, cells, width))
            else:
                result.extend(lines[i:end])
        else:
            result.extend(lines[i:end])
        i = end
    return "".join(result)
