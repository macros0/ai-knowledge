"""Create and validate character ranges pointing back into a source chunk."""

from __future__ import annotations

import hashlib
import re

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
    """Match unique evidence, tolerating only whitespace from PDF line wrapping.

    Search the original text so offsets and hashes remain canonical. Repeated
    matches (including overlapping or differently wrapped ones) are ambiguous.
    """
    words = quote.split()
    if not words:
        return None
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words))
    match = pattern.search(text)
    if match is None or pattern.search(text, match.start() + 1) is not None:
        return None
    return span_from_offsets(text, match.start(), match.end())


def embedded_source_quotes(content: str) -> list[str]:
    """Read a misplaced LLM Source Quotes list, never arbitrary quoted prose.

    These are candidates only: callers must validate them against the chunk.
    Stop at the next non-list paragraph/heading and keep the same three-quote
    limit as structured source_quotes.
    """
    quotes: list[str] = []
    in_section = False
    for line in content.splitlines():
        line = line.strip()
        if not in_section:
            heading = re.sub(r"^#{1,6}\s+", "", line).replace("**", "").rstrip(":").strip()
            in_section = heading.casefold() == "source quotes"
            continue
        if not line:
            continue
        item = re.fullmatch(r'[-*+]\s+"(.+)"', line)
        if item is None:
            break
        quote = item.group(1)
        if quote not in quotes:
            quotes.append(quote)
        if len(quotes) == 3:
            break
    return quotes


def resolve_source_spans(
    text: str, content: str, quotes: list[str] | None = None,
) -> list[SourceSpan]:
    """Resolve navigation evidence for both generation and existing concepts.

    Prefer supplied quotes, then the complete concept, then a substantial
    verbatim excerpt retained in a paraphrase. Never use semantic similarity
    or expand a match to a whole paragraph: only verified characters are marked.
    """
    spans: list[SourceSpan] = []
    seen: set[tuple[int, int]] = set()
    for quote in (quotes or [])[:3] + embedded_source_quotes(content):
        span = locate_unique_quote(text, quote)
        if span and (span.start, span.end) not in seen:
            spans.append(span)
            seen.add((span.start, span.end))
    if spans:
        return spans[:3]
    if len(content.strip()) >= 24:
        span = locate_unique_quote(text, content)
        if span:
            return [span]
    span = _locate_retained_excerpt(text, content)
    return [span] if span else []


def _locate_retained_excerpt(text: str, content: str) -> SourceSpan | None:
    # Keep identifiers (P0002-PERIOD, V_T5UX9) intact. Sentence punctuation
    # outside the excerpt may change; punctuation inside it still must match.
    token_re = r"\w+(?:[-']\w+)*"
    source_tokens = list(re.finditer(token_re, text))
    concept_tokens = list(re.finditer(token_re, content))
    # A shared identifier or generic short phrase is not sufficient evidence.
    # Require eight consecutive words and a substantial part of the concept.
    source_positions: dict[str, list[int]] = {}
    for index, token in enumerate(source_tokens):
        source_positions.setdefault(token.group(), []).append(index)
    previous: dict[int, int] = {}
    candidates: dict[tuple[int, int], str] = {}
    for index, token in enumerate(concept_tokens):
        current = {}
        for source_index in source_positions.get(token.group(), []):
            size = previous.get(source_index - 1, 0) + 1
            current[source_index] = size
            if size < 8 or size < 0.4 * len(concept_tokens):
                continue
            start = concept_tokens[index - size + 1].start()
            quote = content[start:token.end()]
            if len(quote) >= 48:
                candidates[(index - size + 1, size)] = quote
        previous = current

    # Consider all maximal matches, not a greedy alignment: an earlier generic
    # prefix must not hide a later specific phrase of the same word length.
    best_size = 0
    best: dict[tuple[int, int], SourceSpan] = {}
    for (_, size), quote in sorted(candidates.items(), key=lambda item: -item[0][1]):
        if best_size and size < best_size:
            break
        span = locate_unique_quote(text, quote)
        if span:
            best[(span.start, span.end)] = span
            best_size = size
    # Equal-strength fragments in different locations are not a unique anchor.
    return next(iter(best.values())) if len(best) == 1 else None


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
