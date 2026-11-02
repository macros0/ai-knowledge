"""The original same-schema CLI path remains explicit and held."""
import json
from uuid import uuid4

from sqlalchemy import text

from tests.test_replica_runtime_identity_db import installed as installed
from tests.test_replica_schema_upgrade_cli import load


def test_default_cli_still_rebinds_same_schema_without_schema_inputs(installed, tmp_path, monkeypatch):
    import app.db.session as database
    import app.deployment.bootstrap as bootstrap
    import app.services.work.maintenance_control as maintenance
    settings = installed
    source = json.loads(settings.runtime_identity_file.read_bytes())
    settings.runtime_image_id = 'same-schema-update@sha256:' + '9' * 64
    settings.runtime_identity_file = tmp_path / 'legacy-output.json'
    runtime, expected = tmp_path / 'runtime.env', tmp_path / 'source.json'
    runtime.write_text('# Isolated explicit input\n')
    expected.write_text(json.dumps(source))
    monkeypatch.setattr(bootstrap, 'OperatorSettings', lambda **kwargs: settings)
    monkeypatch.setattr(database, 'configure_database', lambda url: None)
    original = maintenance.MaintenanceOperator
    def owned(engine, **kwargs):
        return original(engine, allow_sqlite=True, **kwargs)
    operation = uuid4().hex
    with owned(database.get_engine(), operation_id=operation, owner_id='legacy-cli-test', settings=settings) as op:
        with op.transaction() as session:
            maintenance.enter_maintenance(session, operation, kind='upgrade',
                intended_counts={'frontend': 2, 'api': 2, 'worker': 2})
    monkeypatch.setattr(maintenance, 'MaintenanceOperator', owned)
    args = ['--runtime-env-file', str(runtime), '--expected-identity', str(expected),
        '--operation-id', operation, '--owner-id', 'legacy-cli-test', '--apply']
    assert load().main(args) == 0
    assert load().main(args) == 0
    document = json.loads(settings.runtime_identity_file.read_bytes())
    assert document['installation_id'] == source['installation_id']
    assert document['runtime_fingerprint'] != source['runtime_fingerprint']
    with database.get_engine().connect() as conn:
        assert conn.execute(text('SELECT epoch,operation_id,enabled FROM replica_maintenance_state')).one() == (1, operation, 1)
        assert conn.scalar(text("SELECT count(*) FROM audit_log WHERE action_type='replica_runtime_upgrade'")) == 1
