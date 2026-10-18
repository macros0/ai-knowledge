"""Read-only default, resumable bounded batches through the authenticated API queue."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, inspect, text
from app.db.models import Document, DocumentGenerationState, DocumentSource
from app.db.session import session_scope
from app.services.json_atomic import write_json_atomic
from docparser.source_model import PARSER_VERSION
from docparser.attachment_limits import MAX_CONFIGURABLE_ATTACHMENT_DEPTH


def _parser_release_versions(target_version):
    # The migration targets table/projection formats for a parser release.
    # Mail, reviewed-scope and depth profiles retain that release identity;
    # unknown suffixes must still be reparsed rather than accepted by prefix.
    versions=set()
    for mail_suffix in ('', '-mail-disabled'):
        base=target_version+mail_suffix
        versions.update((base, base+'-scope-v1'))
        versions.update(f'{base}-depth-{depth}' for depth in range(MAX_CONFIGURABLE_ATTACHMENT_DEPTH+1))
    return versions


def plan_run(session, *, target_version, doc_ids=None, limit=5):
    if target_version != PARSER_VERSION or not 1 <= limit <= 100:
        raise ValueError('Unsupported target version or batch limit')
    rows=[]
    target_versions=_parser_release_versions(target_version)
    generation_columns={c['name'] for c in inspect(session.get_bind()).get_columns('document_generations')}
    structure_column='table_schema_version' in generation_columns
    projection_column='chunk_projection_version' in generation_columns
    query=select(Document).where(Document.deleted_at.is_(None)).order_by(Document.id)
    if doc_ids is not None:
        query=query.where(Document.id.in_(doc_ids))
    for doc in session.scalars(query):
        sources=session.execute(select(DocumentSource.display_name,DocumentSource.kind).where(DocumentSource.doc_id==doc.id)).all()
        excel=doc.filename.lower().endswith('.xlsx') or any((name or '').lower().endswith('.xlsx') or kind=='xlsx' for name,kind in sources)
        # Explicit IDs also allow the no-table control in an isolated pilot.
        if not excel and doc_ids is None:
            continue
        state=session.get(DocumentGenerationState,doc.id)
        structure = session.execute(text('SELECT table_schema_version FROM document_generations WHERE id=:id'),
                                    {'id':state.active_generation_id}).scalar() if structure_column and state else None
        projection = session.execute(text('SELECT chunk_projection_version FROM document_generations WHERE id=:id'),
                                     {'id':state.active_generation_id}).scalar() if projection_column and state else None
        if doc.parser_version in target_versions and structure == 1 and projection == 1:
            continue
        rows.append({'doc_id':doc.id,'old_generation_id':state.active_generation_id if state else None,
                     'new_generation_id':None,'old_parser_version':doc.parser_version,'target_version':target_version,
                     'old_chunk_projection_version':projection,
                     'status':'planned','reason':'xlsx_reparse' if excel else 'explicit_control'})
        if len(rows)==limit: break
    return {'schema_version':2,'run_id':uuid4().hex,'created_at':datetime.now(timezone.utc).isoformat(),
            'target_version':target_version,'table_schema_version':1,'chunk_projection_version':1,'documents':rows,'job_id':None,'submission':'not_sent'}


def apply_batch(manifest, api, save):
    """One batch only. Ambiguous POST outcomes require explicit job reconciliation.

    Save intent before enqueueing so a lost response never blindly repeats a job.
    API handles admin authentication, rate limits, admission and four-eyes.
    """
    if manifest['submission'] == 'unknown':
        raise RuntimeError('Unknown queue submission; reconcile its job ID before resuming')
    if type(manifest.get('chunk_projection_version')) is not int or manifest['chunk_projection_version'] != 1:
        raise RuntimeError('Unsupported manifest chunk projection version; create a fresh dry-run')
    if manifest['job_id'] is not None:
        return api.job(manifest['job_id'])
    runtime=api.runtime()
    if runtime.get('parser_version') != manifest['target_version'] or runtime.get('table_structure_enabled') is not True:
        raise RuntimeError('Backend target version or structural route does not match')
    if type(runtime.get('chunk_projection_version')) is not int or runtime['chunk_projection_version'] != 1:
        raise RuntimeError('Backend chunk projection route does not match version 1')
    pending=[d for d in manifest['documents'] if d['status']=='planned']
    if not pending:
        return None
    for doc in pending:
        current=api.document(doc['doc_id'])
        if current.get('parser_version') != doc['old_parser_version']:
            raise RuntimeError('Document changed since planning; create a fresh dry-run')
    manifest['submission']='unknown'; save(manifest)
    job=api.submit([d['doc_id'] for d in pending])
    manifest['job_id']=job['id']; manifest['submission']='sent'; save(manifest)
    return job


def reconcile(manifest, job):
    from app.db.models import DocumentGeneration
    from app.services.table_store import verify_generation_tables
    from app.services.chunk_projection_publication import verify_chunk_projection_publication
    from app.services.generation_artifacts import load_verified_publication, GenerationIntegrityError
    from app.config import get_settings
    from app.db.models import DocumentChunk
    if type(manifest.get("chunk_projection_version")) is not int or manifest["chunk_projection_version"] != 1:
        raise RuntimeError("Unsupported manifest chunk projection version; create a fresh dry-run")
    with session_scope() as session:
        for entry in manifest['documents']:
            doc=session.get(Document,entry['doc_id']); state=session.get(DocumentGenerationState,entry['doc_id'])
            if entry['status'] in {'published','rolled_back'} and state and state.active_generation_id == entry['old_generation_id']:
                entry['status']='rolled_back'
                continue  # Observes rollback; does not perform or certify backup restoration.
            if state and state.candidate_generation_id:
                candidate=session.get(DocumentGeneration,state.candidate_generation_id)
                entry['new_generation_id']=state.candidate_generation_id
                entry['status']='prepared'
                if (candidate and candidate.phase=='ready' and candidate.table_schema_version==1
                        and candidate.chunk_projection_version==manifest['chunk_projection_version']):
                    verify_generation_tables(session,entry['doc_id'],candidate.id)
                    prepared=load_verified_publication(get_settings(),entry['doc_id'],candidate.id,candidate.publication_hash)
                    version=prepared.get('chunk_projection_version')
                    if type(version) is not int or version != candidate.chunk_projection_version:
                        raise GenerationIntegrityError()
                    verify_chunk_projection_publication(session,entry['doc_id'],candidate.id,prepared['chunks'])
                    entry['status']='verified'
            active=session.get(DocumentGeneration,state.active_generation_id) if state and state.active_generation_id else None
            if doc and active and active.id != entry['old_generation_id'] and doc.parser_version in _parser_release_versions(manifest['target_version']) and active.table_schema_version==1 and active.chunk_projection_version==manifest['chunk_projection_version']:
                verify_generation_tables(session,doc.id,active.id)
                chunks=session.execute(select(DocumentChunk.chunk_index,DocumentChunk.section_title,
                    DocumentChunk.content,DocumentChunk.content_hash,DocumentChunk.source_id,DocumentChunk.table_refs)
                    .where(DocumentChunk.doc_id==doc.id).order_by(DocumentChunk.chunk_index)
                    .execution_options(yield_per=1)).mappings()
                verify_chunk_projection_publication(session,doc.id,active.id,chunks)
                entry['new_generation_id']=active.id
                entry['status']='published'
            elif job and job['status'] in {'failed','done','completed','cancelled'}:
                entry['status']='failed'
                entry['failure_code']='target_not_published'
    manifest['queue_status']=job.get('status') if job else None


class QueueAPI:
    def __init__(self, base, authorization):
        import httpx
        if not base.startswith(('http://127.0.0.1:','https://')):
            raise ValueError('Use an explicit loopback or HTTPS backend URL')
        self.client=httpx.Client(base_url=base.rstrip('/')+'/',headers={'Authorization':authorization},timeout=30,follow_redirects=False)
    def _request(self, method, path, **kwargs):
        response=self.client.request(method,path,**kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f'Queue API rejected the request (HTTP {response.status_code})')
        return response.json()
    def runtime(self): return self._request('GET','documents/table-processing/runtime')
    def document(self,doc_id): return self._request('GET','documents/'+doc_id)
    def submit(self,doc_ids): return self._request('POST','documents/bulk-regenerate',json={'doc_ids':doc_ids})
    def job(self,job_id): return self._request('GET','jobs/'+str(job_id))
    def close(self): self.client.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(); modes.add_argument('--dry-run',action='store_true'); modes.add_argument('--apply',action='store_true')
    parser.add_argument('--doc-ids-file',type=Path); parser.add_argument('--target-version',default=PARSER_VERSION)
    parser.add_argument('--limit',type=int,default=5); parser.add_argument('--resume-run')
    parser.add_argument('--run-dir',type=Path,default=Path('data/table-migrations'))
    parser.add_argument('--api-base'); parser.add_argument('--authorization-env',default='TABLE_MIGRATION_AUTHORIZATION')
    parser.add_argument('--job-id',type=int,help='Explicit reconciliation after an unknown POST outcome')
    args=parser.parse_args(argv)
    if args.resume_run:
        if not re.fullmatch('[0-9a-f]{32}',args.resume_run): parser.error('Invalid run ID')
        path=args.run_dir/(args.resume_run+'.json'); manifest=json.loads(path.read_text(encoding='utf-8'))
        if manifest['target_version'] != args.target_version: parser.error('Target version differs from saved run')
    else:
        ids=args.doc_ids_file.read_text(encoding='utf-8-sig').split() if args.doc_ids_file else None
        with session_scope() as session: manifest=plan_run(session,target_version=args.target_version,doc_ids=ids,limit=args.limit)
        args.run_dir.mkdir(parents=True,exist_ok=True); path=args.run_dir/(manifest['run_id']+'.json')
    save=lambda data:write_json_atomic(path,data)
    if args.apply:
        auth=os.environ.get(args.authorization_env)
        if not args.api_base or not auth: parser.error('--apply requires --api-base and an authenticated authorization environment variable')
        api=QueueAPI(args.api_base,auth)
        try:
            if args.job_id is not None:
                job=api.job(args.job_id)
                expected=[d['doc_id'] for d in manifest['documents']]
                if job.get('job_type')!='bulk_regenerate' or (job.get('params') or {}).get('doc_ids')!=expected:
                    raise RuntimeError('Reconciliation job differs from this run')
                manifest['job_id']=args.job_id; manifest['submission']='sent';save(manifest)
            job=apply_batch(manifest,api,save);reconcile(manifest,job)
        finally:api.close()
    save(manifest)
    print(json.dumps({'run_id':manifest['run_id'],'mode':'apply' if args.apply else 'dry-run','documents':len(manifest['documents']),
                      'states':[d['status'] for d in manifest['documents']],'job_id':manifest['job_id']},ensure_ascii=False))
    return int(any(d['status']=='failed' for d in manifest['documents']))


if __name__=='__main__':
    raise SystemExit(main())
