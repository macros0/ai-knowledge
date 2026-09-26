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
    return _locate_retained_excerpts(text, content)


def resolve_paragraph_span(text: str, title: str, content: str) -> SourceSpan | None:
    """Return a possible navigation target for a uniquely matched paragraph.

    Use only multi-paragraph inputs: a one-paragraph non-mail document is too
    broad to suggest a location. This is not verified quote evidence.
    """
    paragraphs = _mail_paragraph_ranges(text)
    if len(paragraphs) < 2:
        return None
    return _unique_paragraph_span(text, paragraphs, title, content)


def mail_summary_items(content: str) -> list[str]:
    """Separate a multi-item summary's claims from its introductory metadata."""
    content = re.split(
        r"(?im)^\s*(?:#{1,6}\s+)?\*{0,2}Source Quotes(?::\*{0,2}|\*{0,2}:?)\s*$", content,
    )[0]
    items = re.findall(r"(?ms)^[-*+]\s+(.+?)(?=^[-*+]\s+|\Z)", content)
    return items if len(items) >= 2 else []


def resolve_mail_source_spans(
    text: str, content: str, quotes: list[str] | None = None,
) -> list[SourceSpan]:
    """For list summaries, require evidence about an item, not just the subject.

    This conservative lexical admission check is not a semantic verifier.
    Accepted quotes still must occur uniquely and verbatim in canonical text.
    If none qualify, recover literal excerpts from individual claims only.
    """
    items = mail_summary_items(content)
    if not items:
        return resolve_source_spans(text, content, quotes)
    preamble = re.split(r"(?m)^[-*+]\s+", content, maxsplit=1)[0]
    introductory_terms = _evidence_tokens(preamble)
    supported_quotes = []
    for quote in (quotes or [])[:3] + embedded_source_quotes(content):
        quote_terms = _evidence_tokens(quote) - introductory_terms
        for item in items:
            if _has_negation(quote) != _has_negation(item):
                continue
            shared = quote_terms & _evidence_tokens(item)
            if len(shared) >= 2 or any(any(char.isdigit() for char in term) for term in shared):
                supported_quotes.append(quote)
                break
    if supported_quotes:
        spans = resolve_source_spans(text, "", supported_quotes)
        if spans:
            return spans
    spans = {(span.start, span.end): span for item in items
             for span in resolve_source_spans(text, item)}
    return sorted(spans.values(), key=lambda span: (span.start, span.end))[:3]


def resolve_mail_paragraph_span(text: str, title: str, content: str) -> SourceSpan | None:
    """Return a conservative paragraph anchor for a mail concept without a quote.

    This is only a possible navigation target, never verified quote evidence.
    A sole body paragraph still needs support from the concept body, and
    conflicting negation rules out the guess.
    """
    paragraphs = _mail_paragraph_ranges(text)
    if len(paragraphs) == 1:
        start, end = paragraphs[0]
        if not _paragraph_match_score(text[start:end], title, content):
            return None
        return span_from_offsets(text, start, end)
    if len(paragraphs) < 2:
        return None
    return _unique_paragraph_span(text, paragraphs, title, content)


def _unique_paragraph_span(
    text: str, paragraphs: list[tuple[int, int]], title: str, content: str,
) -> SourceSpan | None:
    scored = [
        (_paragraph_match_score(text[start:end], title, content), index)
        for index, (start, end) in enumerate(paragraphs)
    ]

    best_score, best_index = max(scored)
    second_score = max(score for score, index in scored if index != best_index)
    if not best_score or best_score <= second_score:
        return None
    start, end = paragraphs[best_index]
    return span_from_offsets(text, start, end)


def _mail_paragraph_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for match in re.finditer(r"(?:^|\n[ \t]*\n)(.*?)(?=\n[ \t]*\n|\Z)", text, flags=re.DOTALL):
        raw = match.group(1)
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        start = match.start(1) + len(raw) - len(raw.lstrip())
        end = start + len(stripped)
        ranges.append((start, end))
    return ranges


def is_whole_paragraph_span(text: str, start: int, end: int) -> bool:
    """Recognize old lexical mail spans that were stored as exact ranges."""
    return (start, end) in _mail_paragraph_ranges(text)


def _paragraph_match_score(paragraph: str, title: str, content: str) -> int:
    """Require concept-body evidence; title can only break a supported tie."""
    if _has_negation(paragraph) != _has_negation(content):
        return 0
    source_terms = _evidence_tokens(paragraph)
    shared_body = source_terms & _evidence_tokens(content)
    identifiers = {term for term in shared_body if any(char.isdigit() for char in term)}
    if len(shared_body) < 2 and not identifiers:
        return 0
    return 2 * len(shared_body) + len(source_terms & _evidence_tokens(title))


def _has_negation(value: str) -> bool:
    return bool(re.search(r"(?<!\w)(?:не|нет|без|not|no)(?!\w)", value, flags=re.IGNORECASE))


def _evidence_tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"\w+", value, flags=re.UNICODE)
        if (len(token) >= 4 or any(char.isdigit() for char in token))
        and token.casefold() not in _EVIDENCE_STOPWORDS
    }


_EVIDENCE_STOPWORDS = {
    "этот", "этого", "этой", "этим", "этими", "для", "при", "как",
    "что", "или", "also", "with", "from", "that", "this", "they",
    "about", "into", "using", "used", "настройте", "используется",
    "сообщение", "сообщения", "сообщении", "сообщений", "письмо", "письма",
    "документ", "документа", "document", "message", "email",
}


def _locate_retained_excerpts(text: str, content: str) -> list[SourceSpan]:
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
    # Each candidate was matched through locate_unique_quote(), so the same
    # quoted words at several source locations were already rejected.  Several
    # *different* equal-length fragments are valid evidence for a concept that
    # compresses intervening prose.  Keep non-overlapping positions in document
    # order; overlapping token windows are alternate descriptions of one fact.
    result: list[SourceSpan] = []
    for span in sorted(best.values(), key=lambda item: (item.start, item.end)):
        if result and span.start < result[-1].end:
            continue
        result.append(span)
        if len(result) == 3:
            break
    return result


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
