"""Offline restoration of an active contract; never change its version/IDs.

Callers retain the generation read lock throughout Qdrant writes. Canonical
replay verifies one chunk at a time; only one bounded text/vector batch lives
at a time. Missing or damaged SQL projections require a new repair candidate.
"""
from itertools import islice
from sqlalchemy import select, func
from app.db.models import DocumentGeneration, DocumentChunk, DocumentChunkProjection, DocumentChunkFragment
from app.services.chunk_index_projection import (CURRENT_CHUNK_PROJECTION_VERSION,
    build_chunk_index_projection, chunk_fragment_point_id, chunk_fragment_group_id, chunk_fragment_point_id_from_descriptor, digest)
from app.services.generation_artifacts import GenerationIntegrityError
from app.services.mail_scope import build_mail_scope_map


def generation_projection_version(session, doc_id, generation_id):
    generation = session.get(DocumentGeneration, generation_id) if generation_id else None
    if generation_id and (generation is None or generation.doc_id != doc_id or generation.phase != 'active'):
        raise GenerationIntegrityError()
    version = generation.chunk_projection_version if generation else None
    if version is not None and (type(version) is not int or version != CURRENT_CHUNK_PROJECTION_VERSION):
        raise GenerationIntegrityError()
    return version


def verified_chunk_parts(session, doc_id, generation_id, row, batch_size):
    """Stream canonical-bound representations, validating stored batches exactly."""
    if type(batch_size) is not int or not 1 <= batch_size <= 256:
        raise ValueError('Invalid maintenance batch size')
    title, content = row.section_title or '', row.content or ''
    projection = build_chunk_index_projection(title, content, version=CURRENT_CHUNK_PROJECTION_VERSION)
    parent = session.get(DocumentChunkProjection, (doc_id, generation_id, row.chunk_index))
    if (parent is None or not parent.complete or parent.version != CURRENT_CHUNK_PROJECTION_VERSION
            or row.content_hash != digest(content)
            or (parent.source_id, parent.source_hash, parent.title_hash, parent.projection_hash) !=
               (row.source_id, projection.source_hash, projection.title_hash, projection.projection_hash)):
        raise GenerationIntegrityError()
    count = session.scalar(select(func.count()).select_from(DocumentChunkFragment).where(
        DocumentChunkFragment.doc_id == doc_id, DocumentChunkFragment.generation_id == generation_id,
        DocumentChunkFragment.chunk_index == row.chunk_index))
    if count != parent.fragment_count or bool(count) == projection.legacy:
        raise GenerationIntegrityError()
    iterator = projection.fragments()
    processed = 0
    while parts := list(islice(iterator, batch_size)):
        if not projection.legacy:
            stored = list(session.scalars(select(DocumentChunkFragment).where(
                DocumentChunkFragment.doc_id == doc_id, DocumentChunkFragment.generation_id == generation_id,
                DocumentChunkFragment.chunk_index == row.chunk_index,
                DocumentChunkFragment.ordinal.in_([p.ordinal for p in parts])).order_by(DocumentChunkFragment.ordinal)))
            if len(stored) != len(parts):
                raise GenerationIntegrityError()
            for part, actual in zip(parts, stored):
                identity = chunk_fragment_point_id(doc_id, row.chunk_index, generation_id, row.source_id, part)
                try:
                    checked = chunk_fragment_point_id_from_descriptor(doc_id, row.chunk_index,
                        generation_id, row.source_id, actual.descriptor)
                except (TypeError, ValueError, KeyError) as exc:
                    raise GenerationIntegrityError() from exc
                if checked != identity:
                    raise GenerationIntegrityError()
                if (actual.ordinal, actual.point_id, actual.descriptor, actual.content, actual.text_hash) != (
                        part.ordinal, identity, part.descriptor, part.text, part.text_hash):
                    raise GenerationIntegrityError()
            processed += len(parts)
        for part in parts:
            yield part
    if processed != parent.fragment_count:
        raise GenerationIntegrityError()


def backfill_projection_chunks(session, store, embedder, doc_id, generation_id,
                               filename, global_tags, dev_tags, source_locale, sources, batch_size, *, force=False):
    from app.services.vector_store import _qdrant_call, chunk_point_id
    scopes = build_mail_scope_map(sources)
    pending, indexed = [], 0

    def flush():
        nonlocal indexed
        if not pending:
            return
        expected = {item['point_id']: item for item in pending}
        existing = _qdrant_call(store.client.retrieve, collection_name=store.collection,
            ids=list(expected), with_payload=['point_type','doc_id','generation_id','source_id','chunk_index','chunk_fragment','chunk_fragment_group'],
            with_vectors=False)
        present = set()
        group_updates = {}
        for point in existing:
            identity = str(point.id)
            item = expected.get(identity)
            payload = point.payload or {}
            if item is None or (payload.get('doc_id'), payload.get('generation_id'), payload.get('source_id'),
                                payload.get('chunk_index'), payload.get('chunk_fragment')) != (
                    doc_id, generation_id, item['source_id'], item['chunk_index'], item['chunk_fragment']):
                raise GenerationIntegrityError()
            if payload.get('point_type') != 'chunk' or type(payload.get('chunk_index')) is not int:
                raise GenerationIntegrityError()
            if item['chunk_fragment'] is not None:
                try:
                    checked = chunk_fragment_point_id_from_descriptor(doc_id, item['chunk_index'],
                        generation_id, item['source_id'], payload.get('chunk_fragment'))
                except (TypeError, ValueError, KeyError) as exc:
                    raise GenerationIntegrityError() from exc
                if checked != identity:
                    raise GenerationIntegrityError()
            present.add(identity)
            if item['chunk_fragment'] is not None:
                group = chunk_fragment_group_id(doc_id, generation_id, item['source_id'], item['chunk_index'])
                if payload.get('chunk_fragment_group') != group:
                    group_updates.setdefault(group, []).append(identity)
        for group, identities in group_updates.items():
            _qdrant_call(store.client.set_payload, collection_name=store.collection,
                         payload={'chunk_fragment_group':group}, points=identities, wait=True)
        missing = [item for item in pending if force or item['point_id'] not in present]
        if missing:
            vectors = embedder.embed_texts([item['content'] for item in missing])
            indexed += len(store.index_chunk_projection_fragments(doc_id, missing, global_tags, vectors,
                generation_id=generation_id, dev_tags=dev_tags, source_locale=source_locale))
        pending.clear()

    rows = session.scalars(select(DocumentChunk).where(DocumentChunk.doc_id == doc_id)
                           .order_by(DocumentChunk.chunk_index).execution_options(yield_per=1))
    for row in rows:
        if row.table_refs:
            flush()
            identity = chunk_point_id(doc_id, row.chunk_index, generation_id=generation_id)
            existing = _qdrant_call(store.client.retrieve, collection_name=store.collection,
                                   ids=[identity], with_payload=['doc_id','generation_id','point_type',
                                       'source_id','chunk_index','chunk_fragment'], with_vectors=False)
            if existing:
                point = existing[0]
                payload = point.payload or {}
                if (len(existing) != 1 or str(point.id) != identity
                        or (payload.get('doc_id'), payload.get('generation_id'), payload.get('source_id'),
                            payload.get('chunk_index'), payload.get('point_type')) !=
                        (doc_id, generation_id, row.source_id, row.chunk_index, 'chunk')
                        or type(payload.get('chunk_index')) is not int
                        or payload.get('chunk_fragment') is not None):
                    raise GenerationIntegrityError()
                if not force:
                    continue
            text = row.content or ''
            title = row.section_title or ''
            capped = text[:store.settings.okf_max_chunk_index_chars]
            vectors = embedder.embed_texts([title + '\n' + capped if title else capped])
            indexed += len(store.index_chunks(doc_id, filename, [text], global_tags, vectors,
                generation_id=generation_id, source_ids=[row.source_id], chunk_indices=[row.chunk_index],
                section_titles=[title], mail_scopes=[scopes.get(row.source_id,'unknown')],
                dev_tags=dev_tags, source_locale=source_locale))
            continue
        for part in verified_chunk_parts(session, doc_id, generation_id, row, batch_size):
            pending.append({'point_id':chunk_fragment_point_id(doc_id, row.chunk_index, generation_id, row.source_id, part),
                            'chunk_index':row.chunk_index, 'source_id':row.source_id,
                            'content':part.text, 'chunk_fragment':part.descriptor,
                            'mail_scope':scopes.get(row.source_id,'unknown')})
            if len(pending) == batch_size:
                flush()
    flush()
    return indexed


def rebuild_projection_sparse(session, store, doc_id, generation_id, target_ids, batch_size):
    from app.services.vector_store import _qdrant_call, SPARSE_VECTOR_NAME, chunk_point_id, _sparse_text
    from app.services.sparse import to_sparse_vector
    from qdrant_client import models as qm
    pending, total = [], 0
    def flush():
        nonlocal total
        if pending:
            _qdrant_call(store.client.update_vectors, collection_name=store.collection, points=pending)
            total += len(pending)
            pending.clear()
    rows = session.scalars(select(DocumentChunk).where(DocumentChunk.doc_id == doc_id)
                           .order_by(DocumentChunk.chunk_index).execution_options(yield_per=1))
    for row in rows:
        if row.table_refs:
            representations = [(chunk_point_id(doc_id,row.chunk_index,generation_id=generation_id),
                _sparse_text(row.section_title or '', (row.content or '')[:store.settings.okf_max_chunk_index_chars]))]
        else:
            representations = ((chunk_fragment_point_id(doc_id,row.chunk_index,generation_id,row.source_id,part),part.text)
                for part in verified_chunk_parts(session,doc_id,generation_id,row,batch_size))
        for identity, text in representations:
            if identity not in target_ids:
                continue
            sparse = to_sparse_vector(text)
            if not sparse.indices:
                continue
            pending.append(qm.PointVectors(id=identity,vector={SPARSE_VECTOR_NAME:sparse}))
            if len(pending) == batch_size:
                flush()
    flush()
    return total


def active_chunk_projection_metadata(session, doc_id, generation_id, index):
    """Bounded canonical metadata for offline point/audit checks, including trash."""
    from app.services.chunk_projection_store import canonical_title_hash
    row = session.execute(select(DocumentChunk.source_id, DocumentChunk.content_hash,
        canonical_title_hash(session), DocumentChunk.table_refs).where(
            DocumentChunk.doc_id==doc_id, DocumentChunk.chunk_index==index)).one_or_none()
    if row is None:
        raise GenerationIntegrityError()
    parent = session.get(DocumentChunkProjection,(doc_id,generation_id,index))
    if row[3]:
        if parent is not None:
            raise GenerationIntegrityError()
        return row[0], None
    if (parent is None or not parent.complete or parent.version!=CURRENT_CHUNK_PROJECTION_VERSION
            or (parent.source_id,parent.source_hash,parent.title_hash)!=(row[0],row[1],row[2])):
        raise GenerationIntegrityError()
    count, first, last = session.execute(select(func.count(),func.min(DocumentChunkFragment.ordinal),
        func.max(DocumentChunkFragment.ordinal)).where(DocumentChunkFragment.doc_id==doc_id,
        DocumentChunkFragment.generation_id==generation_id,DocumentChunkFragment.chunk_index==index)).one()
    if count!=parent.fragment_count or (count and (first!=0 or last!=count-1)):
        raise GenerationIntegrityError()
    return row[0], parent


def valid_active_chunk_point(session, doc_id, generation_id, index, identity, descriptor, cache):
    from app.services.vector_store import chunk_point_id
    if generation_projection_version(session,doc_id,generation_id) is None:
        return descriptor is None and identity==chunk_point_id(doc_id,index,generation_id=generation_id)
    key=(doc_id,generation_id,index)
    if key not in cache:
        cache[key]=active_chunk_projection_metadata(session,doc_id,generation_id,index)
    source,parent=cache[key]
    if parent is None or parent.fragment_count==0:
        return descriptor is None and identity==chunk_point_id(doc_id,index,generation_id=generation_id)
    if not isinstance(descriptor,dict):
        return False
    try:
        checked=chunk_fragment_point_id_from_descriptor(doc_id,index,generation_id,source,descriptor)
    except (TypeError,ValueError,KeyError):
        return False
    part=session.get(DocumentChunkFragment,(doc_id,generation_id,index,descriptor['ordinal']))
    if part is not None:
        try:
            if chunk_fragment_point_id_from_descriptor(doc_id,index,generation_id,source,part.descriptor)!=identity:
                return False
        except (TypeError,ValueError,KeyError):
            return False
    return (part is not None and 0<=part.ordinal<parent.fragment_count and part.point_id==identity==checked
            and part.descriptor==descriptor and part.text_hash==digest(part.content)==descriptor['text_hash'])


def active_chunk_point_ids(session,doc_id,generation_id,index,batch_size):
    from app.services.vector_store import chunk_point_id
    if generation_projection_version(session,doc_id,generation_id) is None:
        yield chunk_point_id(doc_id,index,generation_id=generation_id)
        return
    source,parent=active_chunk_projection_metadata(session,doc_id,generation_id,index)
    if parent is None or parent.fragment_count==0:
        yield chunk_point_id(doc_id,index,generation_id=generation_id)
        return
    records=session.execute(select(DocumentChunkFragment.ordinal,DocumentChunkFragment.point_id,DocumentChunkFragment.descriptor).where(
        DocumentChunkFragment.doc_id==doc_id,DocumentChunkFragment.generation_id==generation_id,
        DocumentChunkFragment.chunk_index==index).order_by(DocumentChunkFragment.ordinal).execution_options(yield_per=batch_size))
    count=0
    for ordinal,identity,descriptor in records:
        try:
            checked=chunk_fragment_point_id_from_descriptor(doc_id,index,generation_id,source,descriptor)
        except (TypeError,ValueError,KeyError) as exc:
            raise GenerationIntegrityError() from exc
        if ordinal!=count or identity!=checked:
            raise GenerationIntegrityError()
        count+=1
        yield identity
    if count!=parent.fragment_count:
        raise GenerationIntegrityError()
