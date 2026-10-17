"""Frozen synthetic recall: one1151-part source must not hide other sources."""
import os
from uuid import uuid4
from copy import deepcopy
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient
from app.config import Settings
from app.db.models import Document,DocumentSource
from app.db.session import session_scope
from app.services.generation_store import begin_generation,mark_generation_ready,publish_generation
from app.services.chunk_store import replace_chunks
from app.services.chunk_index_projection import digest
from app.services.chunk_projection_indexing import index_generation_chunk_projections
from app.services.vector_store import VectorStore
from app.services.sparse import to_sparse_vector
from app.services.retrieval_hydration import load_visible_retrieval_hits
from app.services.context_builder import merge_and_format,drop_unmatched_blocks,drop_partial_title_matches,filter_mail_scope_blocks


@pytest.fixture
def quality_corpus():
    store=VectorStore.__new__(VectorStore)
    store.settings=Settings(_env_file=None,embedding_dimensions=2,search_graph_expansion_enabled=False,qdrant_collection='r2_quality_'+uuid4().hex)
    remote=os.environ.get('R2_QUALITY_QDRANT_URL')
    if remote:
        assert remote=='http://127.0.0.1:16333'
    store.client=QdrantClient(url=remote) if remote else QdrantClient(location=':memory:')
    store.ensure_collection()
    body='| Код | Описание |\n| --- | --- |\n'+''.join('| '+str(i)+' | '+(('уникальный хвост ' if i==1149 else '')+'x'*1700)+' |\n' for i in range(1150))
    corpus={'fanout-doc':[(0,'Каталог видов оплаты',body),(1,'Инструкция','Код. Проверенное правило.')],
            'other-doc':[(0,'Регламент','Код. Отдельное правило.')],
            'mail-doc':[(0,'Письмо','Код. Только письмо.') ]}
    for doc,chunks in corpus.items():
        with session_scope() as s:
            s.add(Document(id=doc,filename=doc+'.docx',status='done'));s.flush()
            s.add(DocumentSource(doc_id=doc,source_id='root',kind='mail' if doc=='mail-doc' else 'document',display_name=doc))
            gen=begin_generation(s,doc).id
        rows=[{'chunk_index':i,'section_title':title,'content':text,'source_id':'root','content_hash':digest(text),'char_count':len(text)} for i,title,text in chunks]
        ids=index_generation_chunk_projections(store,SimpleNamespace(embed_texts=lambda texts:[[1.,0.] for _ in texts]),doc,gen,doc+'.docx',rows,['mail' if doc=='mail-doc' else 'document']*len(rows),[],batch_size=64,dev_tags=[],source_locale=None)
        if doc=='fanout-doc':assert len(ids)==1152
        with session_scope() as s:
            replace_chunks(s,doc,rows);mark_generation_ready(s,doc,gen);publish_generation(s,doc,gen)
    yield store
    store.client.delete_collection(store.collection)
    store.client.close()


CASES=[('Код','all',{('fanout-doc',0),('fanout-doc',1),('other-doc',0),('mail-doc',0)}),
       ('Код','exclude',{('fanout-doc',0),('fanout-doc',1),('other-doc',0)}),
       ('Код','only',{('mail-doc',0)}),
       ('Каталог видов оплаты','all',{('fanout-doc',0)}),
       ('уникальный хвост','all',{('fanout-doc',0)}),
       ('правило','all',{('fanout-doc',1),('other-doc',0)})]


@pytest.mark.parametrize('branches', [{'dense', 'bm25'}, {'bm25'}], ids=['hybrid', 'bm25'])
@pytest.mark.parametrize('query,mail_mode,expected',CASES,ids=['shared-all','shared-document','shared-mail','exact-title','final-body','other-body'])
def test_fanout_preserves_expected_sources_without_limit_changes(quality_corpus,query,mail_mode,expected,branches):
    store=quality_corpus
    sparse=to_sparse_vector(query)
    query_filter=store._build_search_filter(None,mail_mode=mail_mode)
    raw_dense=store.search_dense([1.,0.],query_filter,40)
    raw_bm25=store.search_bm25(sparse,query_filter,40)
    fused=store.search_composite(dense_vec=[1.,0.],sparse_vec=sparse,tags=None,branches=branches,
        top_k=40,per_branch_top_k=40,mail_mode=mail_mode)
    visible,_=load_visible_retrieval_hits(deepcopy(fused),mail_mode=mail_mode)
    blocks=merge_and_format(visible,mail_mode=mail_mode,limit_total_chars=False)
    blocks=drop_unmatched_blocks(blocks,query)
    blocks=drop_partial_title_matches(blocks,query)
    blocks=filter_mail_scope_blocks(blocks,mail_mode=mail_mode)[:5]
    actual={(b['doc_id'],b['chunk_index']) for b in blocks}
    def keys(hits):return sorted({(h.payload['doc_id'],h.payload['chunk_index']) for h in hits})
    assert expected<=actual,{'dense':keys(raw_dense),'bm25':keys(raw_bm25),'fusion':keys(fused),'final':sorted(actual)}
    assert len(fused)<=40 and len(blocks)<=5


def test_grouped_candidates_obey_document_tag_locale_deleted_and_mail_scopes(quality_corpus):
    from qdrant_client import models as qm
    store = quality_corpus
    own = qm.Filter(must=[qm.FieldCondition(key='doc_id', match=qm.MatchValue(value='fanout-doc'))])
    store.client.set_payload(store.collection, {'tags':['selected'], 'source_locale':'ru'}, own)
    scopes = store._build_search_filter(['selected'], doc_ids=['fanout-doc'],
                                       source_locales=['ru'], mail_mode='exclude')
    for hits in (store.search_dense([1.,0.], scopes, 40),
                 store.search_bm25(to_sparse_vector('Код'), scopes, 40)):
        assert {(h.payload['doc_id'], h.payload['chunk_index']) for h in hits} == {('fanout-doc',0), ('fanout-doc',1)}
        assert len(hits) == 40  # Extra parts still fill the remaining slots.
    store.client.set_payload(store.collection, {'deleted':True}, own)
    assert not store.search_dense([1.,0.], scopes, 40)
    assert not store.search_bm25(to_sparse_vector('Код'), scopes, 40)
