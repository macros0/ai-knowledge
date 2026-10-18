"""Migrator defaults to read-only and never repeats an uncertain queue write."""
import pytest
from app.db.models import Document,DocumentSource
from app.db.session import session_scope
from docparser.source_model import PARSER_VERSION
from scripts.reprocess_table_documents import plan_run,apply_batch
from tests.test_generation_pipeline import pipeline_env as pipeline_env


def test_dry_run_includes_successful_legacy_xlsx_and_nested_xlsx():
 with session_scope() as s:
  s.add_all([Document(id='old-xlsx',filename='ok.xlsx',status='done',problem=None),
             Document(id='nested',filename='report.docx',status='done'),
             Document(id='prose',filename='report.txt',status='done')]);s.flush()
  s.add(DocumentSource(doc_id='nested',source_id='root/0',kind='attachment',display_name='table.xlsx'))
 with session_scope() as s:
  before=s.query(Document).count();run=plan_run(s,target_version=PARSER_VERSION)
  assert {d['doc_id'] for d in run['documents']}=={'old-xlsx','nested'}
  assert all(d['status']=='planned' for d in run['documents'])
  assert s.query(Document).count()==before


def test_apply_and_resume_submit_only_one_bounded_job():
 class API:
  calls=0
  def runtime(self):return {'parser_version':PARSER_VERSION,'table_structure_enabled':True,'chunk_projection_version':1}
  def document(self,d):return {'parser_version':None}
  def submit(self,ids):self.calls+=1;return {'id':1,'status':'awaiting_approval'}
  def job(self,id):return {'id':id,'status':'awaiting_approval'}
 with session_scope() as s:
  s.add(Document(id='one',filename='one.xlsx',status='done'));s.flush();run=plan_run(s,target_version=PARSER_VERSION,limit=1)
 api=API();saved=[]
 apply_batch(run,api,lambda m:saved.append(m['submission']))
 apply_batch(run,api,lambda m:saved.append(m['submission']))
 assert api.calls==1 and saved==['unknown','sent']


def test_lost_queue_response_is_not_blindly_retried():
 class API:
  def runtime(self):return {'parser_version':PARSER_VERSION,'table_structure_enabled':True,'chunk_projection_version':1}
  def document(self,d):return {'parser_version':None}
  def submit(self,ids):raise TimeoutError()
 with session_scope() as s:
  s.add(Document(id='one',filename='one.xlsx',status='done'));s.flush();run=plan_run(s,target_version=PARSER_VERSION)
 with pytest.raises(TimeoutError):apply_batch(run,API(),lambda m:None)
 assert run['submission']=='unknown'
 with pytest.raises(RuntimeError,match='Unknown queue submission'):apply_batch(run,API(),lambda m:None)


def test_current_parser_without_structural_publication_is_still_planned():
 from app.services.generation_store import begin_generation,mark_generation_ready,publish_generation
 with session_scope() as s:
  s.add(Document(id='route-disabled',filename='wide.xlsx',status='done',parser_version=PARSER_VERSION));s.flush()
  gen=begin_generation(s,'route-disabled').id;mark_generation_ready(s,'route-disabled',gen);publish_generation(s,'route-disabled',gen)
 with session_scope() as s:
  run=plan_run(s,target_version=PARSER_VERSION)
  assert [d['doc_id'] for d in run['documents']]==['route-disabled']

@pytest.mark.parametrize('parser_suffix', ['', '-depth-6', '-mail-disabled-depth-0', '-scope-v1'])
def test_reconcile_reports_verified_candidate_and_observed_rollback(pipeline_env, parser_suffix):
 from app.services.generation_publication import publish_prepared_document
 from tests.test_generation_pipeline import DOC_ID
 from tests.test_chunk_fragment_publication import prepare
 from scripts.reprocess_table_documents import reconcile
 from app.db.models import DocumentGenerationState,DocumentGeneration
 pipeline, _, _ = pipeline_env
 with session_scope() as s:
  old=s.get(DocumentGenerationState,DOC_ID).active_generation_id
  run=plan_run(s,target_version=PARSER_VERSION,doc_ids=[DOC_ID])
 new, _, _ = prepare(pipeline,long=True)
 with session_scope() as s:s.get(DocumentGeneration,new).table_schema_version=1
 reconcile(run,{'status':'running'})
 assert run['documents'][0]['status']=='verified'
 publish_prepared_document(pipeline.settings,pipeline.vector_store,DOC_ID,new)
 with session_scope() as s:s.get(Document,DOC_ID).parser_version=PARSER_VERSION+parser_suffix
 reconcile(run,{'status':'done'})
 assert run['documents'][0]['status']=='published'
 with session_scope() as s:
  s.get(DocumentGenerationState,DOC_ID).active_generation_id=old
  s.get(DocumentGeneration,old).phase='active';s.get(DocumentGeneration,new).phase='retired'
 reconcile(run,{'status':'done'})
 assert run['documents'][0]['status']=='rolled_back'


@pytest.mark.parametrize("version", [None, True, 999, "1"])
def test_apply_refuses_missing_or_unsupported_projection_runtime_before_enqueue(version):
 class API:
  calls=0
  def runtime(self):return {'parser_version':PARSER_VERSION,'table_structure_enabled':True,'chunk_projection_version':version}
  def document(self,d):return {'parser_version':None}
  def submit(self,ids):self.calls+=1;return {'id':1}
 with session_scope() as s:
  s.add(Document(id='projection-runtime',filename='one.xlsx',status='done'));s.flush();run=plan_run(s,target_version=PARSER_VERSION)
 api=API()
 with pytest.raises(RuntimeError,match='projection'):apply_batch(run,api,lambda m:None)
 assert api.calls==0 and run['submission']=='not_sent'


def test_dry_run_does_not_skip_current_tables_without_generic_format():
 from app.services.generation_store import begin_generation,mark_generation_ready,publish_generation
 with session_scope() as s:
  s.add(Document(id='current-legacy',filename='wide.xlsx',status='done',parser_version=PARSER_VERSION));s.flush()
  gen=begin_generation(s,'current-legacy').id;mark_generation_ready(s,'current-legacy',gen,table_schema_version=1);publish_generation(s,'current-legacy',gen)
 with session_scope() as s:
  run=plan_run(s,target_version=PARSER_VERSION)
  assert [d['doc_id'] for d in run['documents']]==['current-legacy']
  assert run['chunk_projection_version']==1


@pytest.mark.parametrize('suffix,current', [
 ('',True), ('-depth-6',True), ('-mail-disabled-depth-0',True),
 ('-scope-v1',True), ('-mail-disabled-scope-v1',True), ('-depth-32',True),
 ('-depth-33',False), ('-depth-06',False), ('-scope-v1-depth-6',False),
 ('-unknown',False), ('-mail-disabled-extra',False),
])
def test_plan_recognizes_only_supported_parser_release_profiles(suffix,current):
 from app.services.generation_store import begin_generation,mark_generation_ready,publish_generation
 with session_scope() as s:
  s.add(Document(id='profile',filename='table.xlsx',status='done',parser_version=PARSER_VERSION+suffix));s.flush()
  gen=begin_generation(s,'profile',chunk_projection_version=1).id
  mark_generation_ready(s,'profile',gen,table_schema_version=1)
  publish_generation(s,'profile',gen)
 with session_scope() as s:
  run=plan_run(s,target_version=PARSER_VERSION)
  assert [d['doc_id'] for d in run['documents']]==([] if current else ['profile'])



def test_known_job_can_be_polled_after_writer_is_disabled():
 class API:
  def runtime(self):raise AssertionError('Known job polling must not require enabling new writes')
  def job(self,id):return {'id':id,'status':'running'}
 with session_scope() as s:
  s.add(Document(id='known-job',filename='one.xlsx',status='done'));s.flush();run=plan_run(s,target_version=PARSER_VERSION)
 run['job_id']=7;run['submission']='sent'
 assert apply_batch(run,API(),lambda m:None)=={'id':7,'status':'running'}


def test_reconcile_never_reports_legacy_marker_as_target_publication():
 from app.services.generation_store import begin_generation,mark_generation_ready,publish_generation
 from scripts.reprocess_table_documents import reconcile
 with session_scope() as s:
  s.add(Document(id='wrong-target',filename='one.xlsx',status='done'));s.flush()
  old=begin_generation(s,'wrong-target').id;mark_generation_ready(s,'wrong-target',old);publish_generation(s,'wrong-target',old)
 with session_scope() as s:run=plan_run(s,target_version=PARSER_VERSION)
 with session_scope() as s:
  new=begin_generation(s,'wrong-target').id;mark_generation_ready(s,'wrong-target',new,table_schema_version=1);publish_generation(s,'wrong-target',new)
  s.get(Document,'wrong-target').parser_version=PARSER_VERSION
 reconcile(run,{'status':'done'})
 assert run['documents'][0]['status']=='failed' and run['documents'][0]['failure_code']=='target_not_published'


@pytest.mark.parametrize("phase", ["ready", "active"])
@pytest.mark.parametrize("damage", ["content", "descriptor", "hash", "missing-parents"])
def test_reconcile_rejects_corrupt_generic_representation(pipeline_env, phase, damage):
 from sqlalchemy import select, delete
 from app.db.models import DocumentGeneration, DocumentChunkFragment, DocumentChunkProjection
 from app.services.generation_artifacts import GenerationIntegrityError
 from app.services.generation_publication import publish_prepared_document
 from tests.test_generation_pipeline import DOC_ID
 from tests.test_chunk_fragment_publication import prepare
 from scripts.reprocess_table_documents import reconcile
 pipeline, _, _ = pipeline_env
 with session_scope() as s:run=plan_run(s,target_version=PARSER_VERSION,doc_ids=[DOC_ID])
 gen, _, _ = prepare(pipeline,long=True)
 with session_scope() as s:s.get(DocumentGeneration,gen).table_schema_version=1
 if phase == "active":
  publish_prepared_document(pipeline.settings,pipeline.vector_store,DOC_ID,gen)
  with session_scope() as s:s.get(Document,DOC_ID).parser_version=PARSER_VERSION
 with session_scope() as s:
  if damage == "missing-parents":
   s.execute(delete(DocumentChunkFragment).where(DocumentChunkFragment.generation_id==gen))
   s.execute(delete(DocumentChunkProjection).where(DocumentChunkProjection.generation_id==gen))
  else:
   fragment=s.scalar(select(DocumentChunkFragment).where(DocumentChunkFragment.generation_id==gen).order_by(DocumentChunkFragment.ordinal))
   if damage == "content":fragment.content="CORRUPTED"
   elif damage == "hash":fragment.text_hash="0"*64
   else:fragment.descriptor={**fragment.descriptor,"end":999}
 with pytest.raises(GenerationIntegrityError):reconcile(run,{'status':'running' if phase=='ready' else 'done'})
 assert run['documents'][0]['status'] not in {'verified','published'}


def test_reconcile_ready_requires_hashed_prepared_publication(pipeline_env):
 from app.db.models import DocumentGeneration
 from app.services.generation_artifacts import GenerationIntegrityError
 from tests.test_generation_pipeline import DOC_ID
 from tests.test_chunk_fragment_publication import prepare
 from scripts.reprocess_table_documents import reconcile
 pipeline, _, _ = pipeline_env
 with session_scope() as s:run=plan_run(s,target_version=PARSER_VERSION,doc_ids=[DOC_ID])
 gen, _, path = prepare(pipeline,long=True)
 with session_scope() as s:s.get(DocumentGeneration,gen).table_schema_version=1
 path.write_text('{}',encoding='utf-8')
 with pytest.raises(GenerationIntegrityError):reconcile(run,{'status':'running'})
 assert run['documents'][0]['status'] != 'verified'


def test_reconcile_accepts_table_ref_only_without_generic_parents(pipeline_env,tmp_path):
 from tests.test_chunk_writer_policy import test_public_table_ref_only_route_has_no_generic_parents
 from scripts.reprocess_table_documents import reconcile
 pipeline, _, _ = pipeline_env
 test_public_table_ref_only_route_has_no_generic_parents(pipeline_env,tmp_path)
 with session_scope() as s:
  run=plan_run(s,target_version=PARSER_VERSION,doc_ids=['public-marker-table'])
  assert run['documents']==[]
 run['documents']=[{'doc_id':'public-marker-table','old_generation_id':None,'status':'prepared'}]
 reconcile(run,{'status':'done'})
 assert run['documents'][0]['status']=='published'
