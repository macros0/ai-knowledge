"""The offline CLI uses scoped identity settings and resumes after SQL commit."""
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.deployment.runtime_identity import identity_document
from tests.test_replica_schema_adoption import migrated as migrated, COUNTS, PREVIOUS
from tests.test_replica_restore_operator import APP_URL, OP_URL


def load():
    path = Path(__file__).resolve().parents[1] / 'scripts/upgrade_replica_runtime.py'
    spec = importlib.util.spec_from_file_location('schema_upgrade_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def invocation(migrated, tmp_path, monkeypatch):
    import app.db.session as database
    import app.deployment.bootstrap as bootstrap
    import app.services.work.maintenance_control as maintenance
    from tests.test_replica_maintenance import stopped
    engine, settings, old = migrated
    settings.database_url = APP_URL
    settings.runtime_identity_file = tmp_path / 'new-identity.json'
    selected = tmp_path / 'runtime.env'
    selected.write_text('# Selected isolated fixture\n')
    source, receipts, counts, operator = [tmp_path / name for name in (
        'source.json', 'writers.json', 'counts.json', 'operator-url')]
    source.write_text(json.dumps(old))
    receipts.write_text(json.dumps(stopped()))
    counts.write_text(json.dumps(COUNTS))
    operator.write_text(OP_URL)
    monkeypatch.setattr(bootstrap, 'OperatorSettings', lambda **kwargs: settings)
    configured = []
    monkeypatch.setattr(database, 'configure_database', lambda url: configured.append(url))
    monkeypatch.setattr(database, 'get_engine', lambda: engine)
    original = maintenance.MaintenanceOperator
    def owned(bound_engine, **kwargs):
        assert bound_engine is engine and kwargs['settings'] is settings
        return original(bound_engine, allow_sqlite=True, **kwargs)
    monkeypatch.setattr(maintenance, 'MaintenanceOperator', owned)
    operation = uuid4().hex
    argv = ['--runtime-env-file', str(selected), '--expected-identity', str(source),
        '--operation-id', operation, '--owner-id', 'cli-schema-test',
        '--adopt-schema-from', PREVIOUS, '--writer-receipts', str(receipts),
        '--intended-counts', str(counts), '--operator-db-file', str(operator), '--apply']
    return {'engine': engine, 'settings': settings, 'old': old, 'configured': configured,
        'argv': argv, 'operation': operation, 'receipts': receipts, 'counts': counts, 'operator': operator}


def test_cli_adopts_schema_with_operator_engine_but_scoped_signed_identity(invocation, capsys):
    value = invocation
    assert load().main(value['argv']) == 0
    stdout, stderr = capsys.readouterr()
    assert 'OPERATOR_CANARY' not in stdout + stderr and 'APP_CANARY' not in stdout + stderr
    receipt = json.loads(stdout.removeprefix('REPLICA_UPGRADE_IDENTITY:'))
    document = json.loads(value['settings'].runtime_identity_file.read_bytes())
    assert receipt['identity'] == document and receipt['jobs_held'] is True
    assert value['configured'] == [OP_URL] and value['settings'].db_url == APP_URL
    assert document == identity_document(value['settings'], installation_id=value['old']['installation_id'],
        storage_receipt_digest=value['old']['storage_receipt_digest'])
    with value['engine'].connect() as conn:
        assert conn.execute(text('SELECT epoch,operation_id,enabled FROM replica_maintenance_state')).one() == (2, value['operation'], 1)
        assert conn.execute(text('SELECT status,attempts FROM work_items')).one() == ('queued', 0)


def test_cli_resumes_after_committed_sql_before_identity_publication(invocation, monkeypatch, capsys):
    import app.deployment.bootstrap as bootstrap
    publish = bootstrap.write_identity
    def unavailable(*_args):
        raise OSError('OPERATOR_CANARY must not reach stderr')
    monkeypatch.setattr(bootstrap, 'write_identity', unavailable)
    tool, value = load(), invocation
    assert tool.main(value['argv']) == 2
    assert not value['settings'].runtime_identity_file.exists()
    with value['engine'].connect() as conn:
        assert conn.scalar(text('SELECT epoch FROM replica_maintenance_state')) == 2
        assert conn.scalar(text('SELECT count(*) FROM audit_log')) == 2
    monkeypatch.setattr(bootstrap, 'write_identity', publish)
    assert tool.main(value['argv']) == 0
    assert tool.main(value['argv']) == 0
    with value['engine'].connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM audit_log')) == 2
        assert conn.scalar(text('SELECT enabled FROM replica_maintenance_state'))
    assert 'OPERATOR_CANARY' not in ''.join(capsys.readouterr())


def test_cli_plan_does_not_configure_sql_or_publish_identity(invocation, capsys):
    value = invocation
    assert load().main([arg for arg in value['argv'] if arg != '--apply']) == 0
    assert value['configured'] == [] and not value['settings'].runtime_identity_file.exists()
    assert json.loads(capsys.readouterr().out) == {'status': 'planned', 'mutations': 0}


@pytest.mark.parametrize('failure', ['foreign_sql', 'missing_receipts', 'oversize_receipts',
    'nonobject_counts', 'missing_predecessor', 'unknown_predecessor'])
def test_cli_refuses_unbound_inputs_before_sql_configuration(invocation, failure, capsys):
    value = invocation
    argv = list(value['argv'])
    if failure == 'foreign_sql':
        value['operator'].write_text(OP_URL.replace('@postgres:', '@foreign:'))
    elif failure == 'missing_receipts':
        argv.remove(str(value['receipts']))
        argv.remove('--writer-receipts')
    elif failure == 'oversize_receipts':
        value['receipts'].write_text(' ' * 8193)
    elif failure == 'nonobject_counts':
        value['counts'].write_text('[]')
    elif failure == 'missing_predecessor':
        argv.remove(PREVIOUS)
        argv.remove('--adopt-schema-from')
    else:
        argv[argv.index(PREVIOUS)] = 'unknown'
    assert load().main(argv) == 2
    assert value['configured'] == [] and not value['settings'].runtime_identity_file.exists()
    assert 'OPERATOR_CANARY' not in ''.join(capsys.readouterr())
    with value['engine'].connect() as conn:
        assert conn.scalar(text('SELECT operation_id FROM replica_maintenance_state')) == '0' * 32
