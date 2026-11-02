"""Lab operation snapshots must cover all metadata within explicit budgets."""
from hashlib import sha256
import ast
import importlib
import json
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine, text


def module():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/kubernetes'))
    return importlib.import_module('replica_upgrade')


@pytest.fixture
def connection():
    tool = module(); engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            for table in tool.CORPUS_TABLES:
                connection.execute(text(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, value TEXT)'))
            yield connection
    finally:
        engine.dispose()


def test_all_rows_beyond_the_former_ten_thousand_limit_are_fingerprinted(connection):
    tool = module()
    connection.execute(text('INSERT INTO work_items (id,value) VALUES (:id,:value)'),
        [{'id': index, 'value': f'fixture-{index}'} for index in range(11001)])
    before = tool.corpus(connection)
    assert before['work_items']['count'] == 11001
    connection.execute(text("UPDATE work_items SET value='changed' WHERE id=11000"))
    after = tool.corpus(connection)
    assert after['work_items']['count'] == 11001
    assert before['work_items']['sha256'] != after['work_items']['sha256']


def test_small_snapshots_preserve_the_existing_sorted_json_hash(connection):
    rows = [{'id': 7, 'value': 'Я'}, {'id': 2, 'value': None}, {'id': 5, 'value': 'a'}]
    connection.execute(text('INSERT INTO documents (id,value) VALUES (:id,:value)'), rows)
    encoded = sorted(json.dumps(row, sort_keys=True, separators=(',', ':'), default=str) for row in rows)
    options = dict(connection.get_execution_options())
    result = module().corpus(connection)
    assert dict(connection.get_execution_options()) == options
    assert result['documents'] == {'count': 3, 'sha256': sha256('\n'.join(encoded).encode()).hexdigest()}
    assert result['document_staging'] == {'count': 0, 'sha256': sha256(b'').hexdigest()}


def test_embedded_release_job_uses_the_same_self_contained_snapshot_function(connection):
    tool = module()
    connection.execute(text("INSERT INTO documents VALUES (1,'synthetic')"))
    parsed = ast.parse(tool.release_source())
    function = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == 'corpus')
    scope = {'json': json, 'CORPUS_TABLES': tool.CORPUS_TABLES}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<embedded-release-corpus>', 'exec'), scope)
    assert scope['corpus'](connection) == tool.corpus(connection)


def test_row_budget_refuses_instead_of_returning_a_partial_snapshot(connection):
    connection.execute(text('INSERT INTO work_items (id,value) VALUES (:id,:value)'),
        [{'id': index, 'value': 'x'} for index in range(4)])
    with pytest.raises(ValueError, match='bound'):
        module().corpus(connection, max_rows=3)


def test_byte_budget_is_global_across_tables(connection):
    for table in module().CORPUS_TABLES:
        connection.execute(text(f'INSERT INTO "{table}" VALUES (1,:value)'), {'value': 'x' * 20})
    with pytest.raises(ValueError, match='byte bound'):
        module().corpus(connection, max_bytes=80)


def test_byte_refusal_stops_streaming_and_closes_the_cursor():
    class Result:
        yielded = 0
        closed = False
        def mappings(self):
            for index in range(10000):
                self.yielded += 1
                yield {'id': index, 'value': 'x' * 60}
        def close(self): self.closed = True
    result = Result()
    class Connection:
        calls = 0
        def execute(self, _statement):
            self.calls += 1
            return result
    connection = Connection()
    with pytest.raises(ValueError, match='byte bound'):
        module().corpus(connection, max_bytes=100)
    assert result.yielded == 2 and result.closed and connection.calls == 1


@pytest.mark.parametrize('kwargs', [
    {'max_rows': 0}, {'max_rows': True}, {'max_rows': 100001},
    {'max_bytes': 0}, {'max_bytes': True}, {'max_bytes': 64 * 1024 * 1024 + 1},
])
def test_invalid_snapshot_budgets_never_query_the_database(kwargs):
    class NoQuery:
        def execute(self, *_args): pytest.fail('Invalid budgets must refuse before SQL')
    with pytest.raises(ValueError): module().corpus(NoQuery(), **kwargs)
