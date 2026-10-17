"""Grouped row predicates must preserve complete document/generation/table identities."""
from sqlalchemy import select
from app.db.models import Document,DocumentGeneration,DocumentTable,DocumentTableRow
from app.db.session import session_scope
from app.services.generation_store import begin_generation
from app.services import table_retrieval


def test_grouped_identifiers_do_not_mix_documents_generations_tables_or_row_sets():
 with session_scope() as session:
  session.add_all([Document(id='identity-a',filename='a.xlsx',status='done'),Document(id='identity-b',filename='b.xlsx',status='done')]);session.flush()
  a1=begin_generation(session,'identity-a').id;a2='c'*32;b1=begin_generation(session,'identity-b').id
  session.add(DocumentGeneration(id=a2,doc_id='identity-a',phase='retired'));session.flush()
  scopes=[('identity-a',a1,'a'*32),('identity-a',a1,'b'*32),('identity-a',a2,'a'*32),('identity-b',b1,'a'*32)]
  for doc,gen,table in scopes:
   session.add(DocumentTable(doc_id=doc,generation_id=gen,table_id=table,source_id='root',descriptor={},quality={'complete':True}));session.flush()
   session.add_all([DocumentTableRow(doc_id=doc,generation_id=gen,table_id=table,row_number=n,cells={}) for n in [2,3,4]])
  session.flush()
  expected=[(*scopes[0],2),(*scopes[1],3),(*scopes[2],4),(*scopes[3],2)]
  condition=table_retrieval._row_identity_condition(expected+[expected[0]])
  columns=[DocumentTableRow.doc_id,DocumentTableRow.generation_id,DocumentTableRow.table_id,DocumentTableRow.row_number]
  actual=session.execute(select(*columns).where(condition)).all()
  assert set(actual)==set(expected) and len(actual)==4
  assert session.execute(select(*columns).where(table_retrieval._row_identity_condition([]))).all()==[]
