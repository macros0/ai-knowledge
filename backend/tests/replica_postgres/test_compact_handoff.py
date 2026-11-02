"""Atomic legacy handoff on real PostgreSQL, after the official Alembic chain."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import MaintenanceState, ReplicaRuntimeIdentity, WorkItem
from app.db.session import get_engine
from app.services.work.maintenance_control import MaintenanceOperator
from tests.support.replica_postgres import PrivatePostgresUrl
from tests.replica_postgres.test_compact_adoption import official_schema as official_schema
from tests.test_replica_compact_adoption import corpus as corpus, source_snapshot
from tests.test_replica_compact_handoff import (apply, plan, queued_prepared as queued_prepared,
    test_audit_failure_rolls_back_new_work_inputs_and_hold_then_retries as rollback_contract,
    test_reviewed_plan_preserves_every_original_row_and_never_executes as preservation_contract)
from tests.test_replica_compact_handoff_variants import (checkpoint_prepared as checkpoint_prepared,
    test_migrated_checkpoint_continuation_freezes_original_policy_without_generation_synthesis as checkpoint_contract)


@pytest.fixture
def native(queued_prepared):
    settings,receipt,task=queued_prepared
    settings.database_url=PrivatePostgresUrl(get_engine().url.render_as_string(hide_password=False))
    return settings,receipt,task


def test_pg_handoff_preserves_queues_approval_and_same_held_retry(native,monkeypatch):
    preservation_contract(native,monkeypatch)


def test_pg_handoff_audit_failure_rolls_back_all_admissions(native,monkeypatch):
    rollback_contract(native,monkeypatch)


def test_pg_migrated_checkpoint_handoff_retains_policy_and_pointer(checkpoint_prepared):
    settings,_,_=checkpoint_prepared
    settings.database_url=PrivatePostgresUrl(get_engine().url.render_as_string(hide_password=False))
    checkpoint_contract(checkpoint_prepared)


def test_pg_handoff_conflicting_old_writer_leaves_no_partial_new_work(native):
    settings,receipt,_=native;source=source_snapshot(settings);manifest=plan(settings,source)
    with get_engine().connect() as writer, MaintenanceOperator(get_engine(),operation_id=uuid4().hex,
            owner_id='native-handoff',settings=settings) as op:
        transaction=writer.begin()
        try:
            writer.execute(text('UPDATE jobs SET status=status'))
            with pytest.raises(DBAPIError) as failure:apply(op,settings,source,receipt,manifest)
            assert failure.value.orig.sqlstate=='55P03'
            with get_engine().connect() as connection:
                assert connection.scalar(select(ReplicaRuntimeIdentity.id)) is None
                assert connection.scalar(select(MaintenanceState.id)) is None
                assert len(connection.execute(select(WorkItem)).all())==1
        finally:transaction.rollback()
        apply(op,settings,source,receipt,manifest)


@pytest.mark.parametrize('publication_failure',[False,True])
def test_pg_handoff_literal_cli_publication_and_resume(native,tmp_path,monkeypatch,capsys,publication_failure):
    from tests.test_replica_compact_adoption import COUNTS, WRITERS
    from tests.test_replica_compact_adoption_cli import load, result
    settings,receipt,_=native;tool=load()
    runtime,source,handoff,probe,counts,writers=[tmp_path/name for name in (
        'runtime.env','source.json','handoff.json','probe.json','counts.json','writers.json')]
    values={'ENVIRONMENT':'development','APP_SECRET_KEY':settings.app_secret_key,'DATABASE_URL':settings.db_url,
        'DATA_DIR':str(settings.data_dir),'DIAGNOSTICS_DIR':str(settings.diagnostics_dir),
        'OKF_DEPLOYMENT_SCHEMA':'replicas-v1','DEPLOYMENT_MODE':'distributed','APP_ROLE':'api',
        'DEPLOYMENT_NAMESPACE':settings.deployment_namespace,'ARTIFACT_BACKEND':'rwx',
        'ARTIFACT_ROOT':str(settings.artifact_root),'ARTIFACT_STORE_ID':settings.artifact_store_id,
        'ARTIFACT_UPLOADS_ENABLED':'true','RUNTIME_IDENTITY_FILE':str(settings.runtime_identity_file),
        'RUNTIME_IMAGE_ID':settings.runtime_image_id,'FRONTEND_REPLICAS':'2','API_REPLICAS':'2','WORKER_REPLICAS':'2'}
    runtime.write_text('\n'.join(key+'='+json.dumps(value) for key,value in values.items()))
    args=['--runtime-env-file',str(runtime),'--source-snapshot',str(source),'--handoff-plan',str(handoff),
        '--operation-id',uuid4().hex,'--owner-id','native-handoff-cli']
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://foreign:private-canary@production/production')
    assert tool.main(['--phase','inspect',*args])==0
    assert result(capsys)['handoff_plan_written'] is True
    for path,value in ((probe,receipt),(counts,COUNTS),(writers,WRITERS)):path.write_text(json.dumps(value))
    args+=['--storage-receipt',str(probe),'--counts',str(counts),'--writer-receipts',str(writers)]
    publish=tool.write_identity
    if publication_failure:
        def unavailable(*_args):raise OSError('synthetic private-canary publication failure')
        monkeypatch.setattr(tool,'write_identity',unavailable)
        assert tool.main(['--phase','adopt','--apply',*args])==2
        report=result(capsys);assert report['jobs_held'] is True and 'private-canary' not in json.dumps(report)
        monkeypatch.setattr(tool,'write_identity',publish)
    for _ in range(2):
        assert tool.main(['--phase','adopt','--apply',*args])==0
        assert result(capsys)['handoff_intents']==8
    with get_engine().connect() as connection:
        assert len(connection.execute(select(WorkItem)).all())==9
        assert connection.scalar(select(MaintenanceState.enabled)) is True
