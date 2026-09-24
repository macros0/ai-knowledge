"""Read and validate concept locations against canonical document chunk text."""

from __future__ import annotations

import hashlib

from sqlalchemy import select

from app.db.models import DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.models.schemas import DocumentTextChunkOut, SourceLocationOut, SourceLocationSpanOut
from app.services.source_evidence import locate_unique_quote


_MIN_LEGACY_QUOTE_CHARS = 24


def get_source_location(doc_id: str, slug: str) -> SourceLocationOut | None:
    with session_scope() as session:
        concept = session.execute(
            select(
                OkfConcept.chunk_index,
                OkfConcept.source_spans,
                OkfConcept.content,
            ).where(
                OkfConcept.doc_id == doc_id,
                OkfConcept.slug == slug,
            )
        ).first()
        if concept is None:
            return None
        chunk_index, stored_spans, concept_content = concept
        if chunk_index is None:
            return SourceLocationOut(status="unavailable")
        chunk = session.execute(
            select(DocumentChunk.content).where(
                DocumentChunk.doc_id == doc_id,
                DocumentChunk.chunk_index == chunk_index,
            )
        ).first()
        if chunk is None:
            return SourceLocationOut(status="unavailable", chunk_index=chunk_index)
        content = chunk[0] or ""
        if not content:
            return SourceLocationOut(status="unavailable", chunk_index=chunk_index)

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    valid: list[SourceLocationSpanOut] = []
    seen: set[tuple[int, int]] = set()
    for span in stored_spans or []:
        if not isinstance(span, dict):
            continue
        start, end = span.get("start"), span.get("end")
        if (
            span.get("chunk_hash") != digest
            or isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or not (0 <= start < end <= len(content))
            or (start, end) in seen
        ):
            continue
        seen.add((start, end))
        valid.append(
            SourceLocationSpanOut(
                start=start,
                end=end,
                quote=content[start:end][:240],
            )
        )
    recovered = False
    if not valid and stored_spans is None:
        legacy_quote = (concept_content or "").strip()
        if len(legacy_quote) >= _MIN_LEGACY_QUOTE_CHARS:
            recovered = locate_unique_quote(content, legacy_quote)
            if recovered is not None:
                valid.append(
                    SourceLocationSpanOut(
                        start=recovered.start,
                        end=recovered.end,
                        quote=content[recovered.start : recovered.end][:240],
                    )
                )
                recovered = True
    valid.sort(key=lambda item: (item.start, item.end))
    return SourceLocationOut(
        status="recovered" if recovered else "exact" if valid else "chunk",
        chunk_index=chunk_index,
        spans=valid,
    )


def get_document_text_chunks(doc_id: str) -> list[DocumentTextChunkOut]:
    with session_scope() as session:
        rows = session.execute(
            select(DocumentChunk.chunk_index, DocumentChunk.content)
            .where(DocumentChunk.doc_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
    return [DocumentTextChunkOut(chunk_index=index, content=content or "") for index, content in rows]
