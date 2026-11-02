"""Nonempty compact adoption is explicit, source-bound and always starts held."""
from datetime import datetime, timedelta, timezone
import importlib
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.db.models import (ArtifactStorageControl, ArtifactStorageMigration, AuditLog, AuditOutbox, AuthSession,
    Document, DocumentGenerationState, GlossaryState, Job, MaintenanceState, ReplicaRuntimeIdentity, WorkItem)
from app.db.session import get_engine, session_scope
from app.services.artifacts.control import migration_access
from app.services.work.maintenance_control import MaintenanceOperator, MaintenanceRefused, REQUIRED_WRITERS, assert_writes_allowed
from tests.test_artifact_storage_migration import backup, corpus as corpus

COUNTS={'frontend':2,'api':2,'worker':2}
WRITERS={name:'stopped' for name in REQUIRED_WRITERS}


def module():
    name='app.deployment.compact_adoption'
    assert importlib.util.find_spec(name) is not None, 'Offline compact adoption bridge is missing'
    return importlib.import_module(name)


@pytest.fixture
def prepared(corpus):
    from app.deployment.bootstrap import OperatorSettings, probe_artifacts
    from app.services.artifacts.local import LocalBlobStore
    from app.services.artifacts.migration import StorageMigration, installation_id
    from app.services.artifacts.rwx import RwxBlobStore
    source,root=corpus
    doc_id='0000000000000001'
    bundle=source.okf_dir/doc_id;bundle.mkdir(parents=True)
    (bundle/'accepted.md').write_text('accepted legacy text',encoding='utf-8')
    with session_scope() as session:
        session.execute(text('CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)'))
        revisions=session.execute(text('SELECT version_num FROM alembic_version')).scalars().all()
        if not revisions:session.execute(text("INSERT INTO alembic_version VALUES ('rpl05_maintenance_epoch')"))
        else:assert revisions==['rpl05_maintenance_epoch']  # Native fixtures run the real Alembic chain first.
        session.add(DocumentGenerationState(doc_id=doc_id))
        state=session.get(GlossaryState,1)
        if state is None:session.add(GlossaryState(id=1,revision=7,identities_ready=True))
        else:state.revision=7
        session.add(AuthSession(id='kept-cookie',external_id='legacy-user',identity={'keep':'private-canary'},
            expires_at=datetime.now(timezone.utc)+timedelta(days=1)))
        session.add(Job(job_type='bulk_generate',status='awaiting_approval',created_by='legacy-admin',params={'ids':[doc_id]}))
        session.add(WorkItem(id='5'*32,kind='artifact_gc',payload_version=1,payload_json={},dedupe_key='legacy-queued'))
        session.add(AuditLog(action_type='legacy_action',target_type='fixture',target_id='preserve',new_value={'keep':True}))
        session.add(AuditOutbox(event_id=str(uuid4()),payload='{"old":"opaque legacy event"}',status='held'))
    shared=root/'shared';shared.mkdir()
    install=installation_id()
    old={'version':1,'installation_id':install,'store':{'backend':'local','store_id':'source','root':str(source.artifact_root)}}
    new={'version':1,'installation_id':install,'store':{'backend':'rwx','store_id':'target','root':str(shared)}}
    task=StorageMigration(source,old,new,root/'transfer/plan.json',stores={
        'source':LocalBlobStore(source.artifact_root,store_id='source'),'target':RwxBlobStore(shared,store_id='target')})
    proof=backup(task,root)
    task.copy(apply=True,writers_stopped=True,backup=proof)
    task.cutover(apply=True,writers_stopped=True,backup=proof)
    target=OperatorSettings(_env_file=None,data_dir=root/'cold',diagnostics_dir=root/'cold-diagnostics',
        database_url='postgresql+psycopg://fixture:private-canary@db/replica_test_compact',app_secret_key='a'*64,
        okf_deployment_schema='replicas-v1',deployment_mode='distributed',app_role='api',
        deployment_namespace='replica-test-compactfixture',artifact_backend='rwx',artifact_root=shared,
        artifact_store_id='target',artifact_uploads_enabled=True,frontend_replicas=2,api_replicas=2,worker_replicas=2,
        runtime_image_id='okf-backend@sha256:'+'9'*64,runtime_identity_file=root/'private/identity.json')
    receipt=probe_artifacts(target,installation_id=install,selection_digest='sha256:'+'b'*64)
    return target,receipt,task


def source_snapshot(settings):
    with session_scope() as session:return module().capture_compact_source(session,settings)


def adopt(op,settings,snapshot,receipt,**kwargs):
    with op.transaction() as session:
        return module().adopt_compact_runtime(session,settings,snapshot,receipt,operation_id=op.operation_id,
            intended_counts=kwargs.get('counts',dict(COUNTS)),writer_receipts=kwargs.get('writers',dict(WRITERS)))


def operator(settings,operation=None,owner='compact-test'):
    return MaintenanceOperator(get_engine(),operation_id=operation or uuid4().hex,owner_id=owner,settings=settings,allow_sqlite=True)


def test_real_s4_cutover_legacy_nulls_queue_config_and_auth_remain_held(prepared):
    settings,receipt,_=prepared
    snapshot=source_snapshot(settings)
    assert snapshot['document_count']==2 and snapshot['legacy_document_count']==2 and snapshot['queued_work_count']==1
    with operator(settings) as op:
        document=adopt(op,settings,snapshot,receipt)
        assert adopt(op,settings,snapshot,receipt)==document
        with op.transaction() as session:
            state=session.get(MaintenanceState,1)
            assert state.enabled and state.epoch==1 and state.operation_id==op.operation_id
            assert session.get(ArtifactStorageControl,1).maintenance_token==op.operation_id
            assert session.get(ReplicaRuntimeIdentity,1).fingerprint==document['runtime_fingerprint']
            assert session.get(DocumentGenerationState,'0000000000000001').active_generation_id is None
            assert session.get(DocumentGenerationState,'0000000000000002') is None
            assert session.get(WorkItem,'5'*32).status=='queued'
            assert session.get(GlossaryState,1).revision==7
            assert session.get(AuthSession,'kept-cookie').identity=={'keep':'private-canary'}
            assert session.scalar(select(Job.status))=='awaiting_approval'
            assert session.scalar(select(text('count(*)')).select_from(AuditLog))==2
            assert document['installation_id']==snapshot['installation_id']
    with pytest.raises(MaintenanceRefused),session_scope() as session:assert_writes_allowed(session,None)
    assert not settings.runtime_identity_file.exists()


@pytest.mark.parametrize('fault',['document','null-pointer','config','auth','audit','outbox','work','readonly-roots'])
def test_changed_source_refuses_instead_of_adopting_another_corpus(prepared,fault):
    settings,receipt,_=prepared;snapshot=source_snapshot(settings)
    # Simulate an explicit offline/admin mutation, rather than weakening the
    # application guard that correctly rejects the retired source Settings.
    with migration_access(),session_scope() as session:
        if fault=='document':session.get(Document,'0000000000000001').filename='changed.docx'
        elif fault=='null-pointer':session.get(DocumentGenerationState,'0000000000000001').candidate_generation_id='f'*32
        elif fault=='config':session.get(GlossaryState,1).revision=8
        elif fault=='auth':session.get(AuthSession,'kept-cookie').identity={'changed':True}
        elif fault=='audit':session.scalar(select(AuditLog)).new_value={'changed':True}
        elif fault=='outbox':session.scalar(select(AuditOutbox)).payload='{"changed":true}'
        elif fault=='work':session.get(WorkItem,'5'*32).payload_json={'changed':True}
        else:session.get(ArtifactStorageControl,1).readonly_roots=['/changed-root']
    with operator(settings) as op,pytest.raises(MaintenanceRefused):adopt(op,settings,snapshot,receipt)
    with session_scope() as session:assert session.get(ReplicaRuntimeIdentity,1) is None and session.get(MaintenanceState,1) is None


@pytest.mark.parametrize('fault',['history','backup','selection','running','owner','lease','unknown-kind','unknown-version','retired-epoch','unconverted-job','unconverted-document'])
def test_unverified_migration_or_uncertain_legacy_intent_is_not_activated(prepared,fault):
    settings,receipt,_=prepared
    with migration_access(),session_scope() as session:
        control=session.get(ArtifactStorageControl,1);work=session.get(WorkItem,'5'*32)
        if fault=='history':session.get(ArtifactStorageMigration,control.migration_id).verified_hash=None
        elif fault=='backup':session.get(ArtifactStorageMigration,control.migration_id).backup_hash='not-a-digest'
        elif fault=='selection':control.active_store_id='other'
        elif fault=='running':work.status='running'
        elif fault=='owner':work.owner_id='retired-owner'
        elif fault=='lease':work.lease_until=datetime.now(timezone.utc)
        elif fault=='unknown-kind':work.kind='unsupported_legacy'
        elif fault=='unknown-version':work.payload_version=999
        elif fault=='retired-epoch':work.maintenance_epoch=1
        elif fault=='unconverted-job':session.add(Job(job_type='bulk_export',status='queued',params={}))
        else:session.get(Document,'0000000000000002').status='processing'
    with pytest.raises(MaintenanceRefused):
        snapshot=source_snapshot(settings)
        with operator(settings) as op:adopt(op,settings,snapshot,receipt)


@pytest.mark.parametrize('fault',['extra-table','extra-column','old-schema'])
def test_unknown_or_incomplete_schema_cannot_be_stamped_as_ready(prepared,fault):
    settings,_,_=prepared
    with get_engine().begin() as connection:
        connection.execute(text({'extra-table':'CREATE TABLE unknown_data(id INTEGER PRIMARY KEY)',
            'extra-column':'ALTER TABLE documents ADD COLUMN unknown_data TEXT',
            'old-schema':"UPDATE alembic_version SET version_num='rpl04_runtime_identity'"}[fault]))
    with pytest.raises(MaintenanceRefused):source_snapshot(settings)


@pytest.mark.parametrize('fault',['owner','operation','target','receipt','writers','counts','released'])
def test_held_retry_cannot_be_rebound_to_another_authority(prepared,fault):
    settings,receipt,_=prepared;snapshot=source_snapshot(settings)
    operation=uuid4().hex
    with operator(settings,operation) as op:adopt(op,settings,snapshot,receipt)
    changed=settings.model_copy(deep=True)
    if fault=='target':changed.runtime_image_id='okf-backend@sha256:'+'8'*64
    if fault=='receipt':receipt={**receipt,'selection_digest':'sha256:'+'c'*64}
    if fault=='released':
        with operator(settings,operation) as op:
            from app.services.work.maintenance_control import leave_maintenance
            with op.transaction() as session:leave_maintenance(session,operation,writer_receipts=WRITERS)
    with operator(changed,uuid4().hex if fault=='operation' else operation,'foreign' if fault=='owner' else 'compact-test') as op:
        with pytest.raises(MaintenanceRefused):adopt(op,changed,snapshot,receipt,
            writers={} if fault=='writers' else WRITERS,counts={**COUNTS,'api':True} if fault=='counts' else COUNTS)


def test_audit_failure_rolls_back_fence_and_identity(prepared,monkeypatch):
    settings,receipt,_=prepared;snapshot=source_snapshot(settings)
    def failed(*args,**kwargs):raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr('app.services.audit.record_in_session',failed)
    with operator(settings) as op,pytest.raises(RuntimeError):adopt(op,settings,snapshot,receipt)
    assert source_snapshot(settings)==snapshot
    with session_scope() as session:assert session.get(ReplicaRuntimeIdentity,1) is None and session.get(MaintenanceState,1) is None


def test_fresh_bootstrap_is_still_not_a_nonempty_adoption_bypass(prepared):
    settings,receipt,_=prepared
    from app.deployment.bootstrap import install_identity
    with session_scope() as session,pytest.raises(ValueError):install_identity(session,settings,receipt)


def test_unowned_session_cannot_install_even_with_complete_source_proof(prepared):
    settings,receipt,_=prepared;snapshot=source_snapshot(settings)
    with session_scope() as session,pytest.raises(MaintenanceRefused):
        module().adopt_compact_runtime(session,settings,snapshot,receipt,operation_id=uuid4().hex,
            intended_counts=COUNTS,writer_receipts=WRITERS)


def test_invalid_new_audit_payload_is_a_typed_refusal_and_keeps_installation_held(prepared):
    settings,receipt,_=prepared;snapshot=source_snapshot(settings);operation=uuid4().hex
    with operator(settings,operation) as op:adopt(op,settings,snapshot,receipt)
    with migration_access(),session_scope() as session:
        event=session.scalar(select(AuditLog).where(AuditLog.id>snapshot['audit_log_upper_id']))
        row=session.scalar(select(AuditOutbox).where(AuditOutbox.id>snapshot['audit_outbox_upper_id']))
        if row is None:session.add(AuditOutbox(event_id=event.event_id,payload='[]'))
        else:row.payload='[]'
    with operator(settings,operation) as op,pytest.raises(MaintenanceRefused):adopt(op,settings,snapshot,receipt)
    with get_engine().connect() as connection:
        assert connection.scalar(select(MaintenanceState.enabled)) is True
