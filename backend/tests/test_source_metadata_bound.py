"""Retrieval needs bounded allowlisted provenance, not the full metadata blob."""
import pytest
from sqlalchemy import event
from app.db.models import Document, DocumentSource
from app.db.session import session_scope, get_engine
from app.services import source_store


def seed(metadata):
    with session_scope() as session:
        session.add(Document(id='source-bound', filename='synthetic'))
        session.flush()
        session.add(DocumentSource(doc_id='source-bound', source_id='root', kind='document',
            display_name='😀' * 300, metadata_json=metadata))


def test_loader_projects_allowlist_and_bounds_text_in_sql():
    seed({'mail': True, 'subject': 'Я' * 2000, 'sender': '😀' * 2000,
          'sent_at': 't' * 100, 'date_raw': 'r' * 200, 'private': 'x' * 1_000_000})
    statements = []
    def observe(_connection, _cursor, statement, *_args):
        if 'FROM document_sources' in statement:
            statements.append(statement)
    event.listen(get_engine(), 'before_cursor_execute', observe)
    try:
        with session_scope() as session:
            rows = source_store.load_source_display_rows(session, {'source-bound'})
            assert not list(session.identity_map.values())
    finally:
        event.remove(get_engine(), 'before_cursor_execute', observe)
    assert len(rows) == 1 and len(statements) == 1
    row = rows[0]
    assert len(row.display_name) == 256
    assert row.metadata_json == {'mail': True, 'subject': 'Я' * 512, 'sender': '😀' * 512,
                                 'sent_at': 't' * 64, 'date_raw': 'r' * 128}
    assert 'substr' in statements[0].lower()
    assert source_store.fetch_source_paths(None, {('source-bound', 'root')}, rows=rows)[
        ('source-bound', 'root')][0]['subject'] == 'Я' * 512


@pytest.mark.parametrize('value,expected', [(True, True), (False, False), ('true', False),
                                         (1, False), ({}, False), (None, False)])
def test_mail_type_is_exact_and_non_string_labels_are_ignored(value, expected):
    seed({'mail': value, 'subject': 7, 'sender': ['fake'], 'sent_at': {}, 'date_raw': False})
    with session_scope() as session:
        rows = source_store.load_source_display_rows(session, {'source-bound'})
    assert rows[0].metadata_json == {'mail': expected}


def test_sqlite_projected_labels_preserve_embedded_nul():
    seed({'mail': True, 'subject': 'Before\x00After 0 False', 'sender': 'Я😀\x00Tail'})
    with session_scope() as session:
        rows = source_store.load_source_display_rows(session, {'source-bound'})
    assert rows[0].metadata_json['subject'] == 'Before\x00After 0 False'
    assert rows[0].metadata_json['sender'] == 'Я😀\x00Tail'
