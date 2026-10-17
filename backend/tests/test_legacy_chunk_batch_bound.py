"""Legacy backfill must bound dense inputs and avoid recreating deleted documents."""
from datetime import datetime,timezone
from types import SimpleNamespace
from app.db.models import Document,DocumentChunk
from app.db.session import session_scope
from tests.test_backfills import _vs


def seed(count):
 doc='legacy-bound'
 with session_scope() as s:
  s.add(Document(id=doc,filename='synthetic',status='done'));s.flush()
  s.add_all([DocumentChunk(doc_id=doc,chunk_index=n,content=f'physical 0 False row{n}',char_count=23) for n in range(count)])
 return doc


def test_legacy_embedding_inputs_are_bounded(tmp_path,monkeypatch):
 seed(129);store,fake=_vs(tmp_path,monkeypatch,[]);calls=[]
 def embed(texts):
  calls.append(len(texts));assert len(texts)<=16
  return [[0.]*8 for _ in texts]
 assert store.backfill_chunks(SimpleNamespace(embed_texts=embed),batch_size=16)==129
 assert sum(calls)==129 and len(fake.upserted)==129


def test_document_trashed_during_embedding_is_not_recreated(tmp_path,monkeypatch):
 doc=seed(2);store,fake=_vs(tmp_path,monkeypatch,[])
 def embed(texts):
  with session_scope() as s:s.get(Document,doc).deleted_at=datetime.now(timezone.utc)
  return [[0.]*8 for _ in texts]
 assert store.backfill_chunks(SimpleNamespace(embed_texts=embed),batch_size=16)==0
 assert not fake.upserted


def test_generation_published_during_embedding_is_not_recreated(tmp_path, monkeypatch):
    from app.services.generation_store import begin_generation, mark_generation_ready, publish_generation
    doc = seed(2)
    store, fake = _vs(tmp_path, monkeypatch, [])
    def embed(texts):
        with session_scope() as session:
            generation = begin_generation(session, doc)
            mark_generation_ready(session, doc, generation.id)
            publish_generation(session, doc, generation.id)
        return [[0.] * 8 for _ in texts]
    assert store.backfill_chunks(SimpleNamespace(embed_texts=embed), batch_size=16) == 0
    assert not fake.upserted


def test_sql_content_cap_preserves_unicode_and_last_value(tmp_path, monkeypatch):
    doc = seed(1)
    with session_scope() as session:
        row = session.query(DocumentChunk).filter_by(doc_id=doc, chunk_index=0).one()
        row.section_title = 'Заголовок'
        row.content = 'Я😀0False\x00TAIL'
    store, fake = _vs(tmp_path, monkeypatch, [])
    store.settings.okf_max_chunk_index_chars = 13
    calls = []
    def embed(texts):
        calls.extend(texts)
        return [[0.] * 8 for _ in texts]
    assert store.backfill_chunks(SimpleNamespace(embed_texts=embed), batch_size=16) == 1
    assert calls == ['Заголовок\nЯ😀0False\x00TAIL']
    assert len(fake.upserted) == 1
