"""Validate generation-scoped projections against full prepared canonical text.

Publication may replay one source at a time. Search must use the bounded store
reader instead. No caller can publish a partial or payload-selected format.
"""
from itertools import islice
from sqlalchemy import select, func, and_
from app.db.models import DocumentGeneration, DocumentChunkProjection, DocumentChunkFragment
from app.services.chunk_index_projection import CURRENT_CHUNK_PROJECTION_VERSION, build_chunk_index_projection, chunk_fragment_point_id, chunk_fragment_point_id_from_descriptor, chunk_fragment_group_id, digest
from app.services.generation_artifacts import GenerationIntegrityError


def verify_generation_chunk_projections(session, doc_id, generation_id):
    generation = session.get(DocumentGeneration, generation_id, populate_existing=True)
    if generation is None or generation.doc_id != doc_id:
        raise GenerationIntegrityError()
    version = generation.chunk_projection_version
    if version is not None and version != CURRENT_CHUNK_PROJECTION_VERSION:
        raise GenerationIntegrityError()
    parents = session.scalars(select(DocumentChunkProjection).where(
        DocumentChunkProjection.doc_id == doc_id,
        DocumentChunkProjection.generation_id == generation_id).execution_options(yield_per=64))
    for parent in parents:
        if version is None or parent.version != version or not parent.complete:
            raise GenerationIntegrityError()
        count, first, last = session.execute(select(func.count(),func.min(DocumentChunkFragment.ordinal),func.max(DocumentChunkFragment.ordinal)).where(
            DocumentChunkFragment.doc_id==doc_id,DocumentChunkFragment.generation_id==generation_id,
            DocumentChunkFragment.chunk_index==parent.chunk_index)).one()
        if count != parent.fragment_count or (count and (first!=0 or last!=count-1)):
            raise GenerationIntegrityError()
    orphan = session.scalar(select(DocumentChunkFragment.ordinal).outerjoin(DocumentChunkProjection,and_(
        DocumentChunkFragment.doc_id==DocumentChunkProjection.doc_id,
        DocumentChunkFragment.generation_id==DocumentChunkProjection.generation_id,
        DocumentChunkFragment.chunk_index==DocumentChunkProjection.chunk_index)).where(
            DocumentChunkFragment.doc_id==doc_id,DocumentChunkFragment.generation_id==generation_id,
            DocumentChunkProjection.doc_id.is_(None)).limit(1))
    if orphan is not None:
        raise GenerationIntegrityError()


def verify_chunk_projection_publication(session, doc_id, generation_id, chunks):
    """Return expected chunk payload identities; retain only one bounded text batch."""
    from app.services.vector_store import chunk_point_id
    verify_generation_chunk_projections(session,doc_id,generation_id)
    expected = {}
    generic_indices = set()
    seen_indices = set()
    for row in chunks:
        index=row.get('chunk_index')
        if type(index) is not int or index < 0 or index in seen_indices:
            raise GenerationIntegrityError()
        seen_indices.add(index)
        title=row.get('section_title') or ''
        content=row.get('content') or ''
        source=row.get('source_id')
        if not isinstance(title,str) or not isinstance(content,str) or row.get('content_hash') != digest(content):
            raise GenerationIntegrityError()
        base={'point_type':'chunk','chunk_index':index,'source_id':source}
        if row.get('table_refs'):
            expected[chunk_point_id(doc_id,index,generation_id=generation_id)]={**base,'chunk_fragment':None}
            continue
        generic_indices.add(index)
        projection=build_chunk_index_projection(title,content,version=CURRENT_CHUNK_PROJECTION_VERSION)
        parent=session.get(DocumentChunkProjection,(doc_id,generation_id,index),populate_existing=True)
        if parent is None or (
            parent.source_id,parent.source_hash,parent.title_hash,parent.projection_hash
        ) != (source,projection.source_hash,projection.title_hash,projection.projection_hash):
            raise GenerationIntegrityError()
        count=0
        iterator=projection.fragments()
        if projection.legacy:
            if parent.fragment_count != 0:
                raise GenerationIntegrityError()
            part=next(iterator)
            expected[chunk_fragment_point_id(doc_id,index,generation_id,source,part)]={**base,'chunk_fragment':None}
            continue
        while batch:=list(islice(iterator,64)):
            stored=list(session.scalars(select(DocumentChunkFragment).where(
                DocumentChunkFragment.doc_id==doc_id,DocumentChunkFragment.generation_id==generation_id,
                DocumentChunkFragment.chunk_index==index,DocumentChunkFragment.ordinal.in_([p.ordinal for p in batch])
            ).order_by(DocumentChunkFragment.ordinal)))
            if len(stored)!=len(batch):
                raise GenerationIntegrityError()
            for part,item in zip(batch,stored):
                point_id=chunk_fragment_point_id(doc_id,index,generation_id,source,part)
                try:
                    checked_id=chunk_fragment_point_id_from_descriptor(doc_id,index,generation_id,source,item.descriptor)
                except (ValueError,TypeError,KeyError) as exc:
                    raise GenerationIntegrityError() from exc
                if checked_id != point_id:
                    raise GenerationIntegrityError()
                if (item.ordinal,item.point_id,item.descriptor,item.content,item.text_hash) != (
                    part.ordinal,point_id,part.descriptor,part.text,part.text_hash):
                    raise GenerationIntegrityError()
                expected[point_id]={**base,'chunk_fragment':part.descriptor,
                    'chunk_fragment_group':chunk_fragment_group_id(doc_id,generation_id,source,index)}
            count+=len(batch)
        if count!=parent.fragment_count or count==0:
            raise GenerationIntegrityError()
    actual_indices=set(session.scalars(select(DocumentChunkProjection.chunk_index).where(
        DocumentChunkProjection.doc_id==doc_id,DocumentChunkProjection.generation_id==generation_id)))
    if actual_indices!=generic_indices:
        raise GenerationIntegrityError()
    return expected
