"""Read and validate concept locations against canonical document chunk text."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select

from app.db.models import Document, DocumentChunk, DocumentSource, OkfConcept
from app.db.session import session_scope
from app.models.schemas import DocumentTextChunkOut, SourceLocationOut, SourceLocationSpanOut
from app.services.generation_store import lock_generation_read
from app.services.source_evidence import (
    is_whole_paragraph_span,
    mail_summary_items,
    resolve_mail_paragraph_span,
    resolve_mail_source_spans,
    resolve_paragraph_span,
    resolve_source_spans,
)


def get_source_location(doc_id: str, slug: str) -> SourceLocationOut | None:
    with session_scope() as session:
        lock_generation_read(session, [doc_id])
        concept = session.execute(
            select(
                OkfConcept.chunk_index,
                OkfConcept.source_spans,
                OkfConcept.title,
                OkfConcept.content,
            ).where(
                OkfConcept.doc_id == doc_id,
                OkfConcept.slug == slug,
            )
        ).first()
        if concept is None:
            return None
        chunk_index, stored_spans, concept_title, concept_content = concept
        if chunk_index is None:
            return SourceLocationOut(status="unavailable")
        chunk = session.execute(
            select(DocumentChunk.content, DocumentChunk.source_id).where(
                DocumentChunk.doc_id == doc_id,
                DocumentChunk.chunk_index == chunk_index,
            )
        ).first()
        if chunk is None:
            return SourceLocationOut(status="unavailable", chunk_index=chunk_index)
        content, source_id = chunk
        content = content or ""
        if not content:
            return SourceLocationOut(status="unavailable", chunk_index=chunk_index)
        filename = session.scalar(select(Document.filename).where(Document.id == doc_id)) or ""
        source = session.execute(
            select(DocumentSource.kind, DocumentSource.metadata_json).where(
                DocumentSource.doc_id == doc_id,
                DocumentSource.source_id == source_id,
            )
        ).first() if source_id else None

    source_metadata = source.metadata_json if source and isinstance(source.metadata_json, dict) else {}
    is_mail = (
        (source_id == "root" and Path(filename).suffix.lower() in {".eml", ".msg"})
        or bool(source and source.kind == "mail")
        or bool(source_metadata.get("mail"))
    )

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
    inferred = False
    summary = is_mail and bool(mail_summary_items(concept_content or ""))
    if summary and valid:
        previous_positions = {(span.start, span.end) for span in valid}
        evidence = resolve_mail_source_spans(
            content, concept_content or "", [content[span.start:span.end] for span in valid],
        )
        valid = [SourceLocationSpanOut(start=span.start, end=span.end,
                                       quote=content[span.start:span.end][:240]) for span in evidence]
        recovered = any((span.start, span.end) not in previous_positions for span in evidence)
    elif is_mail and valid:
        verbatim = {(span.start, span.end) for span in resolve_source_spans(content, concept_content or "")}
        guess = resolve_mail_paragraph_span(content, concept_title or "", concept_content or "")
        kept: list[SourceLocationSpanOut] = []
        for span in valid:
            position = (span.start, span.end)
            if is_whole_paragraph_span(content, *position) and position not in verbatim:
                # Old mail generations stored lexical whole-paragraph guesses
                # as exact spans. Valid offsets alone do not prove the claim.
                if guess and position == (guess.start, guess.end):
                    inferred = True
                    kept.append(span)
                continue
            kept.append(span)
        valid = kept
    if not valid and stored_spans is None:
        evidence_resolver = resolve_mail_source_spans if is_mail else resolve_source_spans
        for span in evidence_resolver(content, concept_content or ""):
            valid.append(
                SourceLocationSpanOut(
                    start=span.start,
                    end=span.end,
                    quote=content[span.start : span.end][:240],
                )
            )
            recovered = True
        if not valid and not summary:
            resolver = resolve_mail_paragraph_span if is_mail else resolve_paragraph_span
            paragraph_span = resolver(content, concept_title or "", concept_content or "")
            if paragraph_span:
                valid.append(
                    SourceLocationSpanOut(
                        start=paragraph_span.start,
                        end=paragraph_span.end,
                        quote=content[paragraph_span.start : paragraph_span.end][:240],
                    )
                )
                inferred = True
    valid.sort(key=lambda item: (item.start, item.end))
    return SourceLocationOut(
        status="inferred" if inferred else "recovered" if recovered else "exact" if valid else "chunk",
        chunk_index=chunk_index,
        source_id=source_id,
        spans=valid,
    )


def get_document_text_chunks(doc_id: str) -> list[DocumentTextChunkOut]:
    with session_scope() as session:
        rows = session.execute(
            select(DocumentChunk.chunk_index, DocumentChunk.source_id, DocumentChunk.content)
            .where(DocumentChunk.doc_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
    return [DocumentTextChunkOut(chunk_index=index, source_id=source_id, content=content or "") for index, source_id, content in rows]
