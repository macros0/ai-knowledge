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
