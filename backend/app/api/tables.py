"""Document table views use the same authorization boundary as source views."""
from dataclasses import asdict
import json

from fastapi import APIRouter, Query, Depends
from app.auth.service import require_role
from sqlalchemy import select

from app.api.errors import ApiError
from app.db.models import DocumentTable, DocumentGenerationState
from app.db.session import session_scope
from app.services.table_store import get_document_table, get_table_rows, TableUnavailable

router = APIRouter()
MAX_RESPONSE_BYTES = 1024 * 1024


def _authorize(doc_id):
    from app.api.documents import _require_active_document
    _require_active_document(doc_id)


def _unavailable():
    raise ApiError(status_code=404, code='document_not_found', detail='Источник недоступен')


@router.get('/table-processing/runtime')
def table_runtime(user=Depends(require_role('admin'))):
    from app.config import get_settings
    from docparser.source_model import PARSER_VERSION
    from app.services.chunk_index_projection import CURRENT_CHUNK_PROJECTION_VERSION
    settings = get_settings()
    projection_version = (CURRENT_CHUNK_PROJECTION_VERSION
                          if settings.chunk_index_projection_enabled is True else None)
    return {'parser_version':PARSER_VERSION, 'table_schema_version':1,
            'table_structure_enabled':settings.table_structure_enabled,
            'chunk_projection_version':projection_version}


@router.get('/{doc_id}/tables')
def list_tables(doc_id: str, generation_id: str | None = None):
    _authorize(doc_id)
    with session_scope() as session:
        if generation_id is None:
            state = session.get(DocumentGenerationState, doc_id)
            generation_id = state.active_generation_id if state else None
        result = []
        for table in session.scalars(select(DocumentTable).where(DocumentTable.doc_id == doc_id,
                                    DocumentTable.generation_id == generation_id).order_by(DocumentTable.table_id)):
            try:
                get_document_table(session, doc_id, generation_id, table.table_id)
            except TableUnavailable:
                continue
            result.append({'table_id': table.table_id, 'generation_id': generation_id, 'source_id': table.source_id,
                           'sheet_name': table.descriptor['sheet_name'], 'range': table.descriptor['cell_range'],
                           'quality': table.quality, 'header_status': table.quality.get('header_status', 'unknown')})
        return {'tables': result}


@router.get('/{doc_id}/tables/{table_id}')
def table_descriptor(doc_id: str, table_id: str, generation_id: str = Query(pattern='^[0-9a-f]{32}$'),
                     source_id: str | None = Query(default=None, max_length=255)):
    _authorize(doc_id)
    with session_scope() as session:
        try:
            table = get_document_table(session, doc_id, generation_id, table_id,
                                       source_ids={source_id} if source_id else None)
        except TableUnavailable:
            _unavailable()
        return {'descriptor': table.descriptor, 'quality': table.quality, 'generation_id': generation_id,
                'cause_code': table.quality.get('classifier_cause') or
                    ('header_ambiguous' if table.descriptor['method'] == 'unresolved' else None)}


@router.get('/{doc_id}/tables/{table_id}/rows')
def table_rows(doc_id: str, table_id: str, generation_id: str = Query(pattern='^[0-9a-f]{32}$'),
               from_row: int = Query(default=1, ge=1, le=1048576), limit: int = Query(default=10, ge=1, le=100),
               from_column: int = Query(default=1, ge=1, le=16384), text_start: int = Query(default=0, ge=0),
               columns: str | None = Query(default=None, max_length=256, pattern=r'^\d+(,\d+)*$'),
               source_id: str | None = Query(default=None, max_length=255)):
    _authorize(doc_id)
    with session_scope() as session:
        try:
            table = get_document_table(session, doc_id, generation_id, table_id,
                                       source_ids={source_id} if source_id else None)
        except TableUnavailable:
            _unavailable()
        area = table.descriptor['cell_range']
        if from_row > area['max_row'] or from_column > area['max_column']:
            raise ApiError(status_code=416, code='invalid_request', detail='Диапазон вне таблицы')
        available = [c for c in table.descriptor['columns'] if c['index'] >= from_column]
        requested = set(map(int, columns.split(','))) if columns else None
        if requested is not None and (len(requested) > 12 or any(not 1 <= c <= 16384 for c in requested)):
            raise ApiError(status_code=422, code='invalid_request', detail='Некорректные колонки')
        selected = [c for c in available if requested is None or c['index'] in requested][:12]
        if not selected:
            raise ApiError(status_code=416, code='invalid_request', detail='Диапазон вне таблицы')
        selected = [{**c, 'header_path': [label[:512] for label in c['header_path']]} for c in selected]
        indices = {c['index'] for c in selected}
        stop_row = min(area['max_row'] + 1, from_row + limit)
        try:
            rows = get_table_rows(session, doc_id, generation_id, table_id, range(from_row, stop_row),
                                  source_ids={source_id} if source_id else None)
        except TableUnavailable:
            _unavailable()
        response = {'generation_id': generation_id, 'table_id': table_id, 'columns': selected,
                    'rows': [], 'projection': False, 'continuation': [], 'next_row': stop_row if stop_row <= area['max_row'] else None,
                    'next_column': None}
        for row in rows:
            output = {'row_number': row.row_number, 'hidden': row.hidden, 'cells': {}}
            continuations = []
            for index, cell in row.cells.items():
                if index not in indices:
                    continue
                value = asdict(cell)
                display = cell.display_text
                if text_start > len(display):
                    raise ApiError(status_code=416, code='invalid_request', detail='Диапазон вне ячейки')
                end = min(len(display), text_start + 4000)
                value['display_text'] = display[text_start:end]
                # The endpoint is a bounded display projection; canonical values
                # stay complete in SQL and formula/cache state is explicit.
                for key in ('raw_value', 'cached_value', 'formula', 'number_format'):
                    if isinstance(value[key], str) and len(value[key]) > 512:
                        value[key] = value[key][:512]
                        response['projection'] = True
                output['cells'][str(index)] = value
                if text_start or end < len(display):
                    response['projection'] = True
                    continuations.append({'row_number': row.row_number, 'column': index,
                        'text_start': text_start, 'text_end': end, 'total_chars': len(display),
                        'next_text_start': end if end < len(display) else None})
            trial = {**response, 'rows': [*response['rows'], output], 'continuation': [*response['continuation'], *continuations]}
            if len(json.dumps(trial, ensure_ascii=True).encode()) > MAX_RESPONSE_BYTES - 8192:
                response['projection'] = True
                response['next_row'] = row.row_number
                break
            response['rows'].append(output)
            response['continuation'].extend(continuations)
        if requested is None and len(available) > len(selected):
            response['projection'] = True
            response['next_column'] = available[len(selected)]['index']
        return response
