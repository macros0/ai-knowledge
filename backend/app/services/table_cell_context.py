"""Bounded canonical context for a fully specified cell in an already visible source."""
import html
import json
import math
import re
from copy import deepcopy
from hashlib import sha256

from app.config import get_settings
from app.db.models import DocumentTable, DocumentTableRow
from app.models.schemas import TableRef
from app.services.fusion import Hit
from app.services.table_retrieval import _effective_field_tokens, _query_qualifiers
from pydantic import TypeAdapter
from sqlalchemy import JSON, String, case, cast, func, literal, select, tuple_
from sqlalchemy import column as sql_column

_QUOTE = r'(?:«([^»]+)»|“([^”]+)”|"([^"]+)")'
_REFS = TypeAdapter(list[TableRef])
_MAX_TABLES = 8
_MAX_COLUMNS = 32
_MAX_CELL_JSON = 16384


def _one(pattern, query):
    matches = list(re.finditer(pattern, query, re.IGNORECASE))
    if len(matches) != 1:
        return None
    match = matches[0]
    return next(v for v in match.groups() if v is not None), match


def requested_cell(query):
    """Require one explicit sheet, leaf label, physical row and Excel column."""
    if not isinstance(query, str) or len(query) > 16384:
        return None
    sheet = _one(r'(?:лист(?:е|а)?|sheet)\s*' + _QUOTE, query)
    field = _one(r'(?:пол(?:е|я)|field)\s*' + _QUOTE, query)
    row = _one(r'(?:строк[аеуи]|row)\s+([0-9]{1,7})(?![\w.,])', query)
    col = _one(r'(?:колонк[аеуи]|столб(?:ец|це|ца)|column)\s+([A-Z]{1,3})(?!\w)', query)
    if not all((sheet, field, row, col)):
        return None
    number = int(row[0])
    index = 0
    for character in col[0].upper():
        index = index * 26 + ord(character) - ord('A') + 1
    if not 1 <= number <= 1048576 or not 1 <= index <= 16384:
        return None
    masked = query[:row[1].start()] + ' ' * len(row[1].group()) + query[row[1].end():]
    return {'sheet': sheet[0], 'field': field[0], 'row': number, 'column': index,
            'address': col[0].upper() + str(number), 'masked_query': masked}


def _fold(value):
    return ' '.join(value.split()).casefold() if isinstance(value, str) else None


def _scalar(value):
    return value is None or isinstance(value, (str, bool, int)) or isinstance(value, float) and math.isfinite(value)


def _cell_state(cell, address):
    if not isinstance(cell, dict) or cell.get('address') != address or not isinstance(cell.get('display_text'), str):
        return None
    state, raw, display = cell.get('state'), cell.get('raw_value'), cell['display_text']
    formula = cell.get('formula')
    if state not in ('value', 'blank', 'error', 'formula_missing') or not _scalar(raw) or not _scalar(cell.get('cached_value')):
        return None
    if formula is not None and (not isinstance(formula, str) or not formula):
        return None
    if cell.get('merge_anchor') not in (None, address):
        return None
    if state == 'blank':
        return ('blank', '[blank]') if raw in (None, '') and display == '' and formula is None else None
    if state == 'formula_missing':
        return ('formula_missing', '[formula_result_missing]') if formula and raw is None and display == '' and cell.get('cached_value') is None else None
    if raw == '' and display == '' and state == 'value':
        return ('empty_formula_result', '[empty_formula_result]') if formula else ('empty_string', '[empty_string]')
    if state == 'value' and raw is None and display == '':
        return None  # An inconsistent stored value is not evidence of blankness.
    return state, display


def _read_cells(session, table, row_number, columns):
    """Project only requested typed cells and bound each SQL result before decode."""
    dialect = session.get_bind().dialect.name
    if dialect == 'postgresql':
        object_cells = case((func.json_typeof(DocumentTableRow.cells) == 'object', DocumentTableRow.cells), else_=literal({}, type_=JSON))
        cells = func.json_each(object_cells).table_valued(sql_column('key', String), sql_column('value', JSON)).lateral()
        key, value = cells.c.key, cells.c.value
        size = func.octet_length(cast(value, String))
    else:
        object_cells = case((func.json_type(DocumentTableRow.cells) == 'object', DocumentTableRow.cells), else_=literal({}, type_=JSON))
        cells = func.json_each(object_cells).table_valued(sql_column('key', String), sql_column('value', JSON), sql_column('type', String))
        key, value = cells.c.key, cells.c.value
        size = func.length(cast(value, String)) * 4  # Conservative UTF-8 upper bound.
    valid_object = func.json_typeof(value) == 'object' if dialect == 'postgresql' else cells.c.type == 'object'
    projected = case((valid_object & (size <= _MAX_CELL_JSON), value), else_=None)
    statement = select(key, projected, size).select_from(DocumentTableRow).outerjoin(
        cells, key.in_([str(c) for c in columns])).where(
            DocumentTableRow.doc_id == table.doc_id, DocumentTableRow.generation_id == table.generation_id,
            DocumentTableRow.table_id == table.table_id, DocumentTableRow.row_number == row_number)
    selected = list(session.execute(statement))
    if not selected or any(size is not None and size > _MAX_CELL_JSON for _, _, size in selected):
        return None
    result = {}
    for key, value, _ in selected:
        if key is None:
            continue
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return None
        result[int(key)] = value
    return result


def _table_columns(table, request):
    d = table.descriptor
    if not isinstance(d, dict) or not isinstance(table.quality, dict) or table.quality.get('complete') is not True:
        return None
    bounds = d.get('cell_range', {})
    try:
        header_rows = d.get('header_rows')
        if (not isinstance(header_rows, list)
                or any(not isinstance(row, int) or isinstance(row, bool) for row in header_rows)):
            return None
        declared_headerless = (not header_rows and d.get('declared') is True
                               and d.get('method') == 'explicit' and d.get('normalizer_version', 0) >= 4)
        if not header_rows and not declared_headerless:
            return None
        target_column = next((c for c in d.get('columns', []) if c.get('index') == request['column']), None)
        anchors = target_column.get('header_addresses', []) if target_column else []
        if (not isinstance(anchors, list) or not anchors and not declared_headerless
                or declared_headerless and anchors):
            return None
        for anchor in anchors:
            match = re.fullmatch(r'[A-Z]{1,3}([1-9][0-9]{0,6})', anchor) if isinstance(anchor, str) else None
            if match is None or int(match[1]) not in header_rows:
                return None
        valid = (_fold(d.get('sheet_name')) == _fold(request['sheet'])
                 and request['row'] not in d.get('header_rows', [])
                 and bounds['min_row'] <= request['row'] <= bounds['max_row']
                 and bounds['min_column'] <= request['column'] <= bounds['max_column'])
        if not valid:
            return None
        if any(m['min_row'] <= request['row'] <= m['max_row'] and m['min_column'] <= request['column'] <= m['max_column']
               and (request['row'], request['column']) != (m['min_row'], m['min_column']) for m in d.get('merges', [])):
            return None
        labels = {c['index']: c.get('header_path', []) for c in d.get('columns', [])}
        if not labels.get(request['column']) or _fold(labels[request['column']][-1]) != _fold(request['field']):
            return None
        if any(not isinstance(path, list) or any(not isinstance(s, str) for s in path) for path in labels.values()):
            return None
        fields = _effective_field_tokens(labels)
        qualifiers = _query_qualifiers(request['masked_query'], set().union(*fields.values()))
        if not qualifiers:
            return None
        wanted_tokens = set().union(*qualifiers.values())
        columns = {index for index, tokens in fields.items() if tokens & wanted_tokens} | {request['column']}
        if len(columns) > _MAX_COLUMNS:
            return None
        return labels, fields, qualifiers, columns
    except (KeyError, TypeError, ValueError):
        return None


def project_requested_table_cells(hits, scope):
    """Derive one small cell context inside the caller's locked visibility scope."""
    request = requested_cell(scope.get('query', ''))
    if request is None:
        return hits
    session, active = scope['session'], scope['active_generations']
    templates = {}
    for hit in hits:
        payload = hit.payload
        if payload.get('_canonical_verified') is not True:
            continue
        try:
            refs = _REFS.validate_python(payload.get('table_refs', []))
            if any(ref.generation_id != active.get(payload.get('doc_id')) or ref.generation_id != payload.get('generation_id')
                   or ref.source_id != payload.get('source_id') for ref in refs):
                continue
            for ref in refs:
                templates.setdefault((payload['doc_id'], ref.generation_id, ref.table_id), hit)
        except (KeyError, TypeError, ValueError):
            continue
    if not templates or len(templates) > _MAX_TABLES:
        return hits
    # The visible hit authorizes only its own complete canonical workbook
    # source. Discover the explicitly named sheet inside that same locked
    # doc/generation/source, never another attachment or document.
    sources = {}
    for identity, base in templates.items():
        table = session.get(DocumentTable, identity)
        if (table is not None and table.source_id == base.payload.get('source_id')
                and isinstance(table.quality, dict) and table.quality.get('complete') is True):
            sources.setdefault((table.doc_id, table.generation_id, table.source_id), base)
    if not sources:
        return hits
    discovered = list(session.scalars(select(DocumentTable).where(
        tuple_(DocumentTable.doc_id, DocumentTable.generation_id, DocumentTable.source_id).in_(sources),
        DocumentTable.descriptor['sheet_name'].as_string() == request['sheet'],
    ).limit(_MAX_TABLES + 1)))
    if len(discovered) > _MAX_TABLES:
        return hits
    for table in discovered:
        identity = (table.doc_id, table.generation_id, table.table_id)
        templates.setdefault(identity, sources[(table.doc_id, table.generation_id, table.source_id)])
    derived = []
    for identity, base in templates.items():
        table = session.get(DocumentTable, identity)
        if table is None or table.source_id != base.payload.get('source_id'):
            continue
        selection = _table_columns(table, request)
        if selection is None:
            continue
        labels, fields, qualifiers, columns = selection
        cells = _read_cells(session, table, request['row'], sorted(columns))
        if cells is None:
            continue
        identifier_cells = []
        for index in columns:
            cell = cells.get(index)
            if not isinstance(cell, dict) or cell.get('state') != 'value':
                continue
            text = cell.get('display_text')
            if not isinstance(text, str) or not re.fullmatch(r'[0-9]{3,12}', text) or _cell_state(cell, _letter(index) + str(request['row'])) is None:
                continue
            identifier_cells.append({'value': text, 'labels': labels.get(index, []), 'field_tokens': sorted(fields.get(index, set())),
                                     'row_number': request['row'], 'table_id': table.table_id, 'column': index})
        if not all(all(any(c['value'] == code and token in c['field_tokens'] for c in identifier_cells) for token in tokens)
                   for code, tokens in qualifiers.items()):
            continue
        matching = [c for c in identifier_cells if c['value'] in qualifiers and set(c['field_tokens']) & qualifiers[c['value']]]
        # Canonical serialization omits blank cells, but keeps the complete row.
        target = cells.get(request['column'])
        state = ('blank', '[blank]') if request['column'] not in cells else _cell_state(target, request['address'])
        if state is None:
            continue
        lines = [f"Sheet: {html.escape(table.descriptor['sheet_name'])}; row: {request['row']}",
                 f"{request['address']} | {html.escape(' / '.join(labels[request['column']]))}: {html.escape(state[1])} (cell state: {state[0]})"]
        for code in matching:
            if code['column'] != request['column']:
                lines.append(f"{_letter(code['column'])}{request['row']} | {html.escape(' / '.join(code['labels']))}: {html.escape(code['value'])} (cell state: value)")
        content = '\n'.join(lines)
        settings = scope.get('settings') or get_settings()
        if len(content) > 2000 or len(content.encode()) > settings.table_context_tokens:
            continue
        references = [TableRef(generation_id=table.generation_id, source_id=table.source_id, table_id=table.table_id,
                              row_number=request['row'], columns=[index]).model_dump(mode='json')
                      for index in dict.fromkeys([request['column'], *(c['column'] for c in matching)])]
        proof = {**references[0], 'address': request['address'], 'column': request['column'], 'state': state[0],
                 'sheet_name': table.descriptor['sheet_name'], 'header_path': labels[request['column']]}
        payload = deepcopy(base.payload)
        payload.update(content=content, title=f"{table.descriptor['sheet_name']} — {request['row']}",
                       section_title=f"{table.descriptor['sheet_name']} — {request['row']}", table_refs=references,
                       _table_identifier_cells=matching, _table_cell_states=[proof])
        digest = sha256(json.dumps([table.doc_id, references], sort_keys=True).encode()).hexdigest()
        derived.append(Hit('query-cell-' + digest, base.score, payload))
    # Overlapping/duplicate regions cannot silently choose one physical source.
    return [*hits, derived[0]] if len(derived) == 1 else hits


def _letter(index):
    result = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def prioritize_requested_cell_matches(blocks, query):
    """Put the fully proven explicit answer first; keep other scores/order."""
    request = requested_cell(query)
    if request is None:
        return None
    def complete(block):
        if block.get('_canonical_verified') is not True:
            return False
        for proof in block.get('_table_cell_states', []):
            if (not isinstance(proof, dict) or proof.get('state') not in
                    ('value', 'blank', 'empty_string', 'empty_formula_result', 'formula_missing', 'error')
                    or proof.get('address') != request['address'] or proof.get('column') != request['column']
                    or proof.get('row_number') != request['row']
                    or _fold(proof.get('sheet_name')) != _fold(request['sheet'])
                    or not proof.get('header_path') or _fold(proof['header_path'][-1]) != _fold(request['field'])
                    or proof.get('generation_id') != block.get('generation_id')
                    or proof.get('source_id') != block.get('source_id')):
                continue
            if any(all(ref.get(key) == proof.get(key) for key in
                       ('generation_id', 'source_id', 'table_id', 'row_number'))
                   and request['column'] in ref.get('columns', []) for ref in block.get('table_refs', [])):
                return True
        return False
    if not any(complete(block) for block in blocks):
        return None
    return sorted(blocks, key=complete, reverse=True)
