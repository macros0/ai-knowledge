"""Explicit file-selected offline CLI; SQL commits held before identity publication."""
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, MaintenanceState, ReplicaRuntimeIdentity
from app.db.session import get_engine
from app.services.work.maintenance_control import MaintenanceOperator
from tests.test_replica_compact_adoption import COUNTS, WRITERS, corpus as corpus, prepared as prepared, source_snapshot


def load():
    path=Path(__file__).resolve().parents[1]/'scripts/adopt_compact_runtime.py'
    assert path.is_file(), 'Explicit compact adoption CLI is missing'
    spec=importlib.util.spec_from_file_location('compact_adoption_cli',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def setup_cli(prepared,tmp_path,monkeypatch):
    settings,receipt,_=prepared
    module=load()
    env=tmp_path/'runtime.env';env.write_text('PRIVATE_CANARY=must-not-be-echoed\n')
    source=tmp_path/'source.json';source.write_text(json.dumps(source_snapshot(settings)))
    receipts=tmp_path/'receipt.json';receipts.write_text(json.dumps(receipt))
    counts=tmp_path/'counts.json';counts.write_text(json.dumps(COUNTS))
    writers=tmp_path/'writers.json';writers.write_text(json.dumps(WRITERS))
    def configured(path):
        assert Path(path)==env
        return settings
    monkeypatch.setattr(module,'_load_settings',configured)
    monkeypatch.setattr(module,'configure_database',lambda url:None)
    def owned(engine,**kwargs):return MaintenanceOperator(engine,**kwargs,allow_sqlite=True)
    monkeypatch.setattr(module,'MaintenanceOperator',owned)
    args=['--runtime-env-file',str(env),'--source-snapshot',str(source),'--operation-id',uuid4().hex,'--owner-id','cli-test',
        '--storage-receipt',str(receipts),'--counts',str(counts),'--writer-receipts',str(writers)]
    return module,settings,source,args


def result(capsys):
    captured=capsys.readouterr()
    lines=(captured.out+captured.err).strip().splitlines()
    value=lines[-1]
    prefix='REPLICA_COMPACT_ADOPTION_IDENTITY:'
    return json.loads(value.removeprefix(prefix))


def test_dry_run_reads_no_database_and_creates_no_identity(prepared,tmp_path,monkeypatch,capsys):
    module,settings,_,args=setup_cli(prepared,tmp_path,monkeypatch)
    def forbidden(*args,**kwargs):raise AssertionError('Dry-run opened database')
    monkeypatch.setattr(module,'configure_database',forbidden)
    assert module.main(['--phase','adopt',*args])==0
    report=result(capsys)
    assert report['status']=='planned' and report['mutations']==0 and report['database_observed'] is False
    assert not settings.runtime_identity_file.exists()


def test_inspect_publishes_a_create_only_snapshot_without_installation(prepared,tmp_path,monkeypatch,capsys):
    module,settings,source,args=setup_cli(prepared,tmp_path,monkeypatch)
    source.unlink()
    assert module.main(['--phase','inspect',*args])==0
    report=result(capsys)
    assert report['status']=='inspected' and report['mutations']==0
    assert json.loads(source.read_bytes())==source_snapshot(settings)
    assert not settings.runtime_identity_file.exists()
    before=source.read_bytes();source.write_text('{}')
    assert module.main(['--phase','inspect',*args])==2
    assert source.read_bytes()==b'{}' and before!=b'{}'


def test_apply_commits_held_and_identical_retry_publishes_only_one_identity(prepared,tmp_path,monkeypatch,capsys):
    module,settings,_,args=setup_cli(prepared,tmp_path,monkeypatch)
    for _ in range(2):
        assert module.main(['--phase','adopt','--apply',*args])==0
        report=result(capsys)
        assert report['jobs_held'] is True
        assert json.loads(settings.runtime_identity_file.read_bytes())==report['identity']
    with get_engine().connect() as connection:
        assert connection.scalar(select(MaintenanceState.enabled)) is True
        assert len(connection.execute(select(AuditLog.id)).all())==2


def test_publication_failure_leaves_held_sql_and_same_operation_can_resume(prepared,tmp_path,monkeypatch,capsys):
    module,settings,_,args=setup_cli(prepared,tmp_path,monkeypatch)
    original=module.write_identity
    def fail(*args):raise OSError('must-not-be-echoed secret destination')
    monkeypatch.setattr(module,'write_identity',fail)
    assert module.main(['--phase','adopt','--apply',*args])==2
    report=result(capsys);assert report['jobs_held'] is True and 'must-not' not in json.dumps(report)
    with get_engine().connect() as connection:
        assert connection.scalar(select(MaintenanceState.enabled)) is True
        assert connection.scalar(select(ReplicaRuntimeIdentity.id))==1
    monkeypatch.setattr(module,'write_identity',original)
    assert module.main(['--phase','adopt','--apply',*args])==0
    assert result(capsys)['jobs_held'] is True


@pytest.mark.parametrize('fault',['private-extra','duplicate-key','wrong-receipt','missing-writers','malformed-counts','colliding-output'])
def test_private_malformed_inputs_refuse_before_sql_mutation(prepared,tmp_path,monkeypatch,capsys,fault):
    module,settings,source,args=setup_cli(prepared,tmp_path,monkeypatch)
    if fault=='private-extra':
        value=json.loads(source.read_bytes());value['password']='must-not-be-echoed';source.write_text(json.dumps(value))
    elif fault=='duplicate-key':source.write_text('{"schema_version":1,"schema_version":1,"secret":"must-not-be-echoed"}')
    elif fault=='wrong-receipt':
        path=Path(args[args.index('--storage-receipt')+1]);value=json.loads(path.read_bytes());value['namespace']='foreign'
        path.write_text(json.dumps(value))
    elif fault=='missing-writers':Path(args[args.index('--writer-receipts')+1]).write_text('{}')
    elif fault=='malformed-counts':Path(args[args.index('--counts')+1]).write_text('{"api":true}')
    else:settings.runtime_identity_file=source
    assert module.main(['--phase','adopt','--apply',*args])==2
    report=result(capsys);assert 'must-not-be-echoed' not in json.dumps(report)
    with get_engine().connect() as connection:assert connection.scalar(select(ReplicaRuntimeIdentity.id)) is None


def test_explicit_runtime_file_ignores_ambient_environment_and_interpolation(prepared,tmp_path,monkeypatch):
    module=load();settings,_,_=prepared
    path=tmp_path/'literal.env'
    values={'ENVIRONMENT':'development','APP_NAME':'literal-${ADOPTION_CANARY}','APP_SECRET_KEY':'a'*64,
        'DATABASE_URL':settings.db_url,'DATA_DIR':str(settings.data_dir),'DIAGNOSTICS_DIR':str(settings.diagnostics_dir),
        'OKF_DEPLOYMENT_SCHEMA':'replicas-v1','DEPLOYMENT_MODE':'distributed','APP_ROLE':'api',
        'DEPLOYMENT_NAMESPACE':settings.deployment_namespace,'ARTIFACT_BACKEND':'rwx',
        'ARTIFACT_ROOT':str(settings.artifact_root),'ARTIFACT_STORE_ID':settings.artifact_store_id,
        'ARTIFACT_UPLOADS_ENABLED':'true','RUNTIME_IDENTITY_FILE':str(settings.runtime_identity_file),
        'RUNTIME_IMAGE_ID':settings.runtime_image_id,'FRONTEND_REPLICAS':'2','API_REPLICAS':'2','WORKER_REPLICAS':'2',
        'WORK_RETRY_BACKOFF_SECONDS':'[1,2,5]'}
    path.write_text('\n'.join(key+'='+json.dumps(value) for key,value in values.items()))
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://foreign:must-not-be-echoed@production/production')
    monkeypatch.setenv('APP_SECRET_KEY','b'*64);monkeypatch.setenv('ADOPTION_CANARY','must-not-be-echoed')
    actual=module._load_settings(path)
    assert actual.db_url==settings.db_url and actual.app_name=='literal-${ADOPTION_CANARY}'
    assert actual.app_secret_key=='a'*64 and actual.work_retry_backoff_seconds==(1,2,5)
