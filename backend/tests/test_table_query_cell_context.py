"""Explicit cell context must come from the same visible, complete generation."""
from copy import deepcopy

import pytest

from app.db.models import Document, DocumentTable, DocumentTableRow
from app.db.session import session_scope
from app.services.generation_store import begin_generation
from app.services.fusion import Hit
from app.services import table_retrieval

DOC = 'query-cell-doc'
TABLE = 'd' * 32
QUERY = 'На листе «Контроль» для кода 7601 (строка 7) укажи значение поля «Значение» (колонка LC).'


def cell(address, raw, display, *, state='value', formula=None, cached=None, value_type='string'):
    return {'address': address, 'raw_value': raw, 'display_text': display,
            'state': state, 'formula': formula, 'cached_value': cached, 'value_type': value_type,
            'number_format': 'General', 'merge_anchor': None, 'hidden': False}


def setup(target=None):
    target = target if target is not None else cell('LC7', 'answer', 'answer')
    with session_scope() as session:
        session.add(Document(id=DOC, filename='control.xlsx', status='done'))
        session.flush()
        generation = begin_generation(session, DOC).id
        session.add(DocumentTable(doc_id=DOC, generation_id=generation, table_id=TABLE, source_id='root/0',
            descriptor={'sheet_index': 0, 'sheet_name': 'Контроль',
                'cell_range': {'sheet_index': 0, 'min_row': 1, 'max_row': 7, 'min_column': 1, 'max_column': 315},
                'header_rows': [1], 'merges': [], 'columns': [
                    {'index': 1, 'letter': 'A', 'header_path': ['Код'], 'header_addresses': ['A1']},
                    {'index': 100, 'letter': 'CV', 'header_path': ['Секрет'], 'header_addresses': ['CV1']},
                    {'index': 315, 'letter': 'LC', 'header_path': ['Значение'], 'header_addresses': ['LC1']}]},
            quality={'complete': True}))
        session.flush()
        session.add_all([DocumentTableRow(doc_id=DOC, generation_id=generation, table_id=TABLE,
            row_number=3, cells={'1': cell('A3', '7603', '7603'), '315': cell('LC3', 'different-row', 'different-row')}),
            DocumentTableRow(doc_id=DOC, generation_id=generation, table_id=TABLE,
            row_number=7, cells={'1': cell('A7', '7601', '7601'), '100': cell('CV7', 'private-unrequested', 'private-unrequested'), '315': target})])
    hit = Hit('known-visible', .05, {'doc_id': DOC, 'point_type': 'chunk', 'chunk_index': 12,
        'generation_id': generation, 'source_id': 'root/0', 'mail_scope': 'document',
        '_canonical_verified': True, 'content': 'old canonical row fragment',
        'table_refs': [{'kind': 'table', 'generation_id': generation, 'source_id': 'root/0',
                        'table_id': TABLE, 'row_number': 3, 'columns': [315]}]})
    return generation, hit


def project(generation, hits, query=QUERY, **overrides):
    function = getattr(table_retrieval, 'project_requested_table_cells', None)
    assert callable(function), 'bounded query-derived canonical cell context is missing'
    with session_scope() as session:
        return function(hits, {'session': session, 'active_generations': {DOC: generation}, 'query': query, **overrides})


def derived(hits):
    return [h for h in hits if h.payload.get('_table_cell_states')]


def test_rightmost_cell_can_be_read_from_an_already_visible_table_without_wrong_row_text():
    generation, hit = setup()
    before = deepcopy(hit.payload)
    result = project(generation, [hit])
    assert result[0] is hit and hit.payload == before
    rows = derived(result)
    assert len(rows) == 1
    assert 'LC7 | Значение: answer' in rows[0].payload['content']
    assert 'different-row' not in rows[0].payload['content'] and 'private-unrequested' not in rows[0].payload['content']
    refs = rows[0].payload['table_refs']
    assert refs[0]['columns'] == [315]
    assert all(r['generation_id'] == generation and r['source_id'] == 'root/0' and r['row_number'] == 7 for r in refs)
    assert any(1 in r['columns'] for r in refs), 'Row-key cell also needs provenance'


@pytest.mark.parametrize('target,state,text', [
    (cell('LC7', 0, '0', value_type='number'), 'value', ': 0'),
    (cell('LC7', False, 'False', value_type='boolean'), 'value', ': False'),
    (cell('LC7', None, '', state='blank', value_type='blank'), 'blank', '[blank]'),
    (cell('LC7', '', ''), 'empty_string', '[empty_string]'),
    (cell('LC7', '', '', formula='=""', cached=''), 'empty_formula_result', '[empty_formula_result]'),
    (cell('LC7', None, '', state='formula_missing', formula='=1+1'), 'formula_missing', '[formula_result_missing]'),
    (cell('LC7', '#N/A', '#N/A', state='error', value_type='error'), 'error', '#N/A'),
])
def test_cell_state_is_explicit_and_does_not_confuse_blank_zero_false_or_formula(target, state, text):
    generation, hit = setup(target)
    result = derived(project(generation, [hit]))
    assert len(result) == 1 and text in result[0].payload['content']
    proof = result[0].payload['_table_cell_states'][0]
    assert proof['state'] == state and proof['address'] == 'LC7'
    assert proof['generation_id'] == generation and proof['table_id'] == TABLE


@pytest.mark.parametrize('query', [
    QUERY.replace('7601', '7609'), QUERY.replace('строка 7', 'строка 4'),
    QUERY.replace('LC', 'LD'), QUERY.replace('Контроль', 'Чужой лист'),
    QUERY.replace('Значение', 'Несуществующее поле'), QUERY.replace('строка 7', 'строка 0'),
    QUERY.replace('строка 7', 'строка 1048577'), QUERY.replace('LC', 'XFE'),
    QUERY.replace(' (строка 7)', ''), QUERY.replace(' (колонка LC)', ''),
    'Что означает код 7601?', QUERY + ' и строка 3',
])
def test_ambiguous_mismatched_or_incomplete_cell_request_keeps_existing_hits(query):
    generation, hit = setup()
    assert project(generation, [hit], query) == [hit]


@pytest.mark.parametrize('fault', ['generation', 'source', 'unverified', 'incomplete', 'unknown_header', 'merged_nonanchor', 'bad_cell_address', 'malformed_cell'])
def test_cell_context_never_crosses_canonical_source_or_readiness(fault):
    generation, hit = setup()
    if fault == 'generation': hit.payload['table_refs'][0]['generation_id'] = 'a' * 32
    elif fault == 'source': hit.payload['source_id'] = 'root/1'
    elif fault == 'unverified': hit.payload['_canonical_verified'] = False
    else:
        with session_scope() as session:
            table = session.get(DocumentTable, (DOC, generation, TABLE))
            if fault == 'incomplete': table.quality = {'complete': False}
            elif fault == 'unknown_header':
                descriptor = deepcopy(table.descriptor); descriptor['columns'][-1]['header_path'] = []; table.descriptor = descriptor
            elif fault == 'merged_nonanchor':
                descriptor = deepcopy(table.descriptor); descriptor['merges'] = [{'sheet_index': 0, 'min_row': 6, 'max_row': 7, 'min_column': 315, 'max_column': 315}]; table.descriptor = descriptor
            else:
                row = session.get(DocumentTableRow, (DOC, generation, TABLE, 7))
                values = deepcopy(row.cells)
                if fault == 'bad_cell_address': values['315']['address'] = 'LC6'
                else: values['315'] = 'corrupt cell'
                row.cells = values
    assert project(generation, [hit]) == [hit]


def test_omitted_cell_is_explicitly_blank_only_in_complete_existing_keyed_row():
    generation, hit = setup()
    with session_scope() as session:
        row = session.get(DocumentTableRow, (DOC, generation, TABLE, 7))
        cells = deepcopy(row.cells); del cells['315']; row.cells = cells
    result = derived(project(generation, [hit]))
    assert len(result) == 1 and 'LC7 | Значение: [blank]' in result[0].payload['content']
    assert result[0].payload['_table_cell_states'][0]['state'] == 'blank'


@pytest.mark.parametrize('target', [
    cell('LC7', None, '', state='blank', formula='=1+1'),
    cell('LC7', 'not empty', '', state='blank'),
    cell('LC7', None, '', state='value'),
    cell('LC7', None, '', state='formula_missing'),
    cell('LC7', 'answer', 'answer', formula={'invalid': 'type'}),
    cell('LC7', {'nested': 'bad'}, 'answer'),
    cell('LC7', 1, 1),
    cell('LC7', 'x' * 20000, 'x' * 20000),
])
def test_inconsistent_or_oversized_canonical_cell_does_not_create_context(target):
    generation, hit = setup(target)
    assert project(generation, [hit]) == [hit]


def test_missing_or_erroneous_row_key_does_not_confirm_target():
    generation, hit = setup()
    with session_scope() as session:
        row = session.get(DocumentTableRow, (DOC, generation, TABLE, 7))
        cells = deepcopy(row.cells); cells['1']['state'] = 'error'; row.cells = cells
    assert project(generation, [hit]) == [hit]


def test_context_budget_is_not_satisfied_by_silently_clipping_value():
    from types import SimpleNamespace
    generation, hit = setup()
    assert project(generation, [hit], settings=SimpleNamespace(table_context_tokens=16)) == [hit]


def test_overlap_between_visible_tables_does_not_guess_one_source():
    generation, hit = setup()
    other_id = 'e' * 32
    with session_scope() as session:
        table = session.get(DocumentTable, (DOC, generation, TABLE))
        row = session.get(DocumentTableRow, (DOC, generation, TABLE, 7))
        session.add(DocumentTable(doc_id=DOC, generation_id=generation, table_id=other_id,
            source_id='root/0', descriptor=deepcopy(table.descriptor), quality={'complete': True}))
        session.flush()
        session.add(DocumentTableRow(doc_id=DOC, generation_id=generation, table_id=other_id,
            row_number=7, cells=deepcopy(row.cells)))
    second = deepcopy(hit)
    second.payload['table_refs'][0]['table_id'] = other_id
    assert project(generation, [hit, second]) == [hit, second]


def test_unrequested_wide_row_cells_are_not_returned_by_sql_projection():
    from sqlalchemy import event
    generation, hit = setup()
    with session_scope() as session:
        row = session.get(DocumentTableRow, (DOC, generation, TABLE, 7))
        cells = deepcopy(row.cells); cells['100'] = cell('CV7', 'x' * 100000, 'x' * 100000); row.cells = cells
        engine = session.get_bind()
    projected_shapes = []
    def observed(connection, cursor, statement, parameters, context, executemany):
        if 'json_each' in statement:
            projected_shapes.append([item[0] for item in cursor.description])
    event.listen(engine, 'after_cursor_execute', observed)
    try:
        result = derived(project(generation, [hit]))
    finally:
        event.remove(engine, 'after_cursor_execute', observed)
    assert len(result) == 1 and 'answer' in result[0].payload['content']
    assert projected_shapes and all(len(shape) == 3 and 'cells' not in shape for shape in projected_shapes)


def test_ooxml_formula_text_without_equal_prefix_preserves_missing_cache_state():
    generation, hit = setup(cell('LC7', None, '', state='formula_missing', formula='SUM(A1:A2)'))
    result = derived(project(generation, [hit]))
    assert len(result) == 1
    assert result[0].payload['_table_cell_states'][0]['state'] == 'formula_missing'
    assert '[formula_result_missing]' in result[0].payload['content']


@pytest.mark.parametrize('target, expected', [
    (cell('LC7', 'text without digits', 'text without digits'), 'text without digits'),
    (cell('LC7', 0, '0', value_type='number'), '0'),
    (cell('LC7', False, 'False', value_type='boolean'), 'False'),
    (cell('LC7', '', ''), ''),
    (cell('LC7', None, '', state='formula_missing', formula='SUM(A1:A2)'), ''),
])
def test_postgres_reads_general_typed_cells_independently_of_identifier_projection(target, expected):
    import json
    import os
    from types import SimpleNamespace
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    url = os.environ.get('TABLE_CELL_TEST_POSTGRES_URL')
    if not url:
        pytest.skip('Requires a separately authorized private PostgreSQL test endpoint')
    from sqlalchemy.engine import make_url
    parsed = make_url(url)
    assert parsed.host in ('127.0.0.1', 'localhost') and parsed.port == 15432 and parsed.database.startswith('r2_')
    function = getattr(table_retrieval, 'project_requested_table_cells', None)
    assert callable(function), 'bounded query-derived canonical cell context is missing'
    # The reader is exercised through the public helper in the other tests.
    # Here a temporary table shadows only this connection, so SQL projection
    # semantics are checked without touching any published private or main row.
    reader = function.__globals__.get('_read_cells')
    if reader is None:
        from app.services.table_cell_context import _read_cells as reader
    engine = create_engine(url)
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text('CREATE TEMP TABLE document_table_rows (doc_id TEXT, generation_id TEXT, table_id TEXT, row_number INTEGER, cells JSON, cells_projection JSONB) ON COMMIT DROP'))
            connection.execute(text('INSERT INTO document_table_rows VALUES (:doc, :generation, :table, 7, CAST(:cells AS JSON), CAST(:identifiers AS JSONB))'),
                {'doc': DOC, 'generation': 'a' * 32, 'table': TABLE,
                 'cells': json.dumps({'1': cell('A7', '7601', '7601'), '315': target, '100': cell('CV7', 'x' * 100000, 'x' * 100000)}),
                 'identifiers': json.dumps([{'key': '1', 'value': cell('A7', '7601', '7601')}])})
            with Session(bind=connection) as session:
                table = SimpleNamespace(doc_id=DOC, generation_id='a' * 32, table_id=TABLE)
                values = reader(session, table, 7, [1, 315])
                assert values[315]['display_text'] == expected
                assert values[315]['raw_value'] == target['raw_value']
                assert values[315]['state'] == target['state']
                assert 100 not in values
    finally:
        engine.dispose()


def test_exact_cell_proof_reaches_merged_context_and_saved_source():
    from app.services.context_builder import merge_and_format
    from app.services.chat_source_selection import snapshot_blocks, restore_blocks
    from app.db.models import DocumentGeneration
    generation, hit = setup(cell('LC7', '', ''))
    with session_scope() as session:
        session.get(DocumentGeneration, generation).phase = 'active'
    expanded = project(generation, [hit])
    blocks = merge_and_format(expanded, query=QUERY)
    exact = [b for b in blocks if b.get('_table_cell_states')]
    assert len(exact) == 1 and '[empty_string]' in exact[0]['content']
    saved = snapshot_blocks(exact)
    restored = restore_blocks(saved, mail_mode='all')
    assert restored[0]['content'] == exact[0]['content']
    assert restored[0]['table_refs'] == exact[0]['table_refs']
    assert restored[0]['_table_cell_states'] == exact[0]['_table_cell_states']


def test_index_payload_cannot_claim_an_explicit_canonical_cell_state():
    generation, hit = setup()
    hit.payload['_table_cell_states'] = [{'address': 'LC7', 'state': 'blank'}]
    with session_scope() as session:
        result = table_retrieval.hydrate_table_fragments([hit],
            {'session': session, 'active_generations': {DOC: generation}, 'canonical_values': {}, 'query': 'generic query'})
    assert len(result) == 1 and not result[0].payload.get('_table_cell_states')


def test_explicit_row_cue_selects_proven_cell_before_prose_and_preserves_other_order():
    from app.services.context_builder import merge_and_format
    generation, hit = setup()
    hit.score = .01
    expanded = project(generation, [hit])
    prose = Hit('prose', .2, {'doc_id': 'other-doc', 'point_type': 'chunk', 'chunk_index': 3,
        'content': 'ordinary prose', '_canonical_verified': True})
    blocks = merge_and_format([prose, *expanded], query=QUERY)
    assert blocks[0].get('_table_cell_states')
    assert blocks[0]['table_refs'][0]['row_number'] == 7
    assert blocks[1]['doc_id'] == 'other-doc'
    assert [b['score'] for b in blocks if b.get('table_refs')] == [.01, .01]


def test_generic_query_keeps_original_table_order_and_has_no_extra_database_read():
    generation, hit = setup()
    class NoReadSession:
        def get(self, *args, **kwargs):
            raise AssertionError('Generic query must not read extra canonical cells')
    function = getattr(table_retrieval, 'project_requested_table_cells', None)
    assert callable(function), 'bounded query-derived canonical cell context is missing'
    assert function([hit], {'session': NoReadSession(), 'active_generations': {DOC: generation}, 'query': 'Что означает код 7601?'}) == [hit]


@pytest.mark.parametrize('fault', ['missing_header_rows', 'missing_anchor', 'outside_header_anchor', 'coordinate_only'])
def test_derived_business_field_requires_an_original_header_anchor(fault):
    generation, hit = setup()
    query = QUERY
    with session_scope() as session:
        table = session.get(DocumentTable, (DOC, generation, TABLE))
        descriptor = deepcopy(table.descriptor)
        if fault == 'missing_header_rows': descriptor['header_rows'] = []
        elif fault == 'outside_header_anchor': descriptor['columns'][-1]['header_addresses'] = ['LC99']
        else:
            descriptor['columns'][-1]['header_addresses'] = []
            if fault == 'coordinate_only':
                descriptor['columns'][-1]['header_path'] = ['LC']; query = QUERY.replace('Значение', 'LC')
        table.descriptor = descriptor
    assert project(generation, [hit], query) == [hit]


@pytest.mark.parametrize('literal', ['[blank]', '[empty_string]', '[formula_result_missing]'])
def test_literal_state_marker_is_identified_as_a_value_in_submitted_context(literal):
    generation, hit = setup(cell('LC7', literal, literal))
    result = derived(project(generation, [hit]))
    assert len(result) == 1 and f'LC7 | Значение: {literal}' in result[0].payload['content']
    assert 'cell state: value' in result[0].payload['content']
    assert result[0].payload['_table_cell_states'][0]['state'] == 'value'


def other_visible_sheet(generation, hit):
    other = 'e' * 32
    with session_scope() as session:
        table = session.get(DocumentTable, (DOC, generation, TABLE))
        descriptor = deepcopy(table.descriptor); descriptor['sheet_name'] = 'Другой лист'
        session.add(DocumentTable(doc_id=DOC, generation_id=generation, table_id=other,
            source_id='root/0', descriptor=descriptor, quality={'complete': True}))
    hit.payload['table_refs'][0]['table_id'] = other
    return hit


def test_explicit_cell_is_resolved_within_same_visible_source_when_other_sheet_was_retrieved():
    generation, hit = setup()
    other_visible_sheet(generation, hit)
    result = derived(project(generation, [hit]))
    assert len(result) == 1 and result[0].payload['table_refs'][0]['table_id'] == TABLE
    assert 'LC7 | Значение: answer' in result[0].payload['content']


@pytest.mark.parametrize('fault', ['hidden_source', 'other_generation', 'ambiguous_overlap', 'missing_visible_table'])
def test_visible_source_discovery_never_crosses_scope_or_guesses_ambiguous_target(fault):
    generation, hit = setup()
    other_visible_sheet(generation, hit)
    with session_scope() as session:
        table = session.get(DocumentTable, (DOC, generation, TABLE))
        if fault == 'hidden_source': table.source_id = 'root/1'
        elif fault == 'other_generation':
            descriptor = deepcopy(table.descriptor); descriptor['sheet_name'] = 'Старое имя'; table.descriptor = descriptor
            from app.db.models import DocumentGeneration
            new_generation = 'a' * 32
            session.add(DocumentGeneration(id=new_generation, doc_id=DOC, phase='retired'))
            session.flush()
            copy_descriptor = deepcopy(descriptor); copy_descriptor['sheet_name'] = 'Контроль'
            session.add(DocumentTable(doc_id=DOC, generation_id=new_generation, table_id=TABLE,
                source_id='root/0', descriptor=copy_descriptor, quality={'complete': True}))
        elif fault == 'ambiguous_overlap':
            overlap = 'f' * 32
            session.add(DocumentTable(doc_id=DOC, generation_id=generation, table_id=overlap,
                source_id='root/0', descriptor=deepcopy(table.descriptor), quality={'complete': True}))
            session.flush()
            original = session.get(DocumentTableRow, (DOC, generation, TABLE, 7))
            session.add(DocumentTableRow(doc_id=DOC, generation_id=generation, table_id=overlap,
                row_number=7, cells=deepcopy(original.cells)))
        else: hit.payload['table_refs'][0]['table_id'] = '9' * 32
    assert project(generation, [hit]) == [hit]


def test_proven_explicit_cell_survives_top_k_before_ten_unrelated_prose_blocks():
    from app.services.context_builder import merge_and_format
    generation, hit = setup(); hit.score = .001
    expanded = project(generation, [hit])
    prose = [Hit(str(i), .2-i*.001, {'doc_id': f'prose-{i}', 'point_type': 'chunk',
        'chunk_index': i, 'content': 'ordinary unrelated prose', '_canonical_verified': True}) for i in range(10)]
    blocks = merge_and_format([*prose, *expanded], query=QUERY)
    assert blocks[0].get('_table_cell_states') and blocks[0]['score'] == .001
    assert [b['doc_id'] for b in blocks if b['doc_id'].startswith('prose-')] == [f'prose-{i}' for i in range(10)]
    assert any(b.get('_table_cell_states') for b in blocks[:10])


@pytest.mark.parametrize('target,state', [
    (cell('LC7', None, '', state='blank', value_type='blank'), 'blank'),
    (cell('LC7', '[blank]', '[blank]'), 'value'),
    (cell('LC7', '', ''), 'empty_string'),
])
def test_submitted_state_is_bound_to_each_cell_instead_of_the_whole_row(target, state):
    generation, hit = setup(target)
    result = derived(project(generation, [hit]))
    assert len(result) == 1
    content = result[0].payload['content']
    assert content.splitlines()[0] == 'Sheet: Контроль; row: 7'
    target_line, key_line = content.splitlines()[1:]
    assert target_line.endswith(f'(cell state: {state})')
    assert key_line == 'A7 | Код: 7601 (cell state: value)'
    assert result[0].payload['_table_cell_states'][0]['state'] == state


@pytest.mark.parametrize("change", [
    {"declared": False}, {"declared": 1}, {"method": "unresolved"},
    {"normalizer_version": 3}, {"normalizer_version": "4"}, {"header_rows": None},
])
def test_headerless_cell_context_requires_explicit_current_declaration(change):
    generation, hit = setup()
    with session_scope() as session:
        table = session.query(DocumentTable).filter_by(doc_id=DOC, generation_id=generation).one()
        descriptor = deepcopy(table.descriptor)
        descriptor.update(header_rows=[], declared=True, method="explicit", normalizer_version=4)
        for column in descriptor["columns"]:
            column["header_addresses"] = []
        descriptor.update(change)
        table.descriptor = descriptor
    assert not derived(project(generation, [hit]))
