"""Canonical table fragment hydration within the existing visibility snapshot."""
from collections import defaultdict
from functools import lru_cache
from hashlib import sha256
import re

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import select, tuple_, column as sql_column, String, JSON, func, case, literal, and_, or_

from app.db.models import DocumentTable, DocumentTableRow, OkfConcept, DocumentChunk
from app.models.schemas import TableRef
from pydantic import TypeAdapter
from app.services.table_store import get_table_rows, TableUnavailable


_TABLE_REFS = TypeAdapter(list[TableRef])


def _validated_table_refs(refs, *, encoded=False):
    return _TABLE_REFS.validate_json(refs) if encoded else _TABLE_REFS.validate_python(refs)


def hydrate_table_fragments(hits, scope):
    session, active = scope['session'], scope['active_generations']
    concept_keys = {(h.payload.get('doc_id'), h.payload.get('slug')) for h in hits
                    if h.payload.get('point_type') == 'concept' and h.payload.get('slug')}
    chunk_keys = {(h.payload.get('doc_id'), h.payload.get('chunk_index')) for h in hits
                  if h.payload.get('point_type') == 'chunk' and h.payload.get('chunk_index') is not None}
    values = scope.get('canonical_values', {})
    if 'canonical_values' not in scope and concept_keys:
        for row in session.scalars(select(OkfConcept).where(tuple_(OkfConcept.doc_id, OkfConcept.slug).in_(concept_keys),
                                                            OkfConcept.table_refs.is_not(None))):
            values[(row.doc_id, 'concept', row.slug)] = row
    if 'canonical_values' not in scope and chunk_keys:
        for row in session.scalars(select(DocumentChunk).where(tuple_(DocumentChunk.doc_id, DocumentChunk.chunk_index).in_(chunk_keys),
                                                               DocumentChunk.table_refs.is_not(None))):
            values[(row.doc_id, 'chunk', row.chunk_index)] = row
    verified, table_keys = {}, set()
    # Canonical JSON may be identical in concept and chunk hits. Reuse typed
    # decoding only within this request; each hit still checks its provenance.
    decoded_refs = {}
    for hit in hits:
        payload = hit.payload
        payload.pop('table_refs', None)
        payload.pop('_table_identifier_cells', None)
        payload.pop('_table_cell_states', None)
        kind = payload.get('point_type')
        key = (payload.get('doc_id'), kind, payload.get('slug') if kind == 'concept' else payload.get('chunk_index'))
        stored = values.get(key)
        if stored is None:
            continue
        try:
            encoded = getattr(stored, 'table_refs_json', False)
            cache_key = stored.table_refs if encoded and isinstance(stored.table_refs, str) else None
            if cache_key is not None and cache_key in decoded_refs:
                refs = decoded_refs[cache_key]
            else:
                refs = _validated_table_refs(stored.table_refs, encoded=encoded)
                if cache_key is not None:
                    decoded_refs[cache_key] = refs
            if not refs or any(r.generation_id != active.get(stored.doc_id) or r.source_id != stored.source_id for r in refs):
                raise ValueError('Table generation/source mismatch')
            verified[id(hit)] = (stored, refs)
            table_keys.update((stored.doc_id, r.generation_id, r.table_id) for r in refs)
        except (TypeError, ValueError):
            verified[id(hit)] = None
    tables = {(t.doc_id, t.generation_id, t.table_id): t for t in session.scalars(select(DocumentTable).where(
        tuple_(DocumentTable.doc_id, DocumentTable.generation_id, DocumentTable.table_id).in_(table_keys)))} if table_keys else {}
    # The locked snapshot fixes these identities for this request. Reject a
    # mixed fragment as a whole before selecting rows or building its payload.
    complete_tables = {(*key, table.source_id) for key, table in tables.items()
                       if table.quality.get('complete')}
    for key, value in verified.items():
        if value is not None:
            stored, refs = value
            if any((stored.doc_id, r.generation_id, r.table_id, r.source_id)
                   not in complete_tables for r in refs):
                verified[key] = None
    labels_by_table = {key: {c['index']: c.get('header_path', [])
        for c in table.descriptor.get('columns', [])} for key, table in tables.items()}
    fields = {key: _effective_field_tokens(labels) for key, labels in labels_by_table.items()}
    vocabulary = set().union(*(tokens for columns in fields.values() for tokens in columns.values()))
    qualifiers = _query_qualifiers(scope.get('query', ''), vocabulary)
    requested = set().union(*qualifiers.values())
    wanted = defaultdict(set)
    for value in verified.values() if qualifiers else ():
        if value is None:
            continue
        stored, refs = value
        for ref in refs:
            table_key = (stored.doc_id, ref.generation_id, ref.table_id)
            if ref.row_number in tables[table_key].descriptor.get('header_rows', []):
                continue
            selected = {c for c in ref.columns if fields[table_key].get(c, set()) & requested}
            if selected:
                wanted[(*table_key, ref.row_number)].update(selected)
        if sum(len(columns) for columns in wanted.values()) > 4096:
            wanted.clear()  # Preserve RRF rather than overrun the optional priority budget.
            break
    batches = []
    keys = sorted(wanted)
    projected_pairs = 0
    for start in range(0, len(keys), 128):
        batch = keys[start:start + 128]
        columns = sorted(set().union(*(wanted[key] for key in batch)))
        projected_pairs += len(batch) * len(columns)
        if projected_pairs > 8192:
            batches.clear()
            wanted.clear()
            break
        for offset in range(0, len(columns), 32):
            batches.append((batch, columns[offset:offset + 32]))
    rows = _read_identifier_rows(session, batches)
    result = []
    for hit in hits:
        value = verified.get(id(hit), 'legacy')
        if value == 'legacy':
            result.append(hit)
            continue
        if value is None:
            continue
        stored, refs = value
        identifier_cells = []
        valid_rows = True
        for ref in refs:
            table_key = (stored.doc_id, ref.generation_id, ref.table_id)
            row_key = (*table_key, ref.row_number)
            if row_key not in wanted:
                continue
            row = rows.get(row_key)
            if row is None:
                valid_rows = False
                break
            labels = labels_by_table[table_key]
            for column in set(ref.columns) & wanted[row_key]:
                text = (row.get(column) or '').strip()
                if _CELL_IDENTIFIER.fullmatch(text):
                    identifier_cells.append({'value': text, 'labels': labels.get(column, []),
                        'field_tokens': sorted(fields[table_key][column]),
                        'row_number': ref.row_number, 'table_id': ref.table_id})
        if not valid_rows:
            continue
        hit.payload['_table_identifier_cells'] = identifier_cells
        hit.payload['content'] = stored.content
        hit.payload['table_refs'] = _TABLE_REFS.dump_python(refs, mode='json')
        result.append(hit)
    return project_requested_table_cells(result, scope)


def project_requested_table_cells(hits, scope):
    from app.services.table_cell_context import project_requested_table_cells as project
    return project(hits, scope)


def _row_identity_condition(keys):
    grouped = defaultdict(set)
    for doc_id, generation_id, table_id, row_number in keys:
        grouped[(doc_id,generation_id,table_id)].add(row_number)
    return or_(False, *(and_(DocumentTableRow.doc_id == doc,
                     DocumentTableRow.generation_id == generation,
                     DocumentTableRow.table_id == table,
                     DocumentTableRow.row_number.in_(sorted(rows)))
                 for (doc,generation,table),rows in sorted(grouped.items())))


def _read_identifier_rows(session, batches):
    """Read only derived numeric codes; retain physical identity and missing rows."""
    if session.get_bind().dialect.name != 'postgresql':
        return _read_projected_rows(session, batches)
    rows = {}
    for batch, columns in batches:
        cells = func.jsonb_array_elements(DocumentTableRow.cells_projection).table_valued(
            sql_column('value', JSONB)).lateral()
        key = cells.c.value['key'].as_string()
        display = cells.c.value['value']['display_text'].as_string()
        statement = select(DocumentTableRow.doc_id, DocumentTableRow.generation_id,
            DocumentTableRow.table_id, DocumentTableRow.row_number, key, display).select_from(
                DocumentTableRow).outerjoin(cells, key.in_([str(c) for c in columns])).where(
                _row_identity_condition(batch))
        for row in session.execute(statement):
            values = rows.setdefault(tuple(row[:4]), dict.fromkeys(columns))
            if row[4] is not None:
                values[int(row[4])] = row[5]
    return rows


def _read_projected_rows(session, batches):
    """Parse each canonical JSON row once; return only the bounded display fields.

    An outer join keeps existing rows whose requested cells are absent, matching
    ordinary JSON path projection. Four-part keys keep generations/documents apart.
    """
    rows = {}
    for batch, columns in batches:
        dialect = session.get_bind().dialect.name
        json_type = func.json_typeof if dialect == 'postgresql' else func.json_type
        object_cells = case((json_type(DocumentTableRow.cells) == 'object',
            DocumentTableRow.cells), else_=literal({}, type_=JSON))
        fields = [sql_column('key', String), sql_column('value', JSON)]
        if dialect == 'sqlite':
            fields.append(sql_column('type', String))
        cells = func.json_each(object_cells).table_valued(*fields)
        if dialect == 'postgresql':
            cells = cells.lateral()
            is_object = func.json_typeof(cells.c.value) == 'object'
        else:
            is_object = cells.c.type == 'object'
        display = case((is_object, cells.c.value['display_text'].as_string()), else_=None)
        statement = select(DocumentTableRow.doc_id, DocumentTableRow.generation_id,
            DocumentTableRow.table_id, DocumentTableRow.row_number,
            cells.c.key, display).select_from(
                DocumentTableRow).outerjoin(cells, cells.c.key.in_([str(c) for c in columns])).where(
                tuple_(DocumentTableRow.doc_id, DocumentTableRow.generation_id,
                    DocumentTableRow.table_id, DocumentTableRow.row_number).in_(batch))
        for row in session.execute(statement):
            values = rows.setdefault(tuple(row[:4]), {})
            for column_index in columns:
                values.setdefault(column_index, None)
            if row[4] is not None:
                values[int(row[4])] = row[5]
    return rows


def restore_table_snapshot(saved, *, mail_mode):
    from app.db.session import session_scope
    from app.services.chat_source_selection import _changed
    from app.services.mail_scope import mail_scope_allowed
    if not mail_scope_allowed(saved.get('mail_scope', 'unknown'), mail_mode):
        _changed()
    text = saved.get('content', '')
    if sha256(text.encode()).hexdigest() != saved.get('_table_snapshot_digest'):
        _changed()
    try:
        refs = [TableRef.model_validate(r) for r in saved['table_refs']]
        with session_scope() as session:
            groups = defaultdict(set)
            for ref in refs:
                if ref.source_id != saved.get('source_id') or ref.generation_id != saved.get('generation_id'):
                    _changed()
                groups[(ref.generation_id, ref.table_id, ref.source_id)].add(ref.row_number)
            for (generation, table, source), rows in groups.items():
                stored = get_table_rows(session, saved['doc_id'], generation, table, sorted(rows), source_ids={source})
                if {r.row_number for r in stored} != rows:
                    _changed()
    except (TableUnavailable, ValueError, KeyError):
        _changed()
    return dict(saved)


_CELL_IDENTIFIER = re.compile(r'[0-9]{3,12}')
_IDENTIFIER = re.compile(r'(?<![\w.,+-])[0-9]{3,12}(?!\w|[.,][0-9])')
_QUOTED = re.compile(r'«[^»]*»|“[^”]*”|"[^"]*"')
_NUMERIC_LITERAL = re.compile(r'(?<!\w)[+-]?[0-9]+(?:[.,][0-9]+)?(?!\w)')
_PRECEDING_LABEL = re.compile(r'([^\W\d_]{2,64})\s*(?:[:=#]\s*)?$')


def _label_words(labels):
    from app.services.sparse import TOKEN_RE, normalize_for_tokens
    # Only the column leaf can qualify a field; shared captions are context.
    return TOKEN_RE.findall(normalize_for_tokens(labels[-1] if labels else ''))


def _label_tokens(labels):
    return set(_label_words(labels))


def _compute_effective_field_tokens(paths):
    words = {key: _label_words(labels) for key, labels in paths.items()}
    fields = {key: set(tokens) for key, tokens in words.items()}
    singletons = defaultdict(set)
    for key, tokens in fields.items():
        if len(tokens) == 1:
            singletons[tuple(paths[key][:-1])].update(tokens)
    # A sibling Code leaf takes the generic prefix away from Code BE;
    # a sibling BE name must not suppress the distinguishing BE suffix.
    return {key: tokens - {words[key][0]} if len(tokens) > 1
            and words[key][0] in singletons[tuple(paths[key][:-1])] else tokens
            for key, tokens in fields.items()}


@lru_cache(maxsize=64)
def _cached_effective_field_tokens(key):
    return tuple((index, frozenset(tokens))
                 for index, tokens in _compute_effective_field_tokens(dict(key)).items())


def _effective_field_tokens(paths):
    # Only bounded, pure header metadata is cached. Exact paths include parent
    # groups because siblings determine which generic prefix is suppressed.
    if (len(paths) > 512
            or any(not isinstance(labels, (list, tuple)) for labels in paths.values())
            or sum(len(labels) for labels in paths.values()) > 1024
            or any(not isinstance(label, str) for labels in paths.values() for label in labels)
            or sum(len(label) for labels in paths.values() for label in labels) > 16384):
        return _compute_effective_field_tokens(paths)
    key = tuple((index, tuple(labels)) for index, labels in paths.items())
    # A caller can mutate its result without poisoning a later request.
    return {index: set(tokens) for index, tokens in _cached_effective_field_tokens(key)}


def _query_qualifiers(query, vocabulary):
    from app.services.sparse import normalize_for_tokens
    query = _QUOTED.sub(lambda m: ' ' * len(m.group()), query)
    if any(not _CELL_IDENTIFIER.fullmatch(m.group()) for m in _NUMERIC_LITERAL.finditer(query)):
        return {}
    matches = list(_IDENTIFIER.finditer(query))
    if not matches:
        return {}
    qualifiers = defaultdict(set)
    for match in matches:
        label_match = _PRECEDING_LABEL.search(query[:match.start()])
        if label_match is None:
            return {}
        label = normalize_for_tokens(label_match.group(1))
        if label not in vocabulary and len(label) >= 4 and label[-1] in 'аеёиоуыэюя':
            label = label[:-1]  # e.g. кода -> код, only when the source contains код
        if label not in vocabulary:
            return {}
        qualifiers[match.group()].add(label)
    return qualifiers


def prioritize_table_identifier_matches(blocks, query):
    """Promote canonical field-qualified identifiers from one physical row.

    Preserve legacy positions, scores, fragments and stable RRF order among
    equal matches. Source leaf labels and exact cell values supply evidence.
    """
    from app.services.table_cell_context import prioritize_requested_cell_matches
    requested = prioritize_requested_cell_matches(blocks, query)
    if requested is not None:
        return requested
    positions = [i for i, block in enumerate(blocks) if block.get('table_refs')]
    paths = defaultdict(dict)
    for i in positions:
        for cell in blocks[i].get('_table_identifier_cells', []):
            paths[cell['table_id']][tuple(cell.get('labels', []))] = cell.get('labels', [])
    fallback = {table: _effective_field_tokens(labels) for table, labels in paths.items()}
    def tokens(cell):
        return set(cell['field_tokens']) if 'field_tokens' in cell else fallback[cell['table_id']][tuple(cell.get('labels', []))]
    vocabulary = set().union(*(tokens(cell) for i in positions
        for cell in blocks[i].get('_table_identifier_cells', [])))
    qualifiers = _query_qualifiers(query, vocabulary)
    if not qualifiers:
        return blocks
    def complete(block):
        grouped = defaultdict(list)
        for cell in block.get('_table_identifier_cells', []):
            if _CELL_IDENTIFIER.fullmatch(cell['value']):
                grouped[(cell['table_id'], cell['row_number'])].append(cell)
        return any(all(all(any(cell['value'] == identifier and label in tokens(cell)
                                  for cell in cells) for label in labels)
                       for identifier, labels in qualifiers.items()) for cells in grouped.values())
    ordered = sorted((blocks[i] for i in positions), key=complete, reverse=True)
    result = list(blocks)
    for i, block in zip(positions, ordered):
        result[i] = block
    return result
