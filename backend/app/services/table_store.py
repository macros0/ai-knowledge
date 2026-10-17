"""Generation-scoped canonical tables, bounded writers and portable artifacts."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re

from sqlalchemy import select

from app.db.models import Document, DocumentGeneration, DocumentTable, DocumentTableRow
from app.db.session import session_scope
from app.services.generation_artifacts import GenerationIntegrityError, file_digest
from app.services.generation_store import lock_writable_generation, lock_generation_read
from docparser.table_model import CellRange, TableCell, TableColumn, TableDescriptor, TableRow, TableSummary
from docparser.xlsx_structure import row_bytes


class TableUnavailable(LookupError):
    """No visible table of the requested document/source/generation."""


def descriptor_from_dict(value):
    data = dict(value)
    data['cell_range'] = CellRange(**data['cell_range'])
    data['merges'] = [CellRange(**m) for m in data.get('merges', [])]
    data['columns'] = [TableColumn(**c) for c in data.get('columns', [])]
    return TableDescriptor(**data)


def row_from_dict(value):
    return TableRow(int(value['row_number']), {int(c): TableCell(**v) for c, v in value['cells'].items()},
                    bool(value.get('hidden', False)))


def _row(row):
    return row_from_dict({'row_number': row.row_number, 'cells': row.cells, 'hidden': row.hidden})


def _table(session, doc_id, generation_id, table_id):
    return session.get(DocumentTable, (doc_id, generation_id, table_id))


def get_document_table(session, doc_id, generation_id, table_id, *, source_ids=None):
    """Caller authorizes document ACL first; this enforces publication/trash scope."""
    lock_generation_read(session, [doc_id])
    document = session.get(Document, doc_id, populate_existing=True)
    generation = session.get(DocumentGeneration, generation_id, populate_existing=True)
    if (document is None or document.deleted_at is not None or generation is None
            or generation.doc_id != doc_id or generation.phase not in {'active', 'retired'}):
        raise TableUnavailable()
    table = _table(session, doc_id, generation_id, table_id)
    if table is None or not table.quality.get('complete') or (source_ids is not None and table.source_id not in source_ids):
        raise TableUnavailable()
    return table


def get_table_rows(session, doc_id, generation_id, table_id, row_numbers, *, source_ids=None):
    get_document_table(session, doc_id, generation_id, table_id, source_ids=source_ids)
    numbers = list(dict.fromkeys(row_numbers))
    if len(numbers) > 100 or any(type(n) is not int or not 1 <= n <= 1_048_576 for n in numbers):
        raise ValueError('Invalid table row range')
    rows = session.scalars(select(DocumentTableRow).where(
        DocumentTableRow.doc_id == doc_id, DocumentTableRow.generation_id == generation_id,
        DocumentTableRow.table_id == table_id, DocumentTableRow.row_number.in_(numbers),
    ).order_by(DocumentTableRow.row_number))
    return [_row(r) for r in rows]


def _stored_rows(session, doc_id, generation_id, table_id):
    statement = select(DocumentTableRow).where(DocumentTableRow.doc_id == doc_id,
        DocumentTableRow.generation_id == generation_id, DocumentTableRow.table_id == table_id).order_by(DocumentTableRow.row_number)
    return session.scalars(statement.execution_options(yield_per=256))


def _summary(session, doc_id, generation_id, table_id):
    digest, count, cells = sha256(), 0, 0
    for stored in _stored_rows(session, doc_id, generation_id, table_id):
        row = _row(stored)
        digest.update(row_bytes(row) + b'\n')
        count += 1
        cells += sum(c.state != 'blank' and (c.formula is not None or c.raw_value != '') for c in row.cells.values())
    return TableSummary(count, cells, digest.hexdigest())


def verify_generation_tables(session, doc_id, generation_id):
    result = []
    for table in session.scalars(select(DocumentTable).where(DocumentTable.doc_id == doc_id,
            DocumentTable.generation_id == generation_id).order_by(DocumentTable.table_id)):
        actual = asdict(_summary(session, doc_id, generation_id, table.table_id))
        expected = {key: table.quality.get(key) for key in actual}
        if actual != expected:
            raise GenerationIntegrityError()
        result.append({'table_id': table.table_id, **actual})
    return result


class GenerationTableSink:
    """Each row batch commits independently, but only to the current candidate."""
    def __init__(self, doc_id, generation_id):
        self.doc_id, self.generation_id = doc_id, generation_id

    def begin_table(self, descriptor):
        if not re.fullmatch('[0-9a-f]{32}', descriptor.table_id):
            raise ValueError('Invalid table identity')
        with session_scope() as session:
            lock_writable_generation(session, self.doc_id, self.generation_id)
            table = _table(session, self.doc_id, self.generation_id, descriptor.table_id)
            if table is None:
                table = DocumentTable(doc_id=self.doc_id, generation_id=self.generation_id, table_id=descriptor.table_id,
                                      source_id=descriptor.source_id, schema_version=descriptor.schema_version,
                                      descriptor=asdict(descriptor), quality={'complete': False})
                session.add(table)
            else:
                if table.descriptor != asdict(descriptor):
                    raise ValueError('Candidate table structure changed')
                table.quality = {'complete': False}
                session.query(DocumentTableRow).filter_by(doc_id=self.doc_id, generation_id=self.generation_id,
                                                           table_id=descriptor.table_id).delete(synchronize_session=False)

    def append_rows(self, table_id, rows):
        if len(rows) > 256:
            raise ValueError('Table batch row safety limit exceeded')
        with session_scope() as session:
            lock_writable_generation(session, self.doc_id, self.generation_id)
            table = _table(session, self.doc_id, self.generation_id, table_id)
            if table is None or table.quality.get('complete'):
                raise ValueError('Table is not writable')
            area = CellRange(**table.descriptor['cell_range'])
            for row in rows:
                if not area.min_row <= row.row_number <= area.max_row:
                    raise ValueError('Row is outside table')
                for column, cell in row.cells.items():
                    from openpyxl.utils.cell import coordinate_to_tuple
                    if (not area.contains(row.row_number, column)
                            or coordinate_to_tuple(cell.address) != (row.row_number, column)):
                        raise ValueError('Cell is outside table')
                data = {str(k): asdict(v) for k, v in row.cells.items()}
                previous = session.get(DocumentTableRow, (self.doc_id, self.generation_id, table_id, row.row_number))
                if previous is None:
                    session.add(DocumentTableRow(doc_id=self.doc_id, generation_id=self.generation_id, table_id=table_id,
                                                row_number=row.row_number, cells=data, hidden=row.hidden))
                elif previous.cells != data or previous.hidden != row.hidden:
                    raise ValueError('Candidate row changed on resume')

    def finish_table(self, table_id, summary):
        with session_scope() as session:
            lock_writable_generation(session, self.doc_id, self.generation_id)
            table = _table(session, self.doc_id, self.generation_id, table_id)
            if table is None or _summary(session, self.doc_id, self.generation_id, table_id) != summary:
                raise GenerationIntegrityError()
            table.quality = {**asdict(summary), 'header_status': 'ambiguous' if table.descriptor['method'] == 'unresolved' else 'verified'}


def export_generation_tables(session, doc_id, generation_id, bundle: Path):
    from app.services.generation_files import _reject_links
    from app.services.json_atomic import write_json_atomic
    verify_generation_tables(session, doc_id, generation_id)
    folder = bundle / 'tables'
    _reject_links(folder)
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {'schema_version': 1, 'tables': []}
    for table in session.scalars(select(DocumentTable).where(DocumentTable.doc_id == doc_id,
                                 DocumentTable.generation_id == generation_id).order_by(DocumentTable.table_id)):
        path = folder / (table.table_id + '.jsonl')
        _reject_links(path)
        with path.open('wb') as stream:
            for stored in _stored_rows(session, doc_id, generation_id, table.table_id):
                stream.write(row_bytes(_row(stored)) + b'\n')
        manifest['tables'].append({'descriptor': table.descriptor, 'quality': table.quality,
                                   'rows_file': path.name, 'sha256': file_digest(path)})
    write_json_atomic(folder / 'manifest.json', manifest)
    return manifest


def import_generation_tables(doc_id, generation_id, bundle: Path):
    from app.services.generation_files import _reject_links
    folder = bundle / 'tables'
    _reject_links(folder / 'manifest.json')
    raw = (folder / 'manifest.json').read_bytes()
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('Table manifest exceeds safety limit')
    manifest = json.loads(raw)
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported table manifest')
    sink = GenerationTableSink(doc_id, generation_id)
    # Verify every file before making the first DB write.
    for entry in manifest['tables']:
        name = entry['rows_file']
        if not re.fullmatch(r'[0-9a-f]{32}\.jsonl', name) or name != entry['descriptor']['table_id'] + '.jsonl':
            raise ValueError('Invalid table artifact path')
        path = folder / name
        _reject_links(path)
        if file_digest(path) != entry['sha256']:
            raise ValueError('Table artifact checksum mismatch')
    for entry in manifest['tables']:
        descriptor = descriptor_from_dict(entry['descriptor'])
        sink.begin_table(descriptor)
        batch, size = [], 0
        with (folder / entry['rows_file']).open('rb') as stream:
            for raw in stream:
                if len(raw) > 256 * 1024 * 1024:
                    raise ValueError('Table row safety limit exceeded')
                if batch and (len(batch) == 256 or size + len(raw) > 1024 * 1024):
                    sink.append_rows(descriptor.table_id, batch)
                    batch, size = [], 0
                batch.append(row_from_dict(json.loads(raw)))
                size += len(raw)
        if batch:
            sink.append_rows(descriptor.table_id, batch)
        sink.finish_table(descriptor.table_id, TableSummary(**{k: entry['quality'][k] for k in ('row_count', 'cell_count', 'digest', 'complete')}))


def verify_table_publication(session, doc_id, generation_id, bundle, records):
    """Verify portable/SQL agreement and every physical cell's projection cover.

    The generation's immutable artifact manifest separately verifies file hashes.
    Here descriptors, counts and refs are tied back to its canonical SQL rows.
    """
    from collections import defaultdict
    from app.models.schemas import TableRef
    tables = list(session.scalars(select(DocumentTable).where(DocumentTable.doc_id == doc_id,
                                 DocumentTable.generation_id == generation_id)))
    if not tables:
        if any(r.get('table_refs') for r in records):
            raise GenerationIntegrityError()
        return
    verify_generation_tables(session, doc_id, generation_id)
    try:
        manifest = json.loads((bundle / 'tables' / 'manifest.json').read_text(encoding='utf8'))
        entries = {e['descriptor']['table_id']: e for e in manifest['tables']}
        if len(entries) != len(manifest['tables']) or set(entries) != {t.table_id for t in tables}:
            raise GenerationIntegrityError()
        coverage = defaultdict(lambda: defaultdict(list))
        source_by_table = {t.table_id:t.source_id for t in tables}
        for record in records:
            for raw in record.get('table_refs') or []:
                ref = TableRef.model_validate(raw)
                if ref.generation_id != generation_id or source_by_table.get(ref.table_id) != ref.source_id:
                    raise GenerationIntegrityError()
                for col in ref.columns:
                    coverage[ref.table_id][(ref.row_number,col)].append((ref.text_start,ref.text_end))
        for table in tables:
            entry = entries[table.table_id]
            if entry['descriptor'] != table.descriptor or entry['quality'] != table.quality:
                raise GenerationIntegrityError()
            seen = set()
            for stored in _stored_rows(session,doc_id,generation_id,table.table_id):
                row = _row(stored)
                for col,cell in row.cells.items():
                    if cell.state == 'blank' or (cell.raw_value == '' and cell.formula is None):
                        continue
                    key = (row.row_number,col); seen.add(key)
                    ranges = coverage[table.table_id].get(key,[])
                    if (None,None) in ranges:
                        continue
                    end = 0
                    for start,stop in sorted(ranges):
                        if start > end or stop > len(cell.display_text):
                            raise GenerationIntegrityError()
                        end = max(end,stop)
                    if not ranges or end != len(cell.display_text):
                        raise GenerationIntegrityError()
            if set(coverage[table.table_id]) != seen:
                raise GenerationIntegrityError()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GenerationIntegrityError() from exc


def clone_generation_tables(session, doc_id, base_id, generation_id, bundle):
    """Copy immutable canonical tables with SQL INSERT SELECT, without row buffering."""
    from sqlalchemy import insert, literal
    lock_writable_generation(session,doc_id,generation_id)
    verify_generation_tables(session,doc_id,base_id)
    for table in session.scalars(select(DocumentTable).where(DocumentTable.doc_id==doc_id,DocumentTable.generation_id==base_id)):
        session.add(DocumentTable(doc_id=doc_id,generation_id=generation_id,table_id=table.table_id,
            source_id=table.source_id,schema_version=table.schema_version,descriptor=table.descriptor,quality=table.quality))
    session.flush()
    fields=['doc_id','generation_id','table_id','row_number','cells','hidden']
    statement=select(DocumentTableRow.doc_id,literal(generation_id),DocumentTableRow.table_id,
                     DocumentTableRow.row_number,DocumentTableRow.cells,DocumentTableRow.hidden).where(
                     DocumentTableRow.doc_id==doc_id,DocumentTableRow.generation_id==base_id)
    session.execute(insert(DocumentTableRow).from_select(fields,statement))
    export_generation_tables(session,doc_id,generation_id,bundle)
