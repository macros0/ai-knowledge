"""Narrow canonical field projection and wide-header hydration regressions."""
from types import SimpleNamespace

from app.db.models import Document, DocumentTable, DocumentTableRow
from app.db.session import session_scope
from app.services.generation_store import begin_generation
from app.services.fusion import Hit
from app.services import table_retrieval

DOC = 'c123456789abcdef'
TABLE = 'b' * 32


def rows_fixture():
    with session_scope() as session:
        session.add(Document(id=DOC, filename='control.xlsx', status='done'))
        session.flush()
        generation = begin_generation(session, DOC).id
        session.add(DocumentTable(doc_id=DOC, generation_id=generation, table_id=TABLE,
            source_id='root', descriptor={}, quality={'complete': True}))
        session.flush()
        for number, cells in [(2, {'1': {'display_text': '0'}, '2': {'display_text': 'False'},
                                  '3': {'display_text': 'текст " | \n'}, '9': {'display_text': 'private unrequested'}}),
                              (3, {'1': {'display_text': None}}), (4, {}), (5, None), (6, []), (7, False),
                              (8, {'1': 'junk', '2': '{\"display_text\":\"3100\"}', '3': False})]:
            session.add(DocumentTableRow(doc_id=DOC, generation_id=generation,
                                        table_id=TABLE, row_number=number, cells=cells))
    return generation


def test_projected_rows_preserve_missing_null_false_zero_and_unicode():
    generation = rows_fixture()
    keys = [(DOC, generation, TABLE, n) for n in [2, 3, 4, 5, 6, 7, 8, 999]]
    with session_scope() as session:
        rows = table_retrieval._read_projected_rows(session, [(keys, [1, 2, 3])])
    assert rows[keys[0]] == {1: '0', 2: 'False', 3: 'текст " | \n'}
    assert rows[keys[1]] == {1: None, 2: None, 3: None}
    assert rows[keys[2]] == {1: None, 2: None, 3: None}
    assert all(rows[k] == {1: None, 2: None, 3: None} for k in keys[3:7])
    assert keys[7] not in rows
    assert all(9 not in values for values in rows.values())


def test_projected_rows_do_not_cross_doc_generation_table_or_row_identity():
    generation = rows_fixture()
    wrong = [(DOC, 'a' * 32, TABLE, 2), ('different-doc', generation, TABLE, 2),
             (DOC, generation, 'c' * 32, 2), (DOC, generation, TABLE, 1)]
    with session_scope() as session:
        assert table_retrieval._read_projected_rows(session, [(wrong, [1])]) == {}


def test_many_refs_reuse_wide_header_labels_without_changing_fragments(monkeypatch):
    class Quality(dict):
        reads = 0
        def get(self, key, default=None):
            self.reads += 1
            return super().get(key, default)
    quality = Quality(complete=True)
    class Columns(list):
        reads = 0
        def __iter__(self):
            self.reads += 1
            return super().__iter__()
    columns = Columns([{'index': n, 'header_path': ['Code' if n == 1 else f'Field{n}']}
                       for n in range(1, 129)])
    generation = 'a' * 32
    ref = {'kind': 'table', 'generation_id': generation, 'source_id': 'root',
           'table_id': TABLE, 'row_number': 2, 'columns': [1]}
    stored = SimpleNamespace(doc_id=DOC, source_id='root', content='unchanged canonical text',
                             table_refs=[ref.copy() for _ in range(128)])
    table = SimpleNamespace(doc_id=DOC, generation_id=generation, table_id=TABLE,
                            source_id='root', quality=quality,
                            descriptor={'columns': columns, 'header_rows': [1]})
    key = (DOC, generation, TABLE, 2)
    class Session:
        def scalars(self, statement): return [table]
        def execute(self, statement): return [(*key, '3100')]
    monkeypatch.setattr(table_retrieval, '_read_identifier_rows',
                        lambda session, batches: {key: {1: '3100'}}, raising=False)
    hit = Hit('c', 1, {'point_type': 'concept', 'doc_id': DOC, 'slug': 'row',
                       'table_refs': [{'table_id': 'forged'}]})
    result = table_retrieval.hydrate_table_fragments([hit], {'session': Session(),
        'active_generations': {DOC: generation}, 'canonical_values': {(DOC, 'concept', 'row'): stored},
        'query': 'Code 3100'})
    assert result == [hit] and hit.payload['content'] == stored.content
    assert len(hit.payload['table_refs']) == 128
    assert all(r['table_id'] == TABLE for r in hit.payload['table_refs'])
    assert len(hit.payload['_table_identifier_cells']) == 128
    assert all(c['value'] == '3100' and c['labels'] == ['Code']
               for c in hit.payload['_table_identifier_cells'])
    assert columns.reads <= 2, 'Wide header work must scale with tables, not every reference'

    assert quality.reads == 1, 'Completeness work must scale with tables, not repeated refs'


def test_mixed_refs_fail_closed_without_reusing_another_tables_validity(monkeypatch):
    import copy
    generation = 'a' * 32
    other = 'c' * 32
    def ref(table, source='root'):
        return {'kind': 'table', 'generation_id': generation, 'source_id': source,
                'table_id': table, 'row_number': 2, 'columns': [1]}
    good = SimpleNamespace(doc_id=DOC, generation_id=generation, table_id=TABLE,
        source_id='root', quality={'complete': True},
        descriptor={'columns': [{'index': 1, 'header_path': ['Code']}], 'header_rows': [1]})
    class Session:
        def scalars(self, statement): return tables
    monkeypatch.setattr(table_retrieval, '_read_identifier_rows',
        lambda session, batches: {(DOC, generation, TABLE, 2): {1: '3100'}})
    for query in ('Code 3100', 'ordinary text'):
        for state in ('missing', 'incomplete', 'wrong_source', 'wrong_generation'):
            bad = copy.deepcopy(good)
            bad.table_id = other
            if state == 'incomplete': bad.quality = {'complete': False}
            if state == 'wrong_source': bad.source_id = 'different-source'
            if state == 'wrong_generation': bad.generation_id = 'd' * 32
            tables = [good] if state == 'missing' else [good, bad]
            stored = {
                (DOC, 'concept', 'good'): SimpleNamespace(doc_id=DOC, source_id='root',
                    content='good', table_refs=[ref(TABLE)] * 128),
                (DOC, 'concept', 'mixed'): SimpleNamespace(doc_id=DOC, source_id='root',
                    content='mixed', table_refs=[ref(TABLE)] * 128 + [ref(other)]),
            }
            hits = [Hit(slug, 1, {'point_type': 'concept', 'doc_id': DOC, 'slug': slug})
                    for slug in ('mixed', 'good')]
            result = table_retrieval.hydrate_table_fragments(hits, {'session': Session(),
                'active_generations': {DOC: generation}, 'canonical_values': stored,
                'query': query})
            assert result == [hits[1]], (query, state)
            assert result[0].payload['content'] == 'good'
            assert len(result[0].payload['table_refs']) == 128
