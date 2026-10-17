"""Bounded legacy chunk restoration, revalidating publication before writes."""
import logging
from sqlalchemy import select
from qdrant_client import models as qm
from app.db.models import Document, DocumentChunk
from app.db.session import session_scope
from app.services.generation_store import lock_generation_read
from app.services.bounded_sql_text import capped_text_expression, decode_capped_text
from app.services.development_registry import get_development_registry
from app.services.mail_scope import build_mail_scope_map, MAIL_SCOPE_VERSION
from app.services.source_store import fetch_source_trees, load_source_display_rows
from app.services.chunk_projection_maintenance import generation_projection_version, backfill_projection_chunks

logger = logging.getLogger('app.services.vector_store')


def backfill_legacy_chunks(store, embedder, existing_ids, batch_size):
    from app.services.vector_store import chunk_point_id, _sparse_text, to_sparse_vector, SPARSE_VECTOR_NAME, CHUNK_POINT_TYPE
    if type(batch_size) is not int or not 1 <= batch_size <= 256:
        raise ValueError('Invalid maintenance batch size')
    dev_reg = get_development_registry()
    cap = store.settings.okf_max_chunk_index_chars
    indexed = projection_count = attempted = failed = batches = 0
    failed_docs = set()
    last_doc = None
    while True:
        with session_scope() as session:
            query = select(Document.id).where(Document.deleted_at.is_(None)).order_by(Document.id).limit(1)
            if last_doc is not None:
                query = query.where(Document.id > last_doc)
            doc_id = session.scalar(query)
        if doc_id is None:
            break
        last_doc = doc_id
        with session_scope() as session:
            generation_id = lock_generation_read(session, [doc_id]).get(doc_id)
            doc = session.get(Document, doc_id)
            if doc is None or doc.deleted_at is not None:
                continue
            sources = fetch_source_trees(session, {doc_id},
                rows=load_source_display_rows(session, {doc_id}))[doc_id]
            tags = [t.tag_rel.canonical_text for t in (doc.tags_rel or [])]
            dev_tags = dev_reg.dev_tags(doc.development_id) if doc.development_id else []
            locale = doc.source_locale
            if generation_projection_version(session, doc_id, generation_id) is not None:
                projection_count += backfill_projection_chunks(session, store, embedder, doc_id, generation_id,
                    doc.filename, tags, dev_tags, locale, sources, batch_size)
                continue
            scopes = build_mail_scope_map(sources)
        # Never retain the full corpus or a SQL transaction across legacy embedding.
        del sources
        last_chunk = None
        while True:
            with session_scope() as session:
                current = lock_generation_read(session, [doc_id]).get(doc_id)
                doc = session.get(Document, doc_id)
                if doc is None or doc.deleted_at is not None or current != generation_id:
                    break
                query = select(DocumentChunk.chunk_index, DocumentChunk.source_id, DocumentChunk.section_title,
                    capped_text_expression(session, DocumentChunk.content, cap)).where(DocumentChunk.doc_id == doc_id).order_by(
                        DocumentChunk.chunk_index).limit(batch_size)
                if last_chunk is not None:
                    query = query.where(DocumentChunk.chunk_index > last_chunk)
                rows = session.execute(query).all()
            if not rows:
                break
            last_chunk = rows[-1][0]
            missing = [(chunk_point_id(doc_id, ci, generation_id=generation_id), ci, sid, title or '', decode_capped_text(text, cap) or '')
                       for ci, sid, title, text in rows
                       if chunk_point_id(doc_id, ci, generation_id=generation_id) not in existing_ids]
            if not missing:
                continue
            vectors = embedder.embed_texts([title + '\n' + text if title else text
                                           for _, _, _, title, text in missing])
            if len(vectors) != len(missing):
                raise ValueError('Embedding count does not match the chunk batch')
            points = [qm.PointStruct(id=identity, vector={'': vector,
                SPARSE_VECTOR_NAME: to_sparse_vector(_sparse_text(title, text))}, payload={
                    'point_type': CHUNK_POINT_TYPE, 'generation_id': generation_id, 'doc_id': doc_id,
                    'chunk_index': ci, 'tags': tags, 'dev_tags': dev_tags, 'source_locale': locale,
                    'source_id': sid, 'mail_scope': scopes.get(sid, 'unknown'),
                    'mail_scope_version': MAIL_SCOPE_VERSION})
                for (identity, ci, sid, title, text), vector in zip(missing, vectors)]
            with session_scope() as session:
                current = lock_generation_read(session, [doc_id]).get(doc_id)
                doc = session.get(Document, doc_id)
                if doc is None or doc.deleted_at is not None or current != generation_id:
                    break
                batches += 1
                attempted += len(points)
                try:
                    store.client.upsert(collection_name=store.collection, points=points)
                    indexed += len(points)
                except Exception as exc:
                    failed += 1
                    failed_docs.add(doc_id)
                    logger.error('Backfill чанков: батч %d упал (%d точек): %s', batches, len(points), exc)
            del rows, missing, vectors, points
    if failed:
        logger.error('Backfill чанков завершён ЧАСТИЧНО: батчи ок %d/%d, проиндексировано %d из %d точек; '
                     'неиндексированные документы: %s', batches-failed, batches, indexed, attempted,
                     ', '.join(sorted(failed_docs)))
    elif indexed:
        logger.info('Backfill чанков: %d/%d батчей, %d точек', batches, batches, indexed)
    return indexed + projection_count
