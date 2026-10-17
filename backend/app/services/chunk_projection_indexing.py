"""Bounded candidate indexing for the trusted generic projection format."""
from app.services.chunk_index_projection import build_chunk_index_projection
from app.services.chunk_projection_store import materialize_chunk_projection


def index_generation_chunk_projections(vector_store, embedder, doc_id, generation_id, filename,
                                      rows, scopes, global_tags, *, batch_size, dev_tags, source_locale, projection_session=None):
    if len(rows) != len(scopes) or type(batch_size) is not int or not 1<=batch_size<=256:
        raise ValueError('Invalid projection indexing bounds')
    pending=[]
    point_ids=set()
    def flush():
        if not pending:
            return
        vectors=embedder.embed_texts([item['content'] for item in pending])
        point_ids.update(vector_store.index_chunk_projection_fragments(doc_id,pending,global_tags,vectors,
            generation_id=generation_id,dev_tags=dev_tags,source_locale=source_locale))
        pending.clear()
    for row,scope in zip(rows,scopes):
        title=row['section_title'] or ''
        content=row['content']
        index=row['chunk_index']
        source=row.get('source_id')
        if row.get('table_refs'):
            # TableRef keeps its existing canonical fragment route and identity.
            flush()
            capped=content[:vector_store.settings.okf_max_chunk_index_chars]
            vectors=embedder.embed_texts([title+'\n'+capped if title else capped])
            point_ids.update(vector_store.index_chunks(doc_id,filename,[content],global_tags,vectors,
                section_titles=[title],source_ids=[source],chunk_indices=[index],mail_scopes=[scope],
                generation_id=generation_id,dev_tags=dev_tags,source_locale=source_locale))
            continue
        if projection_session is None:
            materialize_chunk_projection(doc_id,generation_id,index,title,content,source_id=source,batch_size=batch_size)
            projection=build_chunk_index_projection(title,content,version=1)
            parts=projection.fragments()
        else:
            # Repair prepared these rows before opening its canonical snapshot.
            # Verify stored representations without writing via a second session.
            from types import SimpleNamespace
            from app.services.chunk_projection_maintenance import verified_chunk_parts
            projection=None
            parts=verified_chunk_parts(projection_session,doc_id,generation_id,SimpleNamespace(**row),batch_size)
        for part in parts:
            pending.append({'chunk_index':index,'source_id':source,'content':part.text,
                            'chunk_fragment':part.descriptor,'mail_scope':scope})
            if len(pending)==batch_size:
                flush()
        del projection
    flush()
    return point_ids
