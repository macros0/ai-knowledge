"""Rebuild search projections from a consistent published SQL snapshot.

Full collection rebuilds are offline maintenance: stop application writers first.
Per-document locks also prevent a concurrent publication from mixing generations
inside one document's concept/chunk payloads.
"""
from sqlalchemy import or_, select

from app.db.models import Development, Document, DocumentChunk, DocumentGenerationState, OkfConcept
from app.db.session import session_scope
from app.models.schemas import OkfDocument
from app.services.generation_store import lock_generation_read
from app.services.source_store import fetch_source_trees
from app.services.mail_scope import build_record_mail_scopes


def published_document_ids() -> list[str]:
    """Include last published content while a newer attempt is paused or failed."""
    with session_scope() as session:
        return list(session.scalars(
            select(Document.id)
            .outerjoin(DocumentGenerationState, DocumentGenerationState.doc_id == Document.id)
            .where(Document.deleted_at.is_(None), or_(
                DocumentGenerationState.active_generation_id.is_not(None),
                Document.status == "done",
                select(OkfConcept.doc_id).where(OkfConcept.doc_id == Document.id).exists(),
                select(DocumentChunk.doc_id).where(DocumentChunk.doc_id == Document.id).exists(),
            ))
            .order_by(Document.id)
        ))


def reindex_published_document(doc_id, settings, store, embedder, *, concepts_only=False) -> dict:
    """Index exact canonical identities, including non-contiguous chunk indices."""
    counts = {"docs": 0, "concepts": 0, "chunks": 0}
    with session_scope() as session:
        generations = lock_generation_read(session, [doc_id])
        document = session.get(Document, doc_id)
        if document is None or document.deleted_at is not None:
            return counts
        generation_id = generations.get(doc_id)
        global_tags = [item.tag_rel.canonical_text for item in document.tags_rel]
        dev = session.get(Development, document.development_id) if document.development_id else None
        dev_tags = [dev.number, dev.name, *([dev.module] if dev.module else [])] if dev else []
        from app.services.chunk_projection_maintenance import generation_projection_version, backfill_projection_chunks
        version = generation_projection_version(session, doc_id, generation_id)
        sources = fetch_source_trees(session, {doc_id})[doc_id]
        concepts = session.query(OkfConcept).filter_by(doc_id=doc_id).order_by(OkfConcept.slug).all()
        chunks = (session.query(DocumentChunk).filter_by(doc_id=doc_id).order_by(DocumentChunk.chunk_index).all()
                  if version is None else [])
        chunk_records = ([dict(source_id=row.source_id,chunk_index=row.chunk_index) for row in chunks]
            if version is None else [dict(source_id=source,chunk_index=index) for source,index in session.execute(
                select(DocumentChunk.source_id,DocumentChunk.chunk_index).where(DocumentChunk.doc_id==doc_id))])
        if not generation_id and document.status != "done" and not concepts and not chunks:
            return counts
        concept_scopes, chunk_scopes = build_record_mail_scopes(
            sources,
            [dict(source_id=row.source_id, chunk_index=row.chunk_index) for row in concepts],
            chunk_records,
        )
        for offset in range(0, len(concepts), 64):
            batch = concepts[offset:offset + 64]
            docs = [OkfDocument(
                filepath=str(settings.okf_dir / doc_id / f"{row.slug}.md"),
                metadata={
                    "title": row.title, "type": row.type, "tags": list(row.tags or []),
                    "relations": list(row.relations or []), "chunk_index": row.chunk_index,
                    "source_id": row.source_id,
                }, content=row.content or "", markdown="",
            ) for row in batch]
            vectors = embedder.embed_texts([
                f"{item.metadata.get('title', '')}\n{item.content[:settings.okf_max_concept_chars]}"
                for item in docs
            ])
            store.index_concepts(doc_id, docs, vectors, dev_tags=dev_tags,
                                 source_locale=document.source_locale, generation_id=generation_id,
                                 mail_scopes=concept_scopes[offset:offset + len(batch)])
            counts["concepts"] += len(docs)
        if not concepts_only and settings.search_index_chunks_enabled and version is not None:
            backfill_projection_chunks(session,store,embedder,doc_id,generation_id,document.filename,
                global_tags,dev_tags,document.source_locale,sources,64,force=True)
            counts['chunks']=len(chunk_records)
        if not concepts_only and settings.search_index_chunks_enabled and chunks:
            texts = [row.content or "" for row in chunks]
            titles = [row.section_title or "" for row in chunks]
            vectors = embedder.embed_texts([
                f"{title}\n{text[:settings.okf_max_chunk_index_chars]}" if title
                else text[:settings.okf_max_chunk_index_chars]
                for title, text in zip(titles, texts)
            ])
            store.index_chunks(
                doc_id, document.filename, texts, global_tags, vectors,
                section_titles=titles, dev_tags=dev_tags, source_locale=document.source_locale,
                source_ids=[row.source_id for row in chunks], generation_id=generation_id,
                chunk_indices=[row.chunk_index for row in chunks], mail_scopes=chunk_scopes,
            )
            counts["chunks"] = len(chunks)
        counts["docs"] = 1
    return counts
