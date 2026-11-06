"""Actual scope mode contracts and migrated checkpoint preservation."""
import pytest
from sqlalchemy import select

from app.db.models import (Document, DocumentGeneration, DocumentGenerationState, DocumentProcessingReview,
    DocumentScopeChange, DocumentStaging, DocumentWorkInput, Job, WorkItem)
from app.db.session import session_scope
from app.services.artifacts.control import migration_access
from app.services.artifacts.repository import resolve_blob
from app.services.processing_scope import selection_hash
from app.services.work.maintenance_control import MaintenanceRefused
from tests.test_replica_compact_adoption import adopt, corpus as corpus, operator, prepared as prepared, source_snapshot
from tests.test_replica_compact_handoff import apply, owner_rows, plan


def policy(source_hash):
    scope={'schema_version':1,'source_file_hash':source_hash,'parser_version':'accepted-parser',
        'traversal_limits':{},'inventory':{'nodes':[],'complete':True},'decisions':[]}
    scope['selection_hash']=selection_hash(source_hash,'accepted-parser',{},[])
    return scope


@pytest.mark.parametrize('mode',['fast_exclude','reuse_only','incremental_generate'])
def test_queued_scope_recipe_uses_actual_worker_modes_and_preserves_accepted_plan(prepared,mode):
    from app.services.artifacts.publication import canonical_hash
    from app.services.processing_scope_change import ScopeChangePlan, SCOPE_CHANGE_ANALYSIS_VERSION
    settings,receipt,_=prepared
    with migration_access(),session_scope() as session:
        doc_id='0000000000000001';base='b'*32;review_id='c'*32;request='d'*32
        ref=resolve_blob(session,owner_type='original',owner_id=doc_id,generation_id=None,logical_name='original')
        scope=policy(ref.sha256)
        preview=ScopeChangePlan(mode=mode,base_generation_id=base,selection_hash=scope['selection_hash'],mapping_status='verified')
        value={'schema_version':1,'analysis_version':SCOPE_CHANGE_ANALYSIS_VERSION,'preview':preview.to_dict(),
            'snapshot':scope,'review_id':review_id,'revision':1,'input_digest':'a'*64,'canonical_digest':'a'*64}
        session.add(DocumentGeneration(id=base,doc_id=doc_id,phase='active'))
        session.get(DocumentGenerationState,doc_id).active_generation_id=base
        session.get(Document,doc_id).status='queued'
        session.add(DocumentProcessingReview(doc_id=doc_id,id=review_id,state='submitted',request_id=request,
            source_file_hash=ref.sha256,parser_version='accepted-parser',assessor_version='fixture',policy_json={},
            inventory_json=scope['inventory'],selection_json=[],selection_hash=scope['selection_hash']))
        session.add(DocumentScopeChange(id='e'*32,doc_id=doc_id,state='queued',request_id=request,plan_json=value))
    source=source_snapshot(settings);manifest=plan(settings,source);before=owner_rows()
    assert len(manifest['entries'])==1 and manifest['entries'][0]['recipe']=='scope_change.v1'
    with operator(settings) as op:
        apply(op,settings,source,receipt,manifest)
        with op.transaction() as session:
            work=session.get(WorkItem,manifest['entries'][0]['work_id'])
            assert work.kind=='scope_change' and work.payload_json['input_hash']==canonical_hash(value)
            assert session.scalar(select(DocumentWorkInput)) is None
    assert owner_rows()==before


@pytest.fixture
def checkpoint_prepared(corpus,monkeypatch):
    from app.models.schemas import Concept
    from app.services.staging import StagingStore
    from tests.test_replica_compact_adoption import prepared as factory
    source,root=corpus;doc_id='0000000000000002';generation='e'*32
    monkeypatch.setattr('app.services.staging.get_settings',lambda:source)
    with session_scope() as session:
        doc=session.get(Document,doc_id);doc.status='processing';source_hash=doc.file_hash
        scope=policy(source_hash)
        session.add(DocumentGeneration(id=generation,doc_id=doc_id,phase='preparing',processing_scope_json=scope))
        session.add(DocumentGenerationState(doc_id=doc_id,candidate_generation_id=generation))
    stage=StagingStore(doc_id)
    stage.create(1,generation_id=generation,parser_version='accepted-parser',source_file_hash=source_hash,
        processing_scope_hash=scope['selection_hash'])
    stage.save_chunk_text(0,'accepted checkpoint text')
    stage.append_chunk(0,[Concept(id='accepted',title='Accepted',content='accepted output')])
    return factory.__wrapped__((source,root))


def test_migrated_checkpoint_continuation_freezes_original_policy_without_generation_synthesis(checkpoint_prepared):
    settings,receipt,_=checkpoint_prepared;source=source_snapshot(settings);manifest=plan(settings,source);before=owner_rows()
    assert len(manifest['entries'])==1 and manifest['entries'][0]['recipe']=='document_resume.v1'
    with operator(settings) as op:
        apply(op,settings,source,receipt,manifest)
        with op.transaction() as session:
            frozen=session.get(DocumentWorkInput,manifest['entries'][0]['work_id'])
            generation=session.get(DocumentGeneration,'e'*32)
            assert frozen.processing_scope_json==generation.processing_scope_json
            assert session.get(DocumentStaging,'0000000000000002').checkpoint_version==2
            assert session.get(DocumentGenerationState,'0000000000000002').candidate_generation_id==generation.id
            assert session.get(WorkItem,frozen.work_id).payload_json['action']=='resume'
            assert len(list(session.scalars(select(DocumentGeneration))))==1
        assert apply(op,settings,source,receipt,manifest)
    assert owner_rows()==before


@pytest.mark.parametrize('fault',['checkpoint-version','scope-hash','owned-generation'])
def test_incompatible_checkpoint_or_owner_is_never_silently_regenerated(checkpoint_prepared,fault):
    settings,_,_=checkpoint_prepared
    with migration_access(),session_scope() as session:
        row=session.get(DocumentStaging,'0000000000000002')
        if fault=='checkpoint-version':row.checkpoint_version=1
        elif fault=='scope-hash':row.processing_scope_hash='f'*64
        else:session.get(DocumentGeneration,row.generation_id).work_id='5'*32
    with pytest.raises(MaintenanceRefused):plan(settings,source_snapshot(settings))


def test_existing_supported_durable_job_is_preserved_without_duplicate_admission(prepared):
    settings,receipt,_=prepared
    with migration_access(),session_scope() as session:
        job=Job(job_type='bulk_resume',status='queued',params={'doc_ids':['0000000000000001']})
        session.add(job);session.flush()
        session.add(WorkItem(id='d'*32,kind='bulk',payload_version=1,payload_json={'job_id':job.id},dedupe_key=f'bulk/{job.id}'))
    source=source_snapshot(settings)
    assert plan(settings,source)['entries']==[]
    with operator(settings) as op:
        adopt(op,settings,source,receipt)
        with op.transaction() as session:assert len(list(session.scalars(select(WorkItem))))==2
