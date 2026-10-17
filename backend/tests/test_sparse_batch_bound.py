"""Sparse maintenance must release vector batches before reading the next batch."""
from app.db.models import Document,OkfConcept
from app.db.session import session_scope
from app.services.vector_store import concept_point_id
from tests.test_backfills import _vs,_Rec


def test_sparse_flushes_before_vector_accumulation_exceeds_batch(tmp_path,monkeypatch):
 from app.services import vector_store as module
 doc='batch-bound-doc';count=129;batch=16
 with session_scope() as s:
  s.add(Document(id=doc,filename='synthetic',status='done'));s.flush()
  s.add_all([OkfConcept(doc_id=doc,slug=f'item-{n}',title='Synthetic',content=f'physical 0 False distinct{n}') for n in range(count)])
 records=[_Rec(concept_point_id(doc,f'item-{n}')) for n in range(count)]
 store,fake=_vs(tmp_path,monkeypatch,records)
 original=module.to_sparse_vector;processed=[];updates=[];ids=[]
 def sparse(value):processed.append(1);return original(value)
 def update(*,collection_name,points):
  updates.append((len(processed),len(points)));ids.extend(str(p.id) for p in points)
  assert len(points)<=batch
  assert len(processed)-len(ids)+len(points)<=batch
 monkeypatch.setattr(module,'to_sparse_vector',sparse);monkeypatch.setattr(fake,'update_vectors',update)
 assert store.backfill_sparse(force=True,include_chunks=False,batch_size=batch)==count
 assert updates[0]==(batch,batch)
 assert len(ids)==len(set(ids))==count


def test_sparse_reads_only_capped_body_from_sql(tmp_path, monkeypatch):
 from sqlalchemy import event
 from app.db.models import DocumentChunk
 from app.db.session import get_engine
 from app.services.vector_store import chunk_point_id
 doc='sparse-sql-cap'
 with session_scope() as s:
  s.add(Document(id=doc,filename='synthetic',status='done'));s.flush()
  s.add(OkfConcept(doc_id=doc,slug='one',title='Title',content='Я😀0False'+'x'*1000000))
  s.add(DocumentChunk(doc_id=doc,chunk_index=0,content='Я😀0False'+'x'*1000000))
 store,fake=_vs(tmp_path,monkeypatch,[_Rec(concept_point_id(doc,'one')),_Rec(chunk_point_id(doc,0))])
 store.settings.okf_max_concept_chars=8;store.settings.okf_max_chunk_index_chars=8
 statements=[]
 def observe(_connection,_cursor,statement,*_args):
  if ('FROM okf_concepts' in statement or 'FROM document_chunks' in statement):statements.append(statement)
 event.listen(get_engine(),'before_cursor_execute',observe)
 try:assert store.backfill_sparse(force=True,include_chunks=True,batch_size=16)==2
 finally:event.remove(get_engine(),'before_cursor_execute',observe)
 assert statements and all('substr(' in statement.lower() for statement in statements)
