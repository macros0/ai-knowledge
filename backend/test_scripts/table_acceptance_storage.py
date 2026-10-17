"""Read-only private resource fingerprints; no source rows leave the snapshotter."""
import hashlib
import json
from pathlib import Path
import re
from sqlalchemy import inspect, text


class StorageUnknown(RuntimeError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=str)


def digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def files_digest(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise StorageUnknown('Cache directory is absent')
    manifest = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise StorageUnknown('Cache entry escapes private root')
        if path.is_file():
            hashed = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    hashed.update(block)
            manifest[path.relative_to(root).as_posix()] = hashed.hexdigest()
    return digest(manifest)


def _private_database(engine, private_root):
    url = engine.url
    backend = url.get_backend_name()
    if backend == 'postgresql':
        if (url.host != '127.0.0.1' or url.port != 15432
                or not isinstance(url.database, str) or not re.fullmatch(r'r2_[a-z0-9_]+', url.database)):
            raise StorageUnknown('Database is outside the private R2 PostgreSQL identity')
    elif backend == 'sqlite':
        if (private_root is None or not url.database or url.database == ':memory:'
                or not Path(url.database).resolve().is_relative_to(Path(private_root).resolve())
                or not Path(url.database).is_file()):
            raise StorageUnknown('SQLite is outside an existing private root')
    else:
        raise StorageUnknown('Unknown private database backend')
    return backend


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[a-z_][a-z_0-9]*', value):
        raise StorageUnknown('Unexpected schema identifier')
    return '"' + value + '"'


def database_state(engine, *, private_root=None, require_active=True):
    backend = _private_database(engine, private_root)
    schema = []
    content = hashlib.sha256()
    active = []
    with engine.connect() as connection:
        # This is the first SQL statement: every catalog and row read shares the
        # same read-only repeatable snapshot. SQLite starts an explicit read
        # transaction; no persistent query_only mutation leaks to pooled clients.
        connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
                                if backend == 'postgresql' else 'BEGIN'))
        catalog = inspect(connection)
        names = sorted(catalog.get_table_names(schema='public' if backend == 'postgresql' else None))
        if 'document_generation_states' not in names:
            raise StorageUnknown('Generation-pointer schema is absent')
        for name in names:
            table = _identifier(name)
            columns = catalog.get_columns(name)
            pk = catalog.get_pk_constraint(name)['constrained_columns']
            schema.append({'table': name, 'columns': [
                {'name': c['name'], 'type': str(c['type']), 'nullable': c['nullable'],
                 'default': c.get('default'), 'computed': c.get('computed')} for c in columns],
                'primary_key': pk})
            if name == 'document_generation_states' and not any(
                    c['name'] == 'active_generation_id' for c in columns):
                raise StorageUnknown('Active-generation column is absent')
            ordering = ', '.join(_identifier(k) for k in pk)
            if backend == 'postgresql':
                query = 'SELECT row_to_json(t)::text FROM ' + table + ' AS t ORDER BY ' + (ordering or 'row_to_json(t)::text')
            else:
                query = 'SELECT * FROM ' + table + ' ORDER BY ' + (ordering or ', '.join(_identifier(c['name']) for c in columns))
            content.update((name + '\n').encode())
            rows = connection.execution_options(stream_results=True).execute(text(query))
            try:
                for row in rows:
                    data = json.loads(row[0]) if backend == 'postgresql' else dict(row._mapping)
                    content.update((_json(data) + '\n').encode())
                    if name == 'document_generation_states' and data['active_generation_id']:
                        active.append(data)
            finally:
                rows.close()
        connection.rollback()
    if require_active and not active:
        raise StorageUnknown('Published generation pointers are absent')
    return {'database_snapshot_sha256': content.hexdigest(), 'schema_sha256': digest(schema),
            'active_generations_sha256': digest(active), 'active_generation_count': len(active)}


def index_state(client, collection):
    if not isinstance(collection, str) or not re.fullmatch(r'r2_evaluation_[a-z0-9_]+', collection):
        raise StorageUnknown('Collection is outside the private R2 identity')
    config = client.get_collection(collection).config.model_dump(mode='json')
    content = hashlib.sha256()
    offset = None
    count = 0
    offsets = set()
    while True:
        points, offset = client.scroll(collection_name=collection, offset=offset, limit=128,
                                       with_payload=True, with_vectors=True)
        for point in points:
            content.update((_json(point.model_dump(mode='json')) + '\n').encode())
            count += 1
        if offset is None:
            break
        key = _json(offset)
        if key in offsets:
            raise StorageUnknown('Index pagination repeated')
        offsets.add(key)
    return {'index_snapshot_sha256': digest({'config': config, 'points_sha256': content.hexdigest(),
                                            'count': count}), 'point_count': count}


def capture_resource_snapshot(engine, client, collection, cache_root, *,
                              table_structure_enabled, kind, private_root=None):
    if kind not in ('resource-inputs', 'resource-outputs') or type(table_structure_enabled) is not bool:
        raise StorageUnknown('Snapshot role or feature flag is unknown')
    result = database_state(engine, private_root=private_root, require_active=kind == 'resource-outputs')
    result.update(index_state(client, collection))
    result.update(snapshot_kind=kind, collection_name=collection,
                  table_structure_enabled=table_structure_enabled,
                  cache_manifest_sha256=files_digest(cache_root))
    if kind == 'resource-inputs' and (result['active_generation_count'] or result['point_count']):
        raise StorageUnknown('Resource inputs are not a fresh empty private state')
    return result
