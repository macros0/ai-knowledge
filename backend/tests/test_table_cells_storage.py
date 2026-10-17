"""Binary PostgreSQL cells, reversible migration, and portable projection."""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql, sqlite

from app.db.models import DocumentTableRow
from app.services.table_retrieval import _read_projected_rows

REVISION = Path(__file__).resolve().parents[1] / 'alembic/versions/060b1c2d3e4f_table_cells_jsonb.py'
VALUES = [{"1": {"display_text": "0"}, "2": {"display_text": "False"},
           "3": {"display_text": "текст | \n"}, "9": {"display_text": "private unrequested"}},
          {}, None, [], False, {"1": "junk", "2": '{"display_text":"3100"}', "3": False},
          {"1": {"display_text": None}},
          {"1": {"raw_value": 1e20, "cached_value": -0.0, "display_text": None}},
          {"1": {"raw_value": 1e-20, "cached_value": 1e20, "display_text": None}},
          {"1": {"display_text": "3100"}, "2": {"display_text": "003100"},
           "3": {"display_text": "-3100"}, "4": {"display_text": "3100.0"}},
          [{"display_text": "3100"}, {"display_text": "003100"}],
          {"1": {"display_text": " 003100 "}, "2": {"display_text": "\t3100\n"},
           "3": {"display_text": "\u00a0003100\u00a0"}, "4": {"display_text": "3100-other"}}]


def revision():
    spec = importlib.util.spec_from_file_location('table_cells_revision', REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fresh_cells_storage_avoids_postgres_text_reparse_and_keeps_sqlite_json():
    column = DocumentTableRow.__table__.c.cells
    assert column.type.compile(dialect=postgresql.dialect()) == 'JSON'
    projection = DocumentTableRow.__table__.c.cells_projection
    assert projection.type.compile(dialect=postgresql.dialect()) == 'JSONB'
    assert column.type.compile(dialect=sqlite.dialect()) == 'JSON'


def exercise(connection):
    import json
    connection.execute(text('CREATE TABLE document_table_rows (doc_id varchar(16), '
        'generation_id varchar(32), table_id varchar(32), row_number integer, cells JSON NOT NULL, '
        'hidden BOOLEAN NOT NULL, PRIMARY KEY(doc_id,generation_id,table_id,row_number))'))
    for number, value in enumerate(VALUES, 1):
        if connection.dialect.name == 'postgresql':
            sql = 'INSERT INTO document_table_rows VALUES (:doc,:gen,:table,:row,CAST(:cells AS JSON),:hidden)'
        else:
            sql = 'INSERT INTO document_table_rows VALUES (:doc,:gen,:table,:row,:cells,:hidden)'
        connection.execute(text(sql), {'doc': 'storage-control', 'gen': 'a'*32, 'table': 'b'*32,
            'row': number, 'cells': json.dumps(value,ensure_ascii=False), 'hidden': number%2==0})
    def snapshot():
        data = connection.execute(text('SELECT doc_id,generation_id,table_id,row_number,cells,hidden '
                                       'FROM document_table_rows ORDER BY row_number')).all()
        return [json.dumps([*row[:4], json.loads(row[4]) if isinstance(row[4],str) else row[4],bool(row[5])],ensure_ascii=False,sort_keys=True,separators=(',',':')) for row in data]
    original = snapshot()
    module = revision()
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
        assert snapshot() == original
        actual = inspect(connection).get_columns('document_table_rows')
        assert str(next(c['type'] for c in actual if c['name']=='cells')) == 'JSON'
        assert str(next(c['type'] for c in actual if c['name']=='cells_projection')) == (
            'JSONB' if connection.dialect.name == 'postgresql' else 'JSON')
        if connection.dialect.name == 'postgresql':
            from sqlalchemy.orm import Session
            session = Session(bind=connection)
            keys = [('storage-control','a'*32,'b'*32,n) for n in range(1,len(VALUES)+1)]
            projected = _read_projected_rows(session,[(keys+[(*keys[0][:3],999)], [1,2,3])])
            assert projected[keys[0]] == {1:'0',2:'False',3:'текст | \n'}
            assert all(projected[k]=={1:None,2:None,3:None} for k in keys[1:9]+keys[10:11])
            assert (*keys[0][:3],999) not in projected
            wrong = [('other',*keys[0][1:]),(keys[0][0],'c'*32,*keys[0][2:]),
                     (*keys[0][:2],'c'*32,keys[0][3])]
            assert _read_projected_rows(session,[(wrong,[1])]) == {}
            assert projected[keys[9]] == {1:'3100',2:'003100',3:'-3100'}
            from app.services import table_retrieval
            identifiers = table_retrieval._read_identifier_rows(session,[(keys+[(*keys[0][:3],999)], [1,2,3,4])])
            assert identifiers[keys[11]] == {1:' 003100 ',2:'\t3100\n',3:'\u00a0003100\u00a0',4:'3100-other'}
            assert identifiers[keys[9]] == {1:'3100',2:'003100',3:'-3100',4:'3100.0'}
            assert all(identifiers[k]=={1:None,2:None,3:None,4:None} for k in keys[:9]+keys[10:11])
            assert (*keys[0][:3],999) not in identifiers
            raw_projection = connection.execute(text('SELECT cells_projection FROM document_table_rows ORDER BY row_number')).scalars().all()
            assert all(p==[] for p in raw_projection[:9]+raw_projection[10:11])
            assert [v['key'] for v in raw_projection[9]] == ['1','2','3','4']
            direct = _read_projected_rows(session,[(keys,[1,2,3,4])])
            def valid_codes(rows):
                return {(key,column,value.strip()) for key,fields in rows.items()
                        for column,value in fields.items()
                        if value is not None and table_retrieval._CELL_IDENTIFIER.fullmatch(value.strip())}
            assert valid_codes(identifiers) == valid_codes(direct)
            assert len(valid_codes(identifiers)) == 5
            session.close()
        module.downgrade()
        assert snapshot() == original
        module.upgrade()
        assert snapshot() == original


def test_cells_revision_roundtrip_on_sqlite(tmp_path):
    engine = create_engine('sqlite:///'+str(tmp_path/'cells.db'))
    with engine.begin() as connection:
        exercise(connection)
    engine.dispose()


def test_cells_revision_roundtrip_and_projection_on_private_postgres():
    url = os.getenv('TABLE_CELLS_TEST_POSTGRES_URL')
    if not url:
        pytest.skip('Private PostgreSQL connection not configured')
    schema = 'table_cells_test_'+uuid4().hex
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        try:
            exercise(connection)
        finally:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


def test_fresh_private_postgres_projection_does_not_change_row_digest_or_load_wide_defaults():
    url = os.getenv('TABLE_CELLS_TEST_POSTGRES_URL')
    if not url:
        pytest.skip('Private PostgreSQL connection not configured')
    from dataclasses import asdict
    from hashlib import sha256
    from sqlalchemy import event, select
    from sqlalchemy.orm import Session
    from app.db.models import Base, Document, DocumentTable
    from app.services.generation_store import begin_generation
    from app.services.table_store import verify_generation_tables
    from docparser.table_model import TableCell, TableRow, TableSummary
    from docparser.xlsx_structure import row_bytes
    schema = 'table_cells_fresh_'+uuid4().hex
    engine = create_engine(url)
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if 'INSERT INTO document_table_rows' in statement:
            statements.append(statement)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        try:
            Base.metadata.create_all(connection)
            session = Session(bind=connection)
            session.add(Document(id='fresh-storage',filename='control.xlsx',status='done'))
            session.flush()
            generation = begin_generation(session,'fresh-storage').id
            cell = TableCell(address='A2',raw_value=1e20,display_text=' 003100 ',value_type='number',cached_value=-0.0)
            canonical = TableRow(2,{1:cell})
            summary = TableSummary(1,1,sha256(row_bytes(canonical)+b'\n').hexdigest())
            session.add(DocumentTable(doc_id='fresh-storage',generation_id=generation,table_id='b'*32,
                source_id='root',descriptor={},quality=asdict(summary)))
            session.flush()
            event.listen(connection,'before_cursor_execute',capture)
            stored = DocumentTableRow(doc_id='fresh-storage',generation_id=generation,table_id='b'*32,
                                      row_number=2,cells={'1':asdict(cell)})
            session.add(stored)
            session.flush()
            event.remove(connection,'before_cursor_execute',capture)
            assert statements and all('cells_projection' not in sql for sql in statements)
            assert 'cells_projection' not in stored.__dict__
            session.expire_all()
            loaded = session.scalars(select(DocumentTableRow)).one()
            assert 'cells_projection' not in loaded.__dict__
            assert verify_generation_tables(session,'fresh-storage',generation)[0]['digest'] == summary.digest
            from app.services.table_retrieval import _read_identifier_rows
            key=('fresh-storage',generation,'b'*32,2)
            assert _read_identifier_rows(session,[([key],[1])]) == {key:{1:' 003100 '}}
            session.close()
        finally:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()
