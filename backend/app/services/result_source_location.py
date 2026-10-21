"""Carry retrieved passages to canonical document text independently of retrieval mode."""
from __future__ import annotations

from hashlib import sha256
import re
from types import SimpleNamespace

from sqlalchemy import select, tuple_

from app.db.models import Document, DocumentChunk, DocumentGenerationState
from app.db.session import session_scope
from app.models.schemas import ResultLocationRef, ResultSourceSpan, SourceLocationOut, SourceLocationSpanOut
from app.services.generation_store import lock_generation_read
from app.services.source_evidence import locate_unique_quote, resolve_source_spans
from app.services.source_location import get_source_location


def result_excerpt(text: str, maximum: int, *, query: str = '', groups=()) -> str:
    """Choose a literal preview; semantic-only results keep their retrieved head."""
    from app.services.glossary.matching import exact_excerpt, technical_span_boundary
    from app.services.sparse import tokenize
    preview = exact_excerpt(text, maximum, groups)
    if groups or len(text) <= maximum:
        return preview
    terms = set(tokenize(query))
    matches = [m for m in re.finditer(r'\w+', text) if m.group().lower() in terms]
    if not matches:
        return preview
    first = matches[0]
    start = max(0, first.start() - min(80, maximum // 3))
    while start < first.start() and not technical_span_boundary(text, start, len(text)):
        start += 1
    end = min(len(text), start + maximum)
    while end > start and not technical_span_boundary(text, 0, end):
        end -= 1
    return text[start:end]


def _digest(text):
    return sha256(text.encode('utf-8')).hexdigest()


def _projection_offsets(original, projected):
    """Map deterministic trailing-empty-column removal, preserving physical rows."""
    if original == projected:
        return list(range(len(original) + 1))
    raw_lines, lines = original.splitlines(keepends=True), projected.splitlines(keepends=True)
    if len(raw_lines) != len(lines):
        return None
    result, cursor = [], 0
    for raw, line in zip(raw_lines, lines):
        body = line.rstrip()
        ending = line[len(body):]
        if not raw.startswith(body) or (ending and not raw.endswith(ending)):
            return None
        result.extend(range(cursor, cursor + len(body)))
        result.extend(range(cursor + len(raw) - len(ending), cursor + len(raw)))
        cursor += len(raw)
    return [*result, len(original)]


def _retrieved_span(chunk, block, excerpt):
    """Use the origin of the displayed body, which may differ from its concept title."""
    evidence = block.get('_evidence') or {}
    if evidence.get('point_type') != 'chunk':
        return None
    descriptor = evidence.get('chunk_fragment')
    if descriptor:
        from app.services.chunk_index_projection import build_chunk_index_projection
        try:
            projection = build_chunk_index_projection(chunk.section_title or '', chunk.content, version=descriptor['version'])
            fragment = next((f for f in projection.fragments() if f.descriptor == descriptor), None)
            if fragment is None or fragment.field != 'body':
                return None  # A repeated title/header hint is not a body location.
            local = locate_unique_quote(fragment.text, excerpt)
            prefix = len(fragment.text) - len(fragment.covered_text)
            if local is None:
                return None
            start = max(local.start, prefix) - prefix + fragment.start
            end = min(local.end - prefix + fragment.start, fragment.end)
            mapping = _projection_offsets(chunk.content, projection.body)
            if mapping is not None and 0 <= start < end < len(mapping):
                return ResultSourceSpan(start=mapping[start], end=mapping[end])
        except (ValueError, TypeError, KeyError):
            return None
        return None
    content = block.get('content', '')
    local = locate_unique_quote(content, excerpt)
    if local is None:
        return None
    start = evidence.get('start', 0) + local.start
    end = evidence.get('start', 0) + local.end
    if chunk.content[start:end] == content[local.start:local.end]:
        return ResultSourceSpan(start=start, end=end)
    return None


def _literal_spans(content, excerpt):
    if not excerpt or len(excerpt.strip()) < 24:
        return []
    span = locate_unique_quote(content, excerpt)
    if span:
        return [ResultSourceSpan(start=span.start, end=span.end)]
    # A generated table row can retain verbatim cells separated by Markdown pipes.
    # The shared resolver accepts only substantial, unique retained evidence.
    return [ResultSourceSpan(start=s.start, end=s.end) for s in resolve_source_spans(content, excerpt)]


def build_result_locations(blocks: list[dict], excerpts: list[str]) -> list[ResultLocationRef | None]:
    """Batch only final visible text results; table navigation keeps its typed refs."""
    keys = {(b['doc_id'], b['chunk_index']) for b in blocks
            if b.get('doc_id') and b.get('chunk_index') is not None and not b.get('table_refs')}
    if not keys:
        return [None for _ in blocks]
    with session_scope() as session:
        lock_generation_read(session, sorted({key[0] for key in keys}))
        chunks = {(c.doc_id, c.chunk_index): SimpleNamespace(**c._mapping) for c in session.execute(select(
            DocumentChunk.doc_id, DocumentChunk.chunk_index, DocumentChunk.source_id,
            DocumentChunk.content, DocumentChunk.section_title).where(
                tuple_(DocumentChunk.doc_id, DocumentChunk.chunk_index).in_(sorted(keys))))}
        generations = dict(session.execute(select(DocumentGenerationState.doc_id,
            DocumentGenerationState.active_generation_id).where(DocumentGenerationState.doc_id.in_(sorted({k[0] for k in keys})))).all())
    refs = []
    for block, excerpt in zip(blocks, excerpts, strict=True):
        chunk = chunks.get((block.get('doc_id'), block.get('chunk_index')))
        if chunk is None or block.get('table_refs'):
            refs.append(None)
            continue
        if block.get('source_id') is not None and block['source_id'] != chunk.source_id:
            refs.append(None)
            continue
        span = _retrieved_span(chunk, block, excerpt)
        evidence = block.get('_evidence') or {}
        # Projected headers must not get a second chance as a literal body match.
        spans = ([span] if span else [] if evidence.get('chunk_fragment') else _literal_spans(chunk.content, excerpt))
        generation = evidence.get('generation_id', block.get('generation_id'))
        if generation != generations.get(chunk.doc_id):
            spans = []
        refs.append(ResultLocationRef(
            generation_id=generation, source_id=chunk.source_id,
            chunk_index=chunk.chunk_index, source_slug=block.get('source_slug'),
            basis='match' if spans else 'concept' if block.get('source_slug') else 'chunk',
            chunk_hash=_digest(chunk.content), spans=spans[:3],
        ))
    return refs


def resolve_result_location(doc_id: str, ref: ResultLocationRef) -> SourceLocationOut:
    with session_scope() as session:
        lock_generation_read(session, [doc_id])
        document = session.get(Document, doc_id)
        if document is None or document.deleted_at is not None:
            return SourceLocationOut(status='unavailable', reason='missing')
        generation = session.scalar(select(DocumentGenerationState.active_generation_id).where(DocumentGenerationState.doc_id == doc_id))
        chunk = session.scalar(select(DocumentChunk).where(DocumentChunk.doc_id == doc_id,
                                                           DocumentChunk.chunk_index == ref.chunk_index))
        if chunk is None or (ref.source_id is not None and ref.source_id != chunk.source_id):
            return SourceLocationOut(status='unavailable', reason='missing')
        base = {'chunk_index': chunk.chunk_index, 'source_id': chunk.source_id, 'basis': 'chunk',
                'chunk_hash': _digest(chunk.content), 'generation_id': generation}
        if ((ref.basis != 'legacy' or ref.generation_id is not None) and ref.generation_id != generation
                or ref.chunk_hash is not None and ref.chunk_hash != _digest(chunk.content)):
            return SourceLocationOut(status='chunk', reason='changed', **base)
        if ref.basis == 'legacy':
            if ref.source_id is None:
                return SourceLocationOut(status='unavailable', reason='missing')
            spans = _literal_spans(chunk.content, ref.excerpt or '')
        else:
            spans = [s for s in ref.spans if 0 <= s.start < s.end <= len(chunk.content)] if ref.chunk_hash else []
        if spans:
            return SourceLocationOut(status='recovered' if ref.basis == 'legacy' else 'exact',
                **{**base, 'basis': 'match'}, spans=[SourceLocationSpanOut(start=s.start, end=s.end,
                quote=chunk.content[s.start:s.end][:240]) for s in spans])
        if ref.source_slug and ref.basis in {'concept', 'legacy'}:
            # The chunk and generation stay locked while validating concept evidence.
            location = get_source_location(doc_id, ref.source_slug, session=session)
            if location and location.chunk_index == chunk.chunk_index and location.source_id == chunk.source_id:
                return location.model_copy(update={'basis': 'concept'})
        return SourceLocationOut(status='chunk', **base)
