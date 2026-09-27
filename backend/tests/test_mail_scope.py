import pytest
from sqlalchemy import event

from app.services.mail_scope import (
    MAIL_SCOPE_VERSION, build_mail_scope_map, build_record_mail_scopes,
    classify_mail_scope, mail_scope_allowed,
)
from app.services.source_store import fetch_source_trees, replace_sources
from app.db.session import session_scope
from app.db.models import Document


def node(sid='root', parent=None, kind='document', metadata=None, **extras):
    return dict(source_id=sid, parent_source_id=parent, kind=kind, metadata=metadata, **extras)


@pytest.mark.parametrize('root,scope', [
    (node(), 'document'), (node(kind='mail'), 'mail'),
    (node(metadata={'mail': True}), 'mail'),
    (node(metadata={'mail': 'true'}), 'document'),
    (node(metadata={'mail': 1}), 'document'),
    (node(metadata=['mail']), 'document'),
    (node(metadata='mail', display_name='test.msg', tags=['mail']), 'document'),
])
def test_root_scope_is_based_only_on_canonical_mail_flag(root, scope):
    assert classify_mail_scope('root', [root]) == scope
    assert MAIL_SCOPE_VERSION == 1


def test_descendants_inherit_mail_but_siblings_and_containers_do_not():
    sources = [node(), node('ordinary', 'root', container_source_id='email'),
               node('email', 'root', 'mail'), node('docx', 'email'),
               node('pdf', 'docx'), node('nested', 'pdf', 'mail')]
    assert build_mail_scope_map(sources) == dict(root='document', ordinary='document',
                                               email='mail', docx='mail', pdf='mail', nested='mail')


@pytest.mark.parametrize('sources', [
    [], [node('other')], [node(parent='other'), node('other', 'root')],
    [node(), node()], [node(), node('second')], [node(), node('')],
])
def test_ambiguous_root_or_duplicate_invalidates_entire_tree(sources):
    assert classify_mail_scope('root', sources) == 'unknown'
    assert all(value == 'unknown' for value in build_mail_scope_map(sources).values())


def test_corrupt_mail_branch_does_not_invalidate_healthy_neighbor():
    sources = [node(), node('healthy', 'root'), node('email', 'cycle', 'mail'),
               node('cycle', 'email'), node('missing', 'absent'), node('empty', '')]
    scopes = build_mail_scope_map(sources)
    assert scopes['healthy'] == 'document'
    for sid in ('email', 'cycle', 'missing', 'empty'):
        assert scopes[sid] == 'unknown'
    for sid in (None, '', 'absent'):
        assert classify_mail_scope(sid, sources) == 'unknown'


@pytest.mark.parametrize('mode,accepted', [('all', {'mail', 'document', 'unknown'}),
                                          ('only', {'mail'}), ('exclude', {'document'})])
def test_allowed_modes(mode, accepted):
    for scope in ('mail', 'document', 'unknown'):
        assert mail_scope_allowed(scope, mode) is (scope in accepted)


@pytest.mark.parametrize('mode', [None, 'ONLY', 'invalid', False])
def test_invalid_mode_raises_even_for_unknown_scope(mode):
    with pytest.raises(ValueError):
        mail_scope_allowed('unknown', mode)


def test_deep_tree_uses_iterative_linear_walk():
    sources = [node(kind='mail')]
    for i in range(10000):
        sources.append(node(str(i), 'root' if i == 0 else str(i - 1)))
    scopes = build_mail_scope_map(list(reversed(sources)))
    assert len(scopes) == 10001
    assert set(scopes.values()) == {'mail'}


def test_record_scopes_preserve_order_and_reject_source_mismatch():
    sources = [node(), node('email', 'root', 'mail')]
    chunks = [dict(chunk_index=40, source_id='email'),
              dict(chunk_index=3, source_id='root'), dict(chunk_index=50, source_id=None)]
    concepts = [dict(chunk_index=3, source_id='root'), dict(chunk_index=40, source_id='root'),
                dict(chunk_index=40, source_id=None), dict(chunk_index=90, source_id='email'),
                dict(chunk_index=50, source_id='email')]
    assert build_record_mail_scopes(sources, concepts, chunks) == (
        ['document', 'unknown', 'unknown', 'mail', 'mail'], ['mail', 'document', 'unknown'])


def test_batch_adapter_uses_caller_snapshot_and_doc_key():
    with session_scope() as session:
        session.add_all([Document(id='doc', filename='a'), Document(id='mail', filename='b')])
        session.flush()
        replace_sources(session, 'doc', [node()])
        replace_sources(session, 'mail', [node(kind='mail', metadata={'mail': True, 'subject': 'private'})])
        statements = []
        def observe(*args):
            statements.append(args[2])
        event.listen(session.bind, 'before_cursor_execute', observe)
        try:
            trees = fetch_source_trees(session, {'doc', 'mail', 'missing'})
            assert len(statements) == 1
            assert fetch_source_trees(session, set()) == {}
            assert len(statements) == 1
        finally:
            event.remove(session.bind, 'before_cursor_execute', observe)
        assert trees['missing'] == []
        assert classify_mail_scope('root', trees['doc']) == 'document'
        assert classify_mail_scope('root', trees['mail']) == 'mail'
        assert trees['mail'][0]['metadata']['subject'] == 'private'


@pytest.fixture
def scope_store():
    from types import SimpleNamespace
    from qdrant_client import QdrantClient
    from app.services.vector_store import VectorStore
    store = VectorStore.__new__(VectorStore)
    store.settings = SimpleNamespace(qdrant_collection='mail-scope', embedding_dimensions=2,
                                     qdrant_upsert_batch_size=256, okf_max_concept_chars=4000, okf_max_chunk_index_chars=8000)
    store.client = QdrantClient(':memory:')
    store.ensure_collection()
    yield store
    store.client.close()


def concept(source='root', chunk=0):
    from app.models.schemas import OkfDocument
    return OkfDocument(filepath='test.md', content='shared evidence', markdown='',
                       metadata={'source_id': source, 'chunk_index': chunk,
                                 'mail_scope': 'forged', 'title': 'Topic'})


@pytest.mark.parametrize('provided,expected', [(None, 'unknown'), (['mail'], 'mail'),
                                              (['document'], 'document')])
def test_indexed_concept_and_chunk_scope(scope_store, provided, expected):
    kwargs = {} if provided is None else {'mail_scopes': provided}
    cids = scope_store.index_concepts('doc', [concept()], [[1, 0]], **kwargs)
    hids = scope_store.index_chunks('doc', 'a', ['evidence'], [], [[1, 0]],
                                  source_ids=['root'], chunk_indices=[44], **kwargs)
    rows = scope_store.client.retrieve(scope_store.collection, list(cids | hids))
    assert len(rows) == 2
    for row in rows:
        assert row.payload['mail_scope'] == expected
        assert row.payload['mail_scope_version'] == 1
    assert next(row for row in rows if row.payload['point_type'] == 'chunk').payload['chunk_index'] == 44


@pytest.mark.parametrize('scopes', [[], ['mail', 'document'], ['invalid'], [None], [True]])
@pytest.mark.parametrize('kind', ['concept', 'chunk'])
def test_invalid_scope_list_rejected_before_any_upsert(scope_store, scopes, kind):
    with pytest.raises(ValueError):
        if kind == 'concept':
            scope_store.index_concepts('doc', [concept()], [[1, 0]], mail_scopes=scopes)
        else:
            scope_store.index_chunks('doc', 'a', ['evidence'], [], [[1, 0]], mail_scopes=scopes)
    assert scope_store.client.count(scope_store.collection).count == 0


def test_empty_writers_and_noncontiguous_indices(scope_store):
    assert scope_store.index_concepts('doc', [], [], mail_scopes=[]) == set()
    assert scope_store.index_chunks('doc', 'a', [], [], [], mail_scopes=[]) == set()
    ids = scope_store.index_chunks('doc', 'a', ['one', 'two'], [], [[1, 0], [0, 1]],
                                  chunk_indices=[99, 2], mail_scopes=['mail', 'document'])
    rows = scope_store.client.retrieve(scope_store.collection, list(ids))
    assert {r.payload['chunk_index']: r.payload['mail_scope'] for r in rows} == {99: 'mail', 2: 'document'}


def test_patch_is_idempotent_and_preserves_vectors_and_other_payload(scope_store, monkeypatch):
    ids = scope_store.index_concepts('doc', [concept()], [[1, 0]])
    pid = next(iter(ids))
    before = scope_store.client.retrieve(scope_store.collection, [pid], with_vectors=True)[0]
    assert scope_store.patch_mail_scopes({pid: 'mail'}) == 1
    after = scope_store.client.retrieve(scope_store.collection, [pid], with_vectors=True)[0]
    assert after.vector == before.vector
    assert {k: v for k, v in after.payload.items() if not k.startswith('mail_scope')} == {
        k: v for k, v in before.payload.items() if not k.startswith('mail_scope')}
    def unexpected(**kwargs):
        pytest.fail('unchanged patch must not write')
    monkeypatch.setattr(scope_store.client, 'set_payload', unexpected)
    assert scope_store.patch_mail_scopes({pid: 'mail'}) == 0


@pytest.mark.parametrize('failure', ['missing', 'mismatch'])
def test_patch_readback_failure_is_not_silenced(scope_store, monkeypatch, failure):
    from app.services.errors import VectorStoreError
    pid = next(iter(scope_store.index_concepts('doc', [concept()], [[1, 0]])))
    original = scope_store.client.retrieve
    calls = 0
    def retrieve(*args, **kwargs):
        nonlocal calls
        calls += 1
        rows = original(*args, **kwargs)
        if calls == 2:
            if failure == 'missing':
                return []
            rows[0].payload['mail_scope'] = 'document'
        return rows
    monkeypatch.setattr(scope_store.client, 'retrieve', retrieve)
    with pytest.raises(VectorStoreError):
        scope_store.patch_mail_scopes({pid: 'mail'})


def test_strict_index_check_reads_actual_schema_and_rejects_wrong_type():
    from types import SimpleNamespace
    from app.services.vector_store import VectorStore
    from app.services.errors import VectorStoreError
    store = VectorStore.__new__(VectorStore)
    store.settings = SimpleNamespace(qdrant_collection='test')
    schema = {}
    reads = []
    def get_collection(*args, **kwargs):
        reads.append(1)
        return SimpleNamespace(payload_schema=dict(schema))
    def create(**kwargs):
        assert kwargs['wait'] is True
        schema[kwargs['field_name']] = SimpleNamespace(data_type=kwargs['field_schema'])
    store.client = SimpleNamespace(get_collection=get_collection, create_payload_index=create)
    store.ensure_mail_scope_indexes()
    assert len(reads) >= 2
    assert str(schema['mail_scope'].data_type) == 'keyword'
    assert str(schema['mail_scope_version'].data_type) == 'integer'
    schema['mail_scope_version'] = SimpleNamespace(data_type='keyword')
    with pytest.raises(VectorStoreError):
        store.ensure_mail_scope_indexes()
