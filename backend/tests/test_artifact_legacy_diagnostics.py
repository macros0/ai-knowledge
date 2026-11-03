"""S4 carries local diagnostic history and the exact pending bundle inputs."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import (DiagnosticBundle, DiagnosticInstance, DiagnosticSegment, DiagnosticSession,
    DocumentGenerationState, WorkItem)
from app.db.session import session_scope
from app.services.artifacts.control import migration_access
from app.services.artifacts.repository import resolve_blob
from app.services.diagnostics.sanitize import encode_event
from app.services.diagnostics.store import DiagnosticStore
from tests.test_artifact_storage_migration import corpus as corpus
from tests.test_diagnostics_store import encoded


def files(root):
    return {path.relative_to(root).as_posix():sha256(path.read_bytes()).hexdigest()
        for path in root.rglob('*') if path.is_file()}


@pytest.fixture
def legacy(corpus,monkeypatch):
    source,root=corpus
    source.diagnostics_min_free_mb=0
    now=datetime.now(timezone.utc);cutoff=now-timedelta(seconds=10);session_id=str(uuid4());bundle_id=str(uuid4())
    before=json.loads(encoded());before['timestamp_utc']=(cutoff-timedelta(seconds=1)).isoformat()
    captured={**before,'event_id':str(uuid4()),'diagnostic_session_id':session_id}
    later={**captured,'event_id':str(uuid4()),'timestamp_utc':(cutoff+timedelta(seconds=1)).isoformat()}
    with DiagnosticStore(source.diagnostics_dir,source.diagnostics_limits()) as store:
        assert store.append(encode_event(before),stream='baseline')
        assert store.append(encode_event(captured),stream=session_id)
        assert store.append(encode_event(later),stream=session_id)
        store.retire_capture(session_id,now-timedelta(seconds=2))
        from app.services.diagnostics.control import write_projection
        write_projection(store,boot_id=str(uuid4()),active=None,now=now)
    with session_scope() as session:
        session.add(DiagnosticSession(id=session_id,status='stopped',scope='system',boot_id=str(uuid4()),
            created_by_id='operator',created_by='Operator',created_at=now-timedelta(hours=1),expires_at=now+timedelta(hours=1),
            stopped_at=now-timedelta(seconds=2),stop_reason='manual',bytes_written=len(encode_event(captured))+len(encode_event(later))))
        session.add(DiagnosticBundle(id=bundle_id,status='queued',created_by_id='operator',created_by='Operator',
            created_at=cutoff,cutoff_at=cutoff,request={'from_utc':(cutoff-timedelta(hours=1)).isoformat(),
                'to_utc':cutoff.isoformat()},artifact_version=1))
    return dict(source=source,root=root,session_id=session_id,bundle_id=bundle_id,cutoff=cutoff,
        expected_ids={before['event_id'],captured['event_id']},captured_id=captured['event_id'],later_id=later['event_id'],source_files=files(source.diagnostics_dir))


def migrate(legacy):
    from tests.test_replica_compact_adoption import prepared as factory
    return factory.__wrapped__((legacy['source'],legacy['root']))


def test_s4_transfers_all_retained_journal_bytes_and_freezes_pending_request(legacy):
    settings,_,task=migrate(legacy)
    with migration_access(),session_scope() as session:
        row=session.get(DiagnosticBundle,legacy['bundle_id'])
        assert row.status=='queued' and row.artifact_version==2
        frozen=row.manifest['frozen_bundle']
        assert frozen['version']==1 and frozen['cutoff_at']==legacy['cutoff'].isoformat()
        ref=resolve_blob(session,owner_type='diagnostic_bundle',owner_id=row.id,generation_id=None,logical_name='frozen-input.zip')
        assert ref is not None and ref.store_id==settings.artifact_store_id and frozen['ref']['sha256']==ref.sha256
        imported=list(session.scalars(select(DiagnosticSegment)))
        assert sum(row.event_count for row in imported)==3
        assert {event['event_id'] for row in imported for event in map(json.loads,row.payload.splitlines())}==legacy['expected_ids']|{legacy['later_id']}
        assert all(sha256(row.payload.encode()).hexdigest()==row.sha256 for row in imported)
        from app.services.work.store import _utc
        expected_chunks={chunk['id']:chunk for item in task.plan()['diagnostics']['history'] for chunk in item['chunks']}
        for row in imported:
            expected=expected_chunks[row.id]
            assert _utc(row.created_at)==datetime.fromisoformat(expected['created_at'])
            assert _utc(row.expires_at)==datetime.fromisoformat(expected['expires_at'])
        marker=json.loads((legacy['source'].diagnostics_dir/'capture-expiry'/(legacy['session_id']+'.json')).read_bytes())
        assert all(_utc(row.expires_at).timestamp()==marker['expires_at'] for row in imported if row.stream==legacy['session_id'])
        assert all(row.closed_at is not None and row.counters=={} for row in session.scalars(select(DiagnosticInstance)))
        assert session.get(DocumentGenerationState,'0000000000000001').active_generation_id is None
        assert len(list(session.scalars(select(WorkItem))))==1
    assert files(legacy['source'].diagnostics_dir)==legacy['source_files']
    assert task.plan()['diagnostics']['version']==1


@pytest.mark.parametrize('fault',['extra-file','changed-file','changed-control-sql'])
def test_copy_does_not_authorize_changed_or_new_legacy_diagnostic_input(legacy,monkeypatch,fault):
    from app.services.artifacts.migration import StorageMigration
    original=StorageMigration.cutover
    def cutover(task,**kwargs):
        if fault=='extra-file':
            directory=legacy['source'].diagnostics_dir/'events/baseline'
            (directory/('00000000000000000000-'+uuid4().hex+'.jsonl')).write_bytes(encoded())
        elif fault=='changed-file':
            next((legacy['source'].diagnostics_dir/'events/baseline').glob('*.jsonl')).write_bytes(encoded())
        else:
            with migration_access(),session_scope() as session:
                session.get(DiagnosticSession,legacy['session_id']).stop_reason='server_restarted'
        return original(task,**kwargs)
    monkeypatch.setattr(StorageMigration,'cutover',cutover)
    with pytest.raises(ValueError):migrate(legacy)
    with migration_access(),session_scope() as session:
        assert session.get(DiagnosticBundle,legacy['bundle_id']).artifact_version==1
        assert session.scalar(select(DiagnosticSegment.id)) is None


def test_active_capture_is_not_retired_by_elapsed_time_or_writer_claim(legacy):
    with session_scope() as session:
        row=session.get(DiagnosticSession,legacy['session_id']);row.status='active';row.active_slot=1
        row.expires_at=datetime.now(timezone.utc)-timedelta(days=1)
    with pytest.raises(ValueError):migrate(legacy)


def test_pending_unreconciled_control_journal_cannot_disappear_during_s4(legacy):
    path=legacy['source'].diagnostics_dir/'control/deferred.jsonl'
    path.write_text(json.dumps({'event_id':str(uuid4()),'target_id':legacy['session_id'],
        'action':'diagnostic_session_stopped','reason':'manual','timestamp_utc':legacy['cutoff'].isoformat(),
        'bytes_written':1,'user_id':'operator','username':'Operator'})+'\n')
    with pytest.raises(ValueError):migrate(legacy)


def test_diagnostic_cutover_failure_rolls_back_sql_and_resumes_original_plan(legacy,monkeypatch):
    import importlib
    name='app.services.artifacts.diagnostic_migration'
    assert importlib.util.find_spec(name) is not None, 'Legacy diagnostic S4 importer is missing'
    module=importlib.import_module(name);original=module.apply_diagnostics;seen=[]
    from app.services.artifacts.migration import StorageMigration
    real_cutover=StorageMigration.cutover
    def capture(task,**kwargs):seen.append((task,kwargs));return real_cutover(task,**kwargs)
    def unavailable(*args,**kwargs):original(*args,**kwargs);raise RuntimeError('synthetic diagnostic import failure')
    monkeypatch.setattr(StorageMigration,'cutover',capture)
    monkeypatch.setattr(module,'apply_diagnostics',unavailable)
    with pytest.raises(RuntimeError,match='synthetic diagnostic import failure'):migrate(legacy)
    with migration_access(),session_scope() as session:
        assert session.scalar(select(DiagnosticSegment.id)) is None
        assert session.scalar(select(DiagnosticInstance.id)) is None
        assert session.get(DiagnosticBundle,legacy['bundle_id']).artifact_version==1
    monkeypatch.setattr(module,'apply_diagnostics',original)
    task,kwargs=seen[-1]
    assert real_cutover(task,**kwargs)['cutover'] is True
    assert real_cutover(task,**kwargs)['cutover'] is True
    assert files(legacy['source'].diagnostics_dir)==legacy['source_files']


def test_backup_source_revision_includes_diagnostic_control_sql(legacy):
    from tests.test_artifact_storage_migration import migration
    from app.services.artifacts.migration import StorageMigration
    first=migration((legacy['source'],legacy['root']))
    before=first.plan()
    with session_scope() as session:session.get(DiagnosticSession,legacy['session_id']).stop_reason='server_restarted'
    second=StorageMigration(legacy['source'],first.source,first.target,legacy['root']/'other-transfer/plan.json',stores=first.stores)
    after=second.plan()
    assert before['source_sql_revision']==after['source_sql_revision']  # Existing ordinary-corpus hash is unchanged.
    assert before['source_revision']!=after['source_revision']


def test_backup_inside_explicit_frontend_source_is_not_independent(legacy):
    from tests.test_artifact_storage_migration import migration, backup
    from app.services.artifacts.migration import StorageMigration
    first=migration((legacy['source'],legacy['root']))
    frontend=legacy['root']/'backup-frontend-source';frontend.mkdir()
    task=StorageMigration(legacy['source'],first.source,first.target,first.path,stores=first.stores,legacy_frontend_root=frontend)
    proof=backup(task,frontend)
    with pytest.raises(ValueError):task._backup(task.plan(),proof)


def test_live_legacy_writer_lock_prevents_even_a_plan(legacy):
    with DiagnosticStore(legacy['source'].diagnostics_dir,legacy['source'].diagnostics_limits()):
        with pytest.raises(ValueError):migrate(legacy)


@pytest.mark.parametrize('fault',['truncated','hardlink','foreign-field','frontend-writer','active-projection'])
def test_unsafe_legacy_journal_is_refused_without_partial_sql(legacy,fault):
    import os
    from app.services.artifacts.migration import StorageMigration
    from tests.test_artifact_storage_migration import migration
    source=legacy['source'];path=next((source.diagnostics_dir/'events/baseline').glob('*.jsonl'))
    if fault=='truncated':path.write_bytes(path.read_bytes()[:-1])
    elif fault=='hardlink':os.link(path,path.with_name('00000000000000000000-'+uuid4().hex+'.jsonl'))
    elif fault=='foreign-field':
        value=json.loads(path.read_bytes());value['unknown_sensitive_field']='must-not-be-accepted'
        path.write_text(json.dumps(value)+'\n')
    elif fault=='active-projection':
        (source.diagnostics_dir/'control/capture.json').write_text(json.dumps({
            'schema_version':2,'boot_id':str(uuid4()),'active':True,'revision':1,'lease_until':0}))
    task=migration((source,legacy['root']))
    if fault=='frontend-writer':
        root=legacy['root']/'frontend';root.mkdir();(root/'writer.json').write_text('{}')
        task=StorageMigration(source,task.source,task.target,task.path,stores=task.stores,legacy_frontend_root=root)
    with pytest.raises(ValueError):task.plan()
    with migration_access(),session_scope() as session:
        assert session.scalar(select(DiagnosticSegment.id)) is None
        assert session.get(DiagnosticBundle,legacy['bundle_id']).artifact_version==1


def test_second_s4_transfer_rebinds_frozen_input_and_queued_work_together(legacy):
    from app.services.artifacts.migration import StorageMigration
    from app.services.artifacts.rwx import RwxBlobStore
    from app.services.diagnostics.frozen_bundle import frozen_spec
    from app.services.work.store import enqueue
    from tests.test_artifact_storage_migration import backup
    settings,_,first=migrate(legacy)
    with migration_access(),session_scope() as session:
        row=session.get(DiagnosticBundle,legacy['bundle_id']);before=frozen_spec(session,row,settings)
        work_id=enqueue(session,before)
    source=settings.model_copy(deep=True);source.app_role='combined'
    destination=legacy['root']/'second-store';destination.mkdir()
    target={'version':1,'installation_id':first.source['installation_id'],
        'store':{'backend':'rwx','store_id':'second-target','root':str(destination)}}
    task=StorageMigration(source,first.target,target,legacy['root']/'second-transfer/plan.json',
        stores={'target':first.store(first.target['store']),'second-target':RwxBlobStore(destination,store_id='second-target')})
    backup_root=legacy['root']/'second-backup';backup_root.mkdir()
    proof=backup(task,backup_root)
    task.copy(apply=True,writers_stopped=True,backup=proof)
    task.cutover(apply=True,writers_stopped=True,backup=proof)
    selected=settings.model_copy(update={'artifact_root':destination,'artifact_store_id':'second-target'})
    with migration_access(),session_scope() as session:
        row=session.get(DiagnosticBundle,legacy['bundle_id']);after=frozen_spec(session,row,selected)
        assert after.payload['input_hash']!=before.payload['input_hash']
        work=session.get(WorkItem,work_id)
        assert work.payload_json==after.payload and work.status=='queued' and work.attempts==0
        from app.services.work.store import _utc
        assert row.status=='queued' and _utc(row.cutoff_at)==legacy['cutoff']
        assert sum(item.event_count for item in session.scalars(select(DiagnosticSegment)))==3
