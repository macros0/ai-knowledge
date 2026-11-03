"""Read-only legacy diagnostic inventory and S4 atomic journal import.

Payloads stay in verified transfer blobs, not the private JSON plan. Pending
bundle inputs use the existing bounded snapshot/ZIP codecs and stay queued.
"""
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select

from app.db import models
from app.db.base import Base
from app.services.diagnostics.paths import assert_no_links
from app.services.diagnostics.read_view import ReadViewLease, SegmentDescriptor
from app.services.diagnostics.sanitize import encode_event, valid_uuid
from app.services.diagnostics.schema import MAX_EVENT_BYTES
from app.services.diagnostics.store import DiagnosticStore
from .contract import BlobRef
from .publication import canonical_hash

TABLES=('diagnostic_sessions','diagnostic_replica_state','diagnostic_instances','diagnostic_segments','diagnostic_ingest_batches')
MAX_FILES=16384


@contextmanager
def writer_guard(root):
    """Hold the existing writer's OS lock without changing the retained journal."""
    root=Path(root)
    assert_no_links(root,Path(root.anchor))
    path=root/'.writer.lock'
    if not path.exists():
        yield
        return
    assert_no_links(path,root)
    before=path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1:
        raise ValueError('Invalid legacy diagnostic writer lock')
    flags=os.O_RDWR|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0)
    try:
        handle=os.fdopen(os.open(path,flags),'r+b',buffering=0)
    except OSError:
        raise ValueError('Legacy diagnostic writer lock unavailable') from None
    with handle:
        current=os.fstat(handle.fileno())
        if (current.st_dev,current.st_ino,current.st_nlink)!=(before.st_dev,before.st_ino,1):
            raise ValueError('Legacy diagnostic writer lock changed')
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Stop the legacy diagnostic writer before migration') from None
        try:
            assert_no_links(path,root)
            current=path.stat()
            if (current.st_dev,current.st_ino,current.st_nlink)!=(before.st_dev,before.st_ino,1):
                raise ValueError('Legacy diagnostic writer lock changed')
            yield
        finally:
            if os.name=='nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def diagnostic_revision(session):
    digest=sha256()
    for name in TABLES:
        table=Base.metadata.tables[name];digest.update(name.encode()+b'\0')
        for row in session.execute(select(table).order_by(*table.primary_key).execution_options(yield_per=100)).mappings():
            digest.update(canonical_hash(dict(row)).encode())
    return digest.hexdigest()


def _json(data):
    def pairs(values):
        result={}
        for key,value in values:
            if key in result:raise ValueError('Duplicate diagnostic input key')
            result[key]=value
        return result
    return json.loads(data,object_pairs_hook=pairs,parse_constant=lambda _value:(_ for _ in ()).throw(ValueError('Invalid diagnostic number')))


def _read(path,root,limit):
    assert_no_links(path,root)
    before=path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>limit:
        raise ValueError('Bounded regular single-link diagnostic input required')
    flags=os.O_RDONLY|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0)
    with os.fdopen(os.open(path,flags),'rb') as handle:
        opened=os.fstat(handle.fileno())
        if (opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns)!=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns):
            raise ValueError('Diagnostic input changed while opening')
        data=handle.read(limit+1)
        after=os.fstat(handle.fileno())
    assert_no_links(path,root);current=path.stat()
    if len(data)!=before.st_size or any((value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_nlink)!=(
            before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,1) for value in (after,current)):
        raise ValueError('Diagnostic input changed while reading')
    return data,dict(size=len(data),sha256=sha256(data).hexdigest(),mtime_ns=before.st_mtime_ns)


def inventory(backend,frontend,limits):
    result=[];used={'backend':0,'frontend':0}
    for role,root in (('backend',backend),('frontend',frontend)):
        if root is None:continue
        root=Path(root).absolute();assert_no_links(root,Path(root.anchor))
        if not root.is_dir():raise ValueError('Explicit legacy diagnostic root unavailable')
        allowed={'events','control','capture-expiry','snapshots','bundles','inputs','.writer.lock'} if role=='backend' else {
            'events','status.json','sessions.json'}
        for child in root.iterdir():
            assert_no_links(child,root)
            if child.name not in allowed:raise ValueError('Unknown or live diagnostic source protocol')
        selected=[]
        events=root/'events'
        if events.exists():
            for stream in sorted(events.iterdir()):
                assert_no_links(stream,root)
                if not stream.is_dir() or stream.name!='baseline' and valid_uuid(stream.name)!=stream.name:
                    raise ValueError('Invalid diagnostic stream directory')
                pattern=r'(?:\d{20}-[0-9a-f]{32}|remote-[0-9a-f-]{36})\.jsonl' if role=='backend' else r'\d{13}_[0-9a-f-]{36}\.jsonl'
                for path in sorted(stream.iterdir()):
                    if not re.fullmatch(pattern,path.name):raise ValueError('Unknown diagnostic event file')
                    selected.append((path,limits.segment_bytes))
        for name in ('control','capture-expiry') if role=='backend' else ():
            directory=root/name
            if not directory.exists():continue
            for path in sorted(directory.iterdir()):
                if (name=='control' and path.name not in {'deferred.jsonl','capture.json'}
                        or name=='capture-expiry' and (path.suffix!='.json' or valid_uuid(path.stem)!=path.stem)):
                    raise ValueError('Unknown diagnostic control file')
                selected.append((path,limits.control_bytes))
        if role=='frontend':
            selected.extend((root/name,limits.control_bytes) for name in ('status.json','sessions.json') if (root/name).exists())
        for path,limit in selected:
            data,metadata=_read(path,root,limit)
            used[role]+=len(data)
            if used[role]>(limits.backend_bytes if role=='backend' else limits.frontend_bytes) or len(result)>=MAX_FILES:
                raise ValueError('Legacy diagnostic input exceeds its bounded budget')
            if path.suffix=='.json':_json(data)
            result.append(dict(role=role,path=path.relative_to(root).as_posix(),**metadata))
    return dict(backend_root=str(Path(backend).absolute()),frontend_root=str(Path(frontend).absolute()) if frontend else None,
        files=sorted(result,key=lambda item:(item['role'],item['path'])))


def _data(meta,item,limits):
    root=Path(meta[item['role']+'_root'])
    data,actual=_read(root/item['path'],root,limits.segment_bytes if item['path'].startswith('events/') else limits.control_bytes)
    if any(actual[key]!=item[key] for key in actual):raise ValueError('Legacy diagnostic inventory changed')
    return data


def assert_inventory(meta,limits):
    if meta.get('mode')=='shared-sql':return
    actual=inventory(meta['backend_root'],meta['frontend_root'],limits)
    if actual!={key:meta[key] for key in ('backend_root','frontend_root','files')}:
        raise ValueError('Legacy diagnostic files changed after planning')


def _expiry(meta,item,limits):
    stream=item['path'].split('/')[1]
    if item['role']=='backend' and stream!='baseline':
        marker=next((value for value in meta['files'] if value['role']=='backend' and value['path']==f'capture-expiry/{stream}.json'),None)
        if marker:
            value=_json(_data(meta,marker,limits))
            if set(value)!={'expires_at'} or type(value['expires_at']) not in {int,float} or not math.isfinite(value['expires_at']):
                raise ValueError('Invalid diagnostic expiry marker')
            return datetime.fromtimestamp(value['expires_at'],timezone.utc).isoformat()
    ttl=limits.baseline_seconds if stream=='baseline' else limits.capture_seconds+3600
    return datetime.fromtimestamp(item['mtime_ns']/1e9+ttl,timezone.utc).isoformat()


def _chunks(data,role,stream,limit):
    pending=bytearray();component=None;stamps=[]
    for line in data.splitlines(keepends=True):
        if not line.endswith(b'\n') or len(line)>MAX_EVENT_BYTES:raise ValueError('Truncated or oversized legacy diagnostic event')
        event=_json(line)
        canonical=encode_event(event)
        if ((role=='backend' and canonical!=line or role=='frontend' and _json(canonical)!=event)
                or event.get('diagnostic_session_id','baseline')!=stream):
            raise ValueError('Noncanonical or foreign legacy diagnostic event')
        group='frontend' if event['component']=='frontend' else 'backend'
        if role=='frontend' and group!='frontend':raise ValueError('Foreign frontend diagnostic event')
        stamp=datetime.fromisoformat(event['timestamp_utc']).astimezone(timezone.utc).isoformat()
        if pending and (component!=group or len(pending)+len(canonical)>limit):
            yield bytes(pending),component,stamps;pending.clear();stamps=[]
        if len(canonical)>limit:raise ValueError('Legacy diagnostic event exceeds target segment budget')
        component=group;pending.extend(canonical);stamps.append(stamp)
    if pending:yield bytes(pending),component,stamps


class _OfflineStore(DiagnosticStore):
    durable_segments=True  # A complete pinned copy, never current SQL input.
    def __init__(self,root,limits,meta):
        # The operator spool holds source copies, prepared streams and ZIPs.
        # Runtime source/input/ZIP limits are still checked independently.
        backend=limits.backend_bytes+limits.total_bytes+limits.bundle_bytes
        super().__init__(root,replace(limits,backend_bytes=backend,total_bytes=backend+limits.frontend_bytes))
        self.meta=meta
        self.legacy_inputs=self.safe_path(self.root/'inputs'/str(uuid4()))
        self.legacy_inputs.mkdir(parents=True)
        self.descriptors=[]
        for item in meta['files']:
            if not item['path'].startswith('events/'):continue
            stream=item['path'].split('/')[1]
            for ordinal,(data,component,_stamps) in enumerate(_chunks(_data(meta,item,limits),item['role'],stream,min(65536,limits.segment_bytes))):
                path=self.safe_path(self.legacy_inputs/'events'/stream/(item['role']+'-'+item['sha256']+f'-{ordinal}.jsonl'))
                path.parent.mkdir(parents=True,exist_ok=True)
                self.write_bytes(path,data);value=path.stat()
                self.descriptors.append(SegmentDescriptor(path,stream,(value.st_dev,value.st_ino),len(data),
                    datetime.fromisoformat(_expiry(meta,item,limits)).timestamp(),value.st_mtime_ns,sha256(data).hexdigest(),
                    source='frontend' if component=='frontend' else 'backend',root=self.legacy_inputs))

    def pin_read_view(self,filter,cutoff_at):
        return ReadViewLease(self,tuple(self.descriptors),filter,cutoff_at,input_root=self.legacy_inputs)

    def coverage(self,_requested_from=None):
        gaps=['recorder_status_unavailable']
        if not any(item.source=='frontend' for item in self.descriptors):gaps.append('frontend_unavailable')
        else:gaps.append('frontend_status_unavailable')
        return {'recorder_counters_unknown':1,'frontend_counters_unknown':1},tuple(gaps)


def _metadata(session,request):
    from app.services.diagnostics.policy import safe_policy_snapshot
    query=select(models.DiagnosticSession)
    if request.session_id:query=query.where(models.DiagnosticSession.id==str(request.session_id))
    else:query=query.where(models.DiagnosticSession.created_at<=request.to_utc,models.DiagnosticSession.expires_at>=request.from_utc)
    rows=session.scalars(query.order_by(models.DiagnosticSession.id).limit(200))
    policies=[]
    doc_id=request.doc_id
    for row in rows:
        if request.session_id and row.scope=='document':doc_id=row.doc_id
        policies.append(dict(session_id=row.id,created_at_utc=row.created_at.replace(tzinfo=timezone.utc).isoformat()
            if row.created_at.tzinfo is None else row.created_at.astimezone(timezone.utc).isoformat(),
            capture_level=row.capture_level,policy_version=row.policy_version,policy_snapshot=safe_policy_snapshot(row.policy_snapshot)))
    query=select(models.Document.id,models.Document.status)
    if doc_id:query=query.where(models.Document.id==doc_id)
    operations=[dict(kind='document',id=identity,status=status) for identity,status in session.execute(
        query.order_by(models.Document.created_at.desc(),models.Document.id.desc()).limit(50))]
    if not doc_id:
        operations.extend(dict(kind='job',id=str(identity),status=status) for identity,status in session.execute(
            select(models.Job.id,models.Job.status).order_by(models.Job.created_at.desc(),models.Job.id.desc()).limit(50)))
    return {'status':'unknown','dependencies':{}},operations,policies


def compile_diagnostics(compiler,session):
    with writer_guard(compiler.task.legacy_diagnostics_root):
        return _compile_diagnostics(compiler,session)


def _compile_diagnostics(compiler,session):
    limits=compiler.settings.diagnostics_limits();task=compiler.task
    shared=session.get(models.DiagnosticReplicaState,1)
    pending=list(session.scalars(select(models.DiagnosticBundle).where(
        models.DiagnosticBundle.artifact_version==1,models.DiagnosticBundle.status.in_(['queued','building'])).order_by(models.DiagnosticBundle.id).limit(129)))
    if len(pending)>128:raise ValueError('Too many pending legacy diagnostic bundles')
    if shared is not None:
        if pending or task.legacy_diagnostics_root_explicit or task.legacy_frontend_root:
            raise ValueError('Mixed local/shared diagnostic history requires explicit reconciliation')
        return dict(version=1,mode='shared-sql',sql_revision=diagnostic_revision(session),history=[])
    root=task.legacy_diagnostics_root
    if not root.exists() and task.legacy_frontend_root is None:
        if pending or session.scalar(select(models.DiagnosticSession.id).limit(1)):
            raise ValueError('Legacy diagnostic journal is missing')
        return None
    if session.scalar(select(models.DiagnosticSession.id).where(models.DiagnosticSession.status.in_(['starting','active'])).limit(1)):
        raise ValueError('Stop and reconcile legacy diagnostic captures before migration')
    if (session.scalar(select(models.DiagnosticSegment.id).limit(1)) or session.scalar(select(models.DiagnosticInstance.id).limit(1))
            or session.scalar(select(models.DiagnosticIngestBatch.instance_id).limit(1))):
        raise ValueError('Ambiguous shared diagnostic history requires reconciliation')
    meta=inventory(root,task.legacy_frontend_root,limits)
    meta.update(version=1,mode='legacy',sql_revision=diagnostic_revision(session),history=[])
    projection=next((item for item in meta['files'] if item['role']=='backend' and item['path']=='control/capture.json'),None)
    if projection:
        value=_json(_data(meta,projection,limits))
        if (not isinstance(value,dict) or set(value)!={'schema_version','boot_id','active','revision','lease_until'}
                or type(value['schema_version']) is not int or value['schema_version']!=2
                or value['active'] is not False or valid_uuid(value['boot_id'])!=value['boot_id']
                or type(value['revision']) is not int or value['revision']<0
                or type(value['lease_until']) not in {int,float} or not math.isfinite(value['lease_until'])):
            raise ValueError('Reconcile the legacy diagnostic control projection before migration')
    deferred=next((item for item in meta['files'] if item['role']=='backend' and item['path']=='control/deferred.jsonl'),None)
    if deferred:
        for line in _data(meta,deferred,limits).splitlines(keepends=True):
            if not line.endswith(b'\n') or len(line)>4096:raise ValueError('Invalid legacy control journal')
            event=_json(line);row=session.get(models.DiagnosticSession,event.get('target_id'))
            if (row is None or event.get('action')!='diagnostic_session_stopped'
                    or event.get('event_id') not in (row.audit_receipts or [])):
                raise ValueError('Reconcile the audited legacy control journal before migration')
    policy={key:getattr(limits,key) for key in ('baseline_bytes','session_bytes','frontend_bytes','backend_bytes','baseline_seconds','capture_seconds')}
    compiler.inserts.append(dict(table='diagnostic_replica_state',values=dict(id=1,policy_hash=sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest(),evicted_segments=0)))
    captured=datetime.now(timezone.utc).isoformat();instances={};count=0
    for item in meta['files']:
        kind='diagnostic_legacy_'+item['role'];path=Path(meta[item['role']+'_root'])/item['path']
        compiler.track(kind,path,expected=item['sha256'])
        if not item['path'].startswith('events/'):continue
        ref=compiler.file(kind,path,expected=item['sha256'],media='application/x-ndjson')
        stream=item['path'].split('/')[1];chunks=[]
        for ordinal,(data,component,stamps) in enumerate(_chunks(_data(meta,item,limits),item['role'],stream,min(65536,limits.segment_bytes))):
            count+=1
            if count>MAX_FILES:raise ValueError('Legacy journal exceeds the shared segment bound')
            identity=str(uuid5(UUID(compiler.id),f'diagnostic/{item["role"]}/{item["path"]}/{ordinal}'))
            role=item['role']+'/'+component
            instance=instances.setdefault(role,str(uuid5(UUID(compiler.id),'diagnostic-instance/'+role)))
            chunks.append(dict(id=identity,instance_id=instance,stream=stream,component=component,sha256=sha256(data).hexdigest(),
                size=len(data),event_count=len(stamps),first_at=min(stamps),last_at=max(stamps),
                created_at=datetime.fromtimestamp(item['mtime_ns']/1e9,timezone.utc).isoformat(),expires_at=_expiry(meta,item,limits)))
        meta['history'].append(dict(ref=asdict(ref),role=item['role'],stream=stream,chunks=chunks))
    for role,identity in instances.items():
        compiler.inserts.append(dict(table='diagnostic_instances',values=dict(id=identity,role='frontend' if role.endswith('/frontend') else 'combined',
            created_at=captured,heartbeat_at=captured,closed_at=captured,counters={})))
    if pending:
        from app.models.diagnostics import BundleRequest
        from app.services.diagnostics.bundle import build_bundle
        from app.services.diagnostics.snapshot import collect_prepared_snapshot
        scratch=task.path.parent/'diagnostic-inputs'/compiler.id
        with _OfflineStore(scratch,limits,meta) as store:
            for row in pending:
                if (row.status!='queued' or row.finished_at is not None or row.size_bytes or row.sha256 or row.manifest
                        or not row.request.get('session_id') and not all(row.request.get(key) for key in ('from_utc','to_utc'))):
                    raise ValueError('Uncertain legacy diagnostic bundle needs reconciliation')
                request=BundleRequest.model_validate(row.request)
                cutoff=row.cutoff_at.replace(tzinfo=timezone.utc) if row.cutoff_at.tzinfo is None else row.cutoff_at.astimezone(timezone.utc)
                snapshot=collect_prepared_snapshot(request,cutoff_at=cutoff,store=store,job_id=str(uuid5(UUID(compiler.id),row.id)),
                    frontend_root=task.legacy_frontend_root,metadata_provider=lambda value:_metadata(session,value))
                snapshot.partial=True
                snapshot.gaps=tuple(dict.fromkeys((*snapshot.gaps,'metadata_unavailable')))
                snapshot.counts['recorder_counters_unknown']=1
                try:built=build_bundle(snapshot,store.root/'bundles'/(row.id+'.zip'),limits,collection_mode='offline')
                finally:snapshot.release()
                ref=compiler._item(built.sha256,built.size_bytes,'application/zip',dict(kind='generated',path=built.path.relative_to(task.path.parent).as_posix()))
                receipt=dict(version=1,ref=asdict(ref),cutoff_at=cutoff.isoformat(),request_sha256=canonical_hash(row.request),manifest=built.manifest)
                compiler.bind('diagnostic_bundle',row.id,None,'frozen-input.zip',ref)
                compiler.update(models.DiagnosticBundle,{'id':row.id},artifact_version=2,manifest={'frozen_bundle':receipt})
    assert_inventory(meta,limits)
    if meta['sql_revision']!=diagnostic_revision(session):raise ValueError('Diagnostic SQL changed while planning')
    return meta


def apply_diagnostics(session,meta,store,limits):
    if meta is None or meta.get('mode')=='shared-sql':return
    if meta.get('version')!=1:raise ValueError('Unsupported diagnostic import')
    for item in meta['history']:
        ref=BlobRef(**item['ref'])
        if ref.size>limits.segment_bytes:raise ValueError('Oversized diagnostic target input')
        with store.open(ref) as handle:
            data=handle.read(ref.size+1)
        if len(data)!=ref.size or sha256(data).hexdigest()!=ref.sha256:raise ValueError('Diagnostic target input changed')
        actual=list(_chunks(data,item['role'],item['stream'],min(65536,limits.segment_bytes)))
        if len(actual)!=len(item['chunks']):raise ValueError('Diagnostic target chunk inventory changed')
        for (payload,component,stamps),expected in zip(actual,item['chunks']):
            if (len(payload),sha256(payload).hexdigest(),component,len(stamps),min(stamps),max(stamps))!=(
                    expected['size'],expected['sha256'],expected['component'],expected['event_count'],expected['first_at'],expected['last_at']):
                raise ValueError('Diagnostic target chunk changed')
            values={**expected,'payload':payload.decode()}
            for name in ('first_at','last_at','created_at','expires_at'):values[name]=datetime.fromisoformat(values[name])
            session.execute(models.DiagnosticSegment.__table__.insert().values(**values))
