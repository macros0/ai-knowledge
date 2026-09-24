"""Create and validate character ranges pointing back into a source chunk."""

from __future__ import annotations

import hashlib

from app.models.schemas import SourceSpan


def chunk_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def span_from_offsets(text: str, start: int, end: int) -> SourceSpan | None:
    if not (0 <= start < end <= len(text)):
        return None
    return SourceSpan(start=start, end=end, chunk_hash=chunk_digest(text))


def span_for_lines(text: str, start_line: int, end_line: int) -> SourceSpan | None:
    """Return the range for the half-open line interval [start_line, end_line)."""
    line_offsets = [0]
    for line in text.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))
    if not (0 <= start_line < end_line < len(line_offsets)):
        return None
    return span_from_offsets(text, line_offsets[start_line], line_offsets[end_line])


def locate_unique_quote(text: str, quote: str) -> SourceSpan | None:
    """Only return exact evidence when its literal quote appears once."""
    if not quote:
        return None
    start = text.find(quote)
    if start < 0 or text.find(quote, start + 1) >= 0:
        return None
    return span_from_offsets(text, start, start + len(quote))


def line_offset(text: str, line_index: int) -> int | None:
    """Return a code-point offset for a source line, including CRLF correctly."""
    if line_index < 0:
        return None
    offset = 0
    for index, line in enumerate(text.splitlines(keepends=True)):
        if index == line_index:
            return offset
        offset += len(line)
    return len(text) if line_index == len(text.splitlines(keepends=True)) else None
