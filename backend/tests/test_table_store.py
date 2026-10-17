"""Canonical tables obey the existing candidate/active lifecycle."""

import pytest
from openpyxl import Workbook

from app.db.models import Document
from app.db.session import session_scope
from app.services.generation_store import begin_generation, mark_generation_ready, publish_generation, get_generation_state
from docparser import parse_document_result

DOC = 'tables-doc-1'


def candidate():
    with session_scope() as s:
        if s.get(Document, DOC) is None:
            s.add(Document(id=DOC, filename='tables.xlsx', status='done'))
            s.flush()
        return begin_generation(s, DOC).id


def write(tmp_path, generation, value=7):
    from app.services.table_store import GenerationTableSink
    wb = Workbook()
    ws = wb.active
    ws.append(['Code', 'Value'])
    ws.append(['same', value])
    ws.append(['same', value])
    source = tmp_path / (generation + '.xlsx')
    wb.save(source)
    result = parse_document_result(source, table_sink=GenerationTableSink(DOC, generation))
    return next(b.meta['table_descriptor']['table_id'] for b in result.blocks if b.type == 'table')


def activate(generation):
    with session_scope() as s:
        mark_generation_ready(s, DOC, generation)
        publish_generation(s, DOC, generation)


def test_candidate_tables_are_invisible_and_rows_keep_identity(tmp_path):
    from app.services.table_store import get_table_rows, TableUnavailable
    generation = candidate()
    table = write(tmp_path, generation)
    with session_scope() as s, pytest.raises(TableUnavailable):
        get_table_rows(s, DOC, generation, table, [2, 3])
    activate(generation)
    with session_scope() as s:
        rows = get_table_rows(s, DOC, generation, table, [2, 3])
        assert [r.row_number for r in rows] == [2, 3]
        assert [r.cells[2].raw_value for r in rows] == [7, 7]


def test_generations_never_mix_and_old_snapshot_keeps_old_values(tmp_path):
    from app.services.table_store import get_table_rows, TableUnavailable
    first = candidate()
    table = write(tmp_path, first, 7)
    activate(first)
    second = candidate()
    second_table = write(tmp_path, second, 9)
    activate(second)
    with session_scope() as s:
        assert get_table_rows(s, DOC, first, table, [2])[0].cells[2].raw_value == 7
        assert get_table_rows(s, DOC, second, second_table, [2])[0].cells[2].raw_value == 9
        s.get(Document, DOC).deleted_at = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
    with session_scope() as s, pytest.raises(TableUnavailable):
        get_table_rows(s, DOC, first, table, [2])


def test_resume_is_idempotent_and_digest_detects_a_missing_cell(tmp_path):
    from app.db.models import DocumentTableRow
    from app.services.table_store import verify_generation_tables
    from app.services.generation_artifacts import GenerationIntegrityError
    generation = candidate()
    write(tmp_path, generation)
    write(tmp_path, generation)
    with session_scope() as s:
        assert s.query(DocumentTableRow).count() == 3
        assert verify_generation_tables(s, DOC, generation)[0]['cell_count'] == 6
        row = s.query(DocumentTableRow).filter_by(row_number=2).one()
        changed = dict(row.cells)
        changed.pop('2')
        row.cells = changed
    with session_scope() as s, pytest.raises(GenerationIntegrityError):
        mark_generation_ready(s, DOC, generation)


def test_incomplete_sink_and_tampered_ready_preserve_active_pointer(tmp_path):
    from app.db.models import DocumentTable
    from app.services.generation_artifacts import GenerationIntegrityError
    first = candidate()
    write(tmp_path, first)
    activate(first)
    second = candidate()
    write(tmp_path, second)
    with session_scope() as s:
        mark_generation_ready(s, DOC, second)
        table = s.query(DocumentTable).filter_by(generation_id=second).one()
        table.quality = {**table.quality, 'complete': False}
    with session_scope() as s, pytest.raises(GenerationIntegrityError):
        publish_generation(s, DOC, second)
    with session_scope() as s:
        assert get_generation_state(s, DOC).active_generation_id == first


def test_export_import_roundtrip_checksums_and_coordinates(tmp_path):
    from app.services.table_store import export_generation_tables, import_generation_tables, get_table_rows
    generation = candidate()
    table = write(tmp_path, generation)
    bundle = tmp_path / 'portable'
    with session_scope() as s:
        export_generation_tables(s, DOC, generation, bundle)
    activate(generation)
    new = candidate()
    import_generation_tables(DOC, new, bundle)
    activate(new)
    with session_scope() as s:
        row = get_table_rows(s, DOC, new, table, [3])[0]
        assert row.cells[2].address == 'B3' and row.cells[2].raw_value == 7
    rows_path = next(bundle.glob('tables/*.jsonl'))
    rows_path.write_text(rows_path.read_text() + '{}\n')
    another = candidate()
    with pytest.raises(ValueError, match='checksum'):
        import_generation_tables(DOC, another, bundle)

def test_publication_rejects_missing_projection_and_changed_descriptor(tmp_path):
    from app.services.table_store import export_generation_tables, verify_table_publication
    from app.services.generation_artifacts import GenerationIntegrityError
    from app.db.models import DocumentTable
    generation=candidate(); table=write(tmp_path,generation)
    bundle=tmp_path/'bundle'
    with session_scope() as s:
        export_generation_tables(s,DOC,generation,bundle)
        with pytest.raises(GenerationIntegrityError):
            verify_table_publication(s,DOC,generation,bundle,[])
        refs=[{'kind':'table','generation_id':generation,'source_id':'root','table_id':table,'row_number':row,'columns':[1,2]} for row in (1,2,3)]
        verify_table_publication(s,DOC,generation,bundle,[{'table_refs':refs}])
        stored=s.get(DocumentTable,(DOC,generation,table)); d=dict(stored.descriptor); d['sheet_name']='Changed'; stored.descriptor=d
        with pytest.raises(GenerationIntegrityError):
            verify_table_publication(s,DOC,generation,bundle,[{'table_refs':refs}])
