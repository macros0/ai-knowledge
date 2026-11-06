"""Reviewed legacy intent handoff commits with a held identity, never execution."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import (AuditLog, DiagnosticBundle, Document, DocumentGenerationState, DocumentScopeChange,
    DocumentWorkInput, Job, MaintenanceState, ReplicaRuntimeIdentity, WorkItem)
from app.db.session import get_engine, session_scope
from app.services.artifacts.control import migration_access
from app.services.work.maintenance_control import MaintenanceRefused
from tests.test_replica_compact_adoption import (
    COUNTS, WRITERS, adopt, corpus as corpus, module as adoption, operator, prepared as prepared, source_snapshot,
)


def handoff():
    name='app.deployment.compact_handoff'
    assert importlib.util.find_spec(name) is not None, 'Versioned legacy handoff is missing'
    return importlib.import_module(name)


def plan(settings,source):
    with session_scope() as session:
        return handoff().plan_compact_handoff(session,settings,source)


def apply(op,settings,source,receipt,manifest):
    with op.transaction() as session:
        return adoption().adopt_compact_runtime(session,settings,source,receipt,operation_id=op.operation_id,
            intended_counts=COUNTS,writer_receipts=WRITERS,handoff=manifest)


@pytest.fixture
def queued_prepared(corpus,monkeypatch):
    from app.auth.models import User
    from app.services.export_queue import ExportQueue
    from tests.test_replica_compact_adoption import prepared as factory
    settings,root=corpus
    settings.artifact_uploads_enabled=False
    settings.bulk_export_min_free_mb=0
    monkeypatch.setattr('app.services.export_queue.get_settings',lambda:settings)
    actor=User(user_id='legacy-admin',username='Legacy Admin',roles=['admin'])
    queue=ExportQueue(start_worker=False)
    queue.submit(['0000000000000001'],actor,ip_address=None)
    now=datetime.now(timezone.utc)
    with session_scope() as session:
        for kind in ('bulk_delete','bulk_regenerate','bulk_resume'):
            session.add(Job(job_type=kind,status='queued',created_by='Legacy Admin',created_by_id='legacy-admin',
                approved_by='Second Admin' if kind=='bulk_regenerate' else None,
                approved_at=now if kind=='bulk_regenerate' else None,params={'doc_ids':['0000000000000001'],'ip_address':None}))
        common={'locale':'en','user_id':'legacy-admin','username':'Legacy Admin','ip_address':None}
        session.add(Job(job_type='reference_translation',status='queued',params={**common,'entities':['tags']}))
        session.add(Job(job_type='glossary_translation',status='queued',params={**common,'term_ids':[1],
            'expected_translation_versions':{'1':0}}))
        # Shared protocol2 input; local-journal protocol1 needs a separate
        # migration and is tested as an explicit refusal below.
        session.add(DiagnosticBundle(id=str(uuid4()),status='queued',created_by_id='legacy-admin',created_by='Legacy Admin',
            cutoff_at=now,request={'session_id':str(uuid4())},artifact_version=2))
        session.get(Document,'0000000000000002').status='queued'
    # Reuse the real S4 copy/cutover, after seeding the compact public queues.
    return factory.__wrapped__((settings,root))


def owner_rows():
    from app.db.models import AuthSession, DocumentGeneration, DocumentProcessingReview, DocumentStaging, GlossaryState
    result={}
    with get_engine().connect() as connection:
        for model in (Job,Document,DiagnosticBundle,DocumentGeneration,DocumentGenerationState,
                DocumentProcessingReview,DocumentStaging,AuthSession,GlossaryState):
            result[model.__tablename__]=connection.execute(select(model.__table__).order_by(*model.__table__.primary_key)).all()
    return result


@pytest.mark.parametrize('fault',['diagnostic','splitting','indexing','scope-owner','approval-work'])
def test_unconverted_persistent_intents_cannot_slip_through_adoption(prepared,fault):
    settings,receipt,_=prepared
    with migration_access(),session_scope() as session:
        if fault=='diagnostic':
            session.add(DiagnosticBundle(id=str(uuid4()),status='queued',created_by_id='admin',created_by='Admin',
                cutoff_at=datetime.now(timezone.utc),request={'session_id':str(uuid4())}))
        elif fault in {'splitting','indexing'}:session.get(Document,'0000000000000002').status=fault
        elif fault=='scope-owner':
            session.add(DocumentScopeChange(id='e'*32,doc_id='0000000000000001',state='admitted',plan_json={},
                owner_token='f'*32,lease_expires_at=datetime.now(timezone.utc)-timedelta(days=1)))
        else:
            job=session.scalar(select(Job))
            session.add(WorkItem(id='e'*32,kind='bulk',payload_version=1,payload_json={'job_id':job.id},dedupe_key=f'bulk/{job.id}'))
    source=source_snapshot(settings)
    with operator(settings) as op,pytest.raises(MaintenanceRefused):adopt(op,settings,source,receipt)


def test_reviewed_plan_preserves_every_original_row_and_never_executes(queued_prepared,monkeypatch):
    settings,receipt,_=queued_prepared
    source=source_snapshot(settings);before=owner_rows();manifest=plan(settings,source)
    assert manifest['schema_version']==1 and len(manifest['entries'])==8
    assert len({entry['work_id'] for entry in manifest['entries']})==8
    assert manifest==plan(settings,source) and source_snapshot(settings)==source
    from app.services.work.runtime import build_handlers
    registry=build_handlers()
    def forbidden(*_args,**_kwargs):pytest.fail('Handoff executed queued work')
    for key in registry._handlers:registry._handlers[key]=forbidden
    monkeypatch.setattr('app.services.work.runtime.build_handlers',lambda:registry)
    with operator(settings) as op:
        identity=apply(op,settings,source,receipt,manifest)
        assert apply(op,settings,source,receipt,manifest)==identity
        with op.transaction() as session:
            items=list(session.scalars(select(WorkItem)))
            assert len(items)==9 and all(item.status=='queued' and item.attempts==0 and item.fence==0 for item in items)
            assert all(item.owner_id is None and item.lease_until is None for item in items)
            frozen=session.scalar(select(DocumentWorkInput))
            assert frozen.doc_id=='0000000000000002' and frozen.processing_scope_json is None
            assert session.get(MaintenanceState,1).enabled is True
            assert len(list(session.scalars(select(AuditLog).where(AuditLog.id>source['audit_log_upper_id']))))==1
    assert owner_rows()==before


@pytest.mark.parametrize('fault',['omitted','extra','duplicate','unknown-recipe','wrong-work-id','changed-source','version','private-field'])
def test_changed_or_partial_reviewed_plan_refuses_whole_operation(queued_prepared,fault):
    settings,receipt,_=queued_prepared;source=source_snapshot(settings);manifest=plan(settings,source)
    if fault=='omitted':manifest['entries'].pop()
    elif fault=='extra':manifest['entries'].append({**manifest['entries'][0],'source_id':'999999'})
    elif fault=='duplicate':manifest['entries'].append(manifest['entries'][0])
    elif fault=='unknown-recipe':manifest['entries'][0]['recipe']='arbitrary.v1'
    elif fault=='wrong-work-id':manifest['entries'][0]['work_id']='f'*32
    elif fault=='changed-source':manifest['source_sql_sha256']='0'*64
    elif fault=='version':manifest['schema_version']=True
    else:manifest['private_key']='never-echo-this-input'
    with operator(settings) as op,pytest.raises(MaintenanceRefused):apply(op,settings,source,receipt,manifest)
    with get_engine().connect() as connection:
        assert connection.scalar(select(ReplicaRuntimeIdentity.id)) is None
        assert len(connection.execute(select(WorkItem)).all())==1


def test_audit_failure_rolls_back_new_work_inputs_and_hold_then_retries(queued_prepared,monkeypatch):
    settings,receipt,_=queued_prepared;source=source_snapshot(settings);manifest=plan(settings,source)
    def unavailable(*_args,**_kwargs):raise RuntimeError('synthetic audit failure')
    with operator(settings) as op:
        with monkeypatch.context() as patch:
            patch.setattr('app.services.audit.record_in_session',unavailable)
            with pytest.raises(RuntimeError,match='synthetic audit failure'):apply(op,settings,source,receipt,manifest)
        assert source_snapshot(settings)==source
        with op.transaction() as session:
            assert session.get(MaintenanceState,1) is None and session.scalar(select(DocumentWorkInput)) is None
            assert len(list(session.scalars(select(WorkItem))))==1
        apply(op,settings,source,receipt,manifest)


@pytest.mark.parametrize('fault',['payload','attempt','fence','cancel','available','frozen-input','removed-input'])
def test_same_held_retry_verifies_new_rows_instead_of_blindly_excluding_them(queued_prepared,fault):
    settings,receipt,_=queued_prepared;source=source_snapshot(settings);manifest=plan(settings,source)
    with operator(settings) as op:
        apply(op,settings,source,receipt,manifest)
        with op.transaction() as session:
            item=session.get(WorkItem,next(entry['work_id'] for entry in manifest['entries'] if entry['recipe']=='document_resume.v1'))
            if fault=='payload':item.payload_json={**item.payload_json,'action':'regenerate'}
            elif fault=='attempt':item.attempts=1
            elif fault=='fence':item.fence=1
            elif fault=='cancel':item.cancel_requested=True
            elif fault=='available':item.available_at=item.available_at+timedelta(seconds=1)
            else:
                frozen=session.get(DocumentWorkInput,item.id)
                if fault=='removed-input':session.delete(frozen)
                else:frozen.processing_scope_json={'changed':True}
        with pytest.raises(MaintenanceRefused):apply(op,settings,source,receipt,manifest)
        with op.transaction() as session:assert session.get(MaintenanceState,1).enabled is True


def test_unknown_or_running_legacy_recipe_stays_refused(queued_prepared):
    settings,_,_=queued_prepared
    with migration_access(),session_scope() as session:
        job=session.scalar(select(Job).where(Job.job_type=='bulk_resume'));job.status='running'
    source=source_snapshot(settings)
    with pytest.raises(MaintenanceRefused):plan(settings,source)


def test_source_and_approval_are_revalidated_after_review(queued_prepared):
    settings,receipt,_=queued_prepared;source=source_snapshot(settings);manifest=plan(settings,source)
    with migration_access(),session_scope() as session:
        job=session.scalar(select(Job).where(Job.job_type=='bulk_regenerate'));job.approved_by='different'
    with operator(settings) as op,pytest.raises(MaintenanceRefused):apply(op,settings,source,receipt,manifest)


def test_handoff_is_explicit_even_when_all_recipes_are_known(queued_prepared):
    settings,receipt,_=queued_prepared;source=source_snapshot(settings)
    with operator(settings) as op,pytest.raises(MaintenanceRefused):adopt(op,settings,source,receipt)


def test_unowned_session_cannot_apply_even_the_reviewed_plan(queued_prepared):
    settings,receipt,_=queued_prepared;source=source_snapshot(settings);manifest=plan(settings,source)
    with session_scope() as session,pytest.raises(MaintenanceRefused):
        adoption().adopt_compact_runtime(session,settings,source,receipt,handoff=manifest,
            operation_id=uuid4().hex,intended_counts=deepcopy(COUNTS),writer_receipts=WRITERS)


def test_legacy_local_diagnostic_inputs_require_explicit_journal_migration(prepared):
    settings,_,_=prepared
    with migration_access(),session_scope() as session:
        session.add(DiagnosticBundle(id=str(uuid4()),status='queued',created_by_id='admin',created_by='Admin',
            cutoff_at=datetime.now(timezone.utc),request={'session_id':str(uuid4())},artifact_version=1))
    with pytest.raises(MaintenanceRefused):plan(settings,source_snapshot(settings))


def test_progress_without_accepted_checkpoint_requires_reconciliation(prepared):
    settings,_,_=prepared
    with migration_access(),session_scope() as session:
        row=session.get(Document,'0000000000000002');row.status='indexing';row.processed_chunks=1;row.total_chunks=1
    with pytest.raises(MaintenanceRefused):plan(settings,source_snapshot(settings))
