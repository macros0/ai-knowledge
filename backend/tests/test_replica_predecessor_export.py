"""A real encrypted predecessor export must stop on changed physical/SQL ownership."""
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO

import pytest

from app.deployment.replica_backup import BackupError, BackupPlan, BackupReader
from tests.test_replica_backup import KEY, fixture_plan
from tests.test_replica_predecessor_backup import previous_plan
from tests.test_replica_predecessor_stop import load


def exports(tool, plan, payloads, root, *, source_guard, postgres_failure=False, after_read=None):
    @contextmanager
    def stream(entry):
        yield BytesIO(payloads[entry.id])
        if after_read:
            after_read(entry)

    def postgres(writer, entry):
        if postgres_failure:
            tool.export_postgres(writer, entry.id,
                [sys.executable, '-c', "import sys;sys.stdout.buffer.write(b'partial');sys.exit(2)"],
                {}, root.parent / 'private-pg-stderr.log')
        else:
            writer.add(entry.id, BytesIO(payloads[entry.id]))

    return tool.export_predecessor_archive(plan, root, KEY, source_guard=source_guard,
        destination_guard=lambda: plan.destination, postgres_export=postgres,
        object_open=stream, finish_clock=lambda: datetime.now(timezone.utc))


def guard(tool, plan, state):
    def sql_guard():
        if not state['sql_owned']:
            raise ValueError('Owned S4 token was lost')

    def snapshot_guard():
        if not state['snapshot_live']:
            raise ValueError('Original snapshot connection was lost')

    return tool.predecessor_archive_guard(plan, physical_observation=lambda: state['physical'],
        sql_guard=sql_guard, snapshot_guard=snapshot_guard)


def source_state():
    return {'sql_owned': True, 'snapshot_live': True, 'physical': '6' * 64}


def test_predecessor_provider_export_cold_verifies_every_original_payload(tmp_path):
    tool=load()
    value,payloads=previous_plan()
    plan=BackupPlan.model_validate(value)
    result=exports(tool,plan,payloads,tmp_path/'archive',source_guard=guard(tool,plan,source_state()))
    assert result.status=='complete' and result.plan.source.epoch==0
    reader=BackupReader(tmp_path/'archive',KEY)
    for entry,body in payloads.items():
        target=BytesIO()
        reader.copy(entry,target)
        assert target.getvalue()==body


@pytest.mark.parametrize('failure',['physical','sql_owned','snapshot_live'])
def test_changed_source_during_export_cannot_publish_a_complete_archive(tmp_path,failure):
    tool=load()
    value,payloads=previous_plan()
    plan=BackupPlan.model_validate(value)
    state=source_state()

    def change_after_read(entry):
        if entry.component=='qdrant':
            state[failure]='8'*64 if failure=='physical' else False

    with pytest.raises((ValueError,BackupError)):
        exports(tool,plan,payloads,tmp_path/'archive',source_guard=guard(tool,plan,state),after_read=change_after_read)
    assert json.loads((tmp_path/'archive/manifest.json').read_bytes())['status']=='incomplete'


def test_failed_real_pg_dump_keeps_predecessor_archive_incomplete(tmp_path):
    tool=load()
    value,payloads=previous_plan()
    plan=BackupPlan.model_validate(value)
    with pytest.raises(ValueError):
        exports(tool,plan,payloads,tmp_path/'archive',source_guard=guard(tool,plan,source_state()),postgres_failure=True)
    assert json.loads((tmp_path/'archive/manifest.json').read_bytes())['status']=='incomplete'


def test_predecessor_export_cannot_bypass_the_current_schema_protocol(tmp_path):
    tool=load()
    plan,payloads=fixture_plan()
    with pytest.raises(ValueError):
        exports(tool,plan,payloads,tmp_path/'archive',source_guard=lambda: plan.source)
    assert not (tmp_path/'archive').exists()


def test_changed_physical_source_is_refused_before_archive_creation(tmp_path):
    tool=load()
    value,payloads=previous_plan()
    plan=BackupPlan.model_validate(value)
    state=source_state()
    state['physical']='8'*64
    with pytest.raises(ValueError):
        exports(tool,plan,payloads,tmp_path/'archive',source_guard=guard(tool,plan,state))
    assert not (tmp_path/'archive').exists()


def test_predecessor_cli_rejects_foreign_scope_without_revealing_credentials(tmp_path,capsys):
    from tests.test_replica_backup_coordinator import config
    tool=load()
    value=config()
    value['namespace']='default'
    canary='PRIVATE_PREDECESSOR_EXPORT_CANARY_59381'
    value['credentials']={'POSTGRES_PASSWORD':canary}
    path=tmp_path/'config.json'
    path.write_text(json.dumps(value))
    arguments=['--config',str(path),'--namespace-uid','77777777-7777-4777-8777-777777777777',
        '--fingerprint','2'*64]
    for name in ('evidence-file','archive','key-file','pg-dump','report','operation-journal'):
        arguments.extend(['--'+name,str(tmp_path/name)])
    assert tool.main(arguments)==2
    output=capsys.readouterr()
    assert canary not in output.out+output.err
    assert not (tmp_path/'archive').exists() and not (tmp_path/'operation-journal').exists()


@pytest.mark.parametrize('lost',['commit','rollback','closed'])
def test_original_snapshot_cannot_survive_a_new_transaction_on_same_connection(lost):
    from sqlalchemy import create_engine, text
    engine=create_engine('sqlite://')
    with engine.connect() as connection:
        # Real SQLAlchemy/DBAPI transaction lifecycle; the server PID function
        # remains constant to catch a guard that checks only the connection/PID.
        connection.connection.driver_connection.create_function('pg_backend_pid',0,lambda:1718)
        connection.execute(text('SELECT 1'))
        check=load().retained_snapshot_guard(connection)
        check()
        if lost=='closed':
            connection.close()
        else:
            getattr(connection,lost)()
            connection.execute(text('SELECT 1'))
        with pytest.raises(ValueError):
            check()
    engine.dispose()
