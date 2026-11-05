"""Fixed native bridge for the owned basic managed-S3 dependency rehearsal.

No fixture creation, provider reconciliation, lease edits or release authority.
The caller supplies a verified same-schema upgrade and independent recovery copy.
"""
from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from replica_provider_outage import PolicyActions, ProviderRun, STAGES
from replica_runtime_run import OwnedAdapter, digest, read_json
from test_scripts.replica_frontend_http import PrivateFiles
from test_scripts.replica_provider_faults import (
    RECIPE, assert_network, assert_outage, assert_recovered, fault_policy,
    hexadecimal, publication, require, uid, work_row,
)
from test_scripts.replica_runtime_acceptance import role_pods, verify_runtime

KINDS='deployments,statefulsets,daemonsets,cronjobs,jobs,hpa,replicationcontrollers,pods,services,secrets,configmaps,persistentvolumeclaims,networkpolicies'


def inventory_binding(objects):
    """Hash confidential inputs, pin processes; readiness may fail during denial."""
    result={}
    for obj in objects:
        kind=obj['kind'];meta=obj['metadata'];name=kind+'/'+meta['name']
        require(uid(meta.get('uid')) and name not in result)
        if kind=='Job':
            require(not obj.get('status',{}).get('active') and
                (obj.get('status',{}).get('succeeded') or any(c.get('type')=='Failed' and c.get('status')=='True'
                    for c in obj.get('status',{}).get('conditions',[]))))
            continue
        if kind=='Pod':
            status=obj.get('status',{})
            if status.get('phase') in {'Succeeded','Failed'} and any(r.get('kind')=='Job' for r in meta.get('ownerReferences',[])):
                continue
            role=meta.get('labels',{}).get('okf-role') or meta.get('labels',{}).get('okf-storage-component')
            require(role in {'frontend','api','worker','model-stub','postgres','qdrant','artifacts'}
                and not meta.get('deletionTimestamp') and status.get('phase')=='Running')
            states=status.get('containerStatuses',[])
            require(len(states)==1 and len(obj['spec']['containers'])==1
                and states[0].get('restartCount')==0 and hexadecimal(states[0].get('containerID','').split('://')[-1],64)
                and re.search(r'sha256:[0-9a-f]{64}$',states[0].get('imageID','')) is not None)
            selected=dict(spec=obj['spec'],pod_ip=status.get('podIP'),containers=[{k:s.get(k) for k in
                ('name','containerID','imageID','restartCount')} for s in states])
        else:
            require(not meta.get('deletionTimestamp'))
            if kind in {'DaemonSet','HorizontalPodAutoscaler','ReplicationController'}:raise ValueError('Unknown namespaced writer')
            if kind=='CronJob':require(obj.get('spec',{}).get('suspend') is True)
            selected={k:v for k,v in obj.items() if k not in {'apiVersion','kind','metadata','status'}}
        result[name]=dict(uid=meta['uid'],labels=meta.get('labels',{}),content_sha256=digest(selected))
    return result


def pg_proof(binding,authority,child,report):
    require(child.get('phase')=='verified' and report.get('status')=='owned-postgres-partition-stale-return-passed'
        and report.get('policy_removed') is True and report.get('normal_entrypoint_restored') is True
        and report.get('capacity',{}).get('owned_slots_at_finish')==0
        and all(report.get(k)==binding[k] for k in ('namespace','namespace_uid','installation_id','runtime_fingerprint'))
        and child.get('namespace_uid')==binding['namespace_uid'] and child.get('fingerprint')==binding['runtime_fingerprint']
        and hexadecimal(child.get('operation_id'),32) and report.get('fault_operation_id')==child['operation_id'])
    require(all(child.get('fault_authority',{}).get(k)==authority[k] for k in
        ('source_journal_sha256','backup_id','backup_authentication','backup_archive')))
    return dict(operation_id=child['operation_id'],journal_sha256=digest(child),report_sha256=digest(report))


def platform_binding(kube,objects):
    names={o['spec']['nodeName'] for o in objects if o['kind']=='Pod' and o.get('status',{}).get('phase')=='Running'}
    require(len(names)==1)
    nodes={}
    for name in sorted(names):
        obj=json.loads(kube.run('get','node',name,'-o','json'));meta=obj['metadata']
        require(meta['name']==name and uid(meta.get('uid')) and not meta.get('deletionTimestamp')
            and any(c.get('type')=='Ready' and c.get('status')=='True' for c in obj.get('status',{}).get('conditions',[])))
        nodes[name]=dict(uid=meta['uid'],spec_sha256=digest(obj['spec']))
    claims=[o for o in objects if o['kind']=='PersistentVolumeClaim'];require(len(claims)==3)
    volumes={}
    for claim in claims:
        name=claim.get('spec',{}).get('volumeName');require(isinstance(name,str) and name and claim.get('status',{}).get('phase')=='Bound')
        obj=json.loads(kube.run('get','pv',name,'-o','json'));meta=obj['metadata'];ref=obj['spec'].get('claimRef',{})
        require(meta['name']==name and uid(meta.get('uid')) and not meta.get('deletionTimestamp') and name not in volumes
            and all(ref.get(k)==claim['metadata'][k] for k in ('uid','name','namespace')))
        volumes[name]=dict(uid=meta['uid'],spec_sha256=digest(obj['spec']))
    return dict(nodes=nodes,volumes=volumes)


def pod_http(request,directory):
    """Self-contained Pod RPC. Only its remote caller chooses the fixed directory.

    Exclusive durable intents precede every changing POST. Observers never create
    a directory, authenticate, regenerate or log out. No cookie/body is returned.
    """
    import hashlib
    from http.cookiejar import CookieJar
    from io import BytesIO
    import json
    import os
    from pathlib import Path
    import re
    import stat
    import urllib.error
    import urllib.request
    from zipfile import ZipFile
    from app.config import get_settings
    from app.deployment.runtime_identity import enforce_runtime_identity

    def need(value):
        if not value:raise ValueError('Owned native HTTP admission refused')
    need(type(request.get('observe')) is bool and request.get('action') in
        {'login','qdrant.submit','model.submit','check','logout'})
    need(re.fullmatch('[0-9a-f]{32}',request.get('execution_id','')) is not None
        and re.fullmatch('[0-9a-f]{16}',request.get('doc_id','')) is not None)
    s=get_settings();identity=enforce_runtime_identity(s)
    need(s.model_mode=='stub' and s.auth_provider=='simulation' and s.embedding_provider=='fake'
        and s.deployment_mode=='distributed' and s.artifact_backend=='s3'
        and s.llm_base_url=='http://model-stub:8000/v1' and s.deployment_namespace==request['namespace']
        and identity.fingerprint==request['runtime_fingerprint'] and identity.installation_id==request['installation_id'])
    directory=Path(directory);observe=request['observe'];action=request['action']
    binding={k:request[k] for k in ('execution_id','doc_id','namespace','runtime_fingerprint','installation_id')}
    def safe(path,directory=False):
        st=path.lstat()
        need(not path.is_symlink() and (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode) and st.st_nlink==1)
            and not getattr(st,'st_file_attributes',0)&0x400)
        if os.name!='nt':need(st.st_uid==os.getuid() and not st.st_mode&0o077)
        return st
    # Refuse ancestor redirects as well as aliased receipt files.
    for parent in directory.parents:
        need(not parent.is_symlink() and not getattr(parent.lstat(),'st_file_attributes',0)&0x400)
    if not directory.exists():
        if observe:return None
        directory.mkdir(mode=0o700)
    safe(directory,True)
    def exists(name):
        p=directory/name
        try:safe(p)
        except FileNotFoundError:return False
        return True
    def read(name):
        p=directory/name;safe(p)
        fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        with os.fdopen(fd,'rb') as f:
            before=os.fstat(f.fileno());current=safe(p)
            need((before.st_dev,before.st_ino)==(current.st_dev,current.st_ino))
            raw=f.read(1024*1024+1);need(len(raw)<=1024*1024)
        return json.loads(raw)
    def once(name,value):
        p=directory/name
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(value,f,sort_keys=True,allow_nan=False);f.flush();os.fsync(f.fileno())
        if os.name!='nt':
            fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
    if exists('binding.json'):need(read('binding.json')==binding)
    elif observe:return None
    else:once('binding.json',binding)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):raise ValueError('Native redirect refused')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPCookieProcessor(CookieJar()))
    cookie=read('session.json')['cookie'] if exists('session.json') else None
    def http(path,data=None):
        headers={'Content-Type':'application/json'}
        if cookie:headers['Cookie']=cookie
        req=urllib.request.Request('http://127.0.0.1:8000'+path,
            data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            with opener.open(req,timeout=30) as response:
                raw=response.read(8*1024*1024+1);need(len(raw)<=8*1024*1024)
                return response.status,raw,response.headers
        except urllib.error.HTTPError as exc:
            # Never return provider/app error bodies or response headers.
            return exc.code,b'',{}
    if action=='check':
        need(cookie is not None)
        expected=request['checks'];need(set(expected)=={'original_sha256','query','export_job','export_part','export_sha256'})
        need(re.fullmatch('[0-9a-f]{32}',expected['export_job']) is not None
            and type(expected['export_part']) is int and expected['export_part']>0
            and isinstance(expected['query'],str) and 1<=len(expected['query'])<=1000)
        for key in ('original_sha256','export_sha256'):need(re.fullmatch('[0-9a-f]{64}',expected[key]) is not None)
        status,body,_=http('/api/documents/'+request['doc_id']+'/download');need(status==200)
        need(hashlib.sha256(body).hexdigest()==expected['original_sha256'])
        status,raw,_=http('/api/search',{'query':expected['query'],'mode':'bm25','top_k':40});need(status==200)
        need(request['doc_id'] in {h['doc_id'] for h in json.loads(raw)['hits']})
        status,raw,_=http('/api/jobs/'+expected['export_job']+'/export/'+str(expected['export_part']));need(status==200)
        need(hashlib.sha256(raw).hexdigest()==expected['export_sha256'])
        with ZipFile(BytesIO(raw)) as archive:
            docs=[v for v in archive.infolist() if v.filename.endswith('.docx')]
            need(len(docs)==1 and docs[0].file_size<=8*1024*1024
                and hashlib.sha256(archive.read(docs[0])).hexdigest()==expected['original_sha256'])
        return {'passed':True,'download_search_export_verified':True,'original_sha256':expected['original_sha256'],
            'export_sha256':expected['export_sha256']}
    result_name=action+'.result.json';intent_name=action+'.intent.json'
    if exists(result_name):return read(result_name)
    if exists(intent_name):
        need(read(intent_name)=={'binding':binding,'action':action})
        if action=='login' and cookie:
            status,body,_=http('/api/auth/me')
            if status==200 and json.loads(body)['user']['username']=='replica.admin':
                return {'passed':True,'authenticated':True}
        if action=='logout' and cookie:
            status,_,_=http('/api/auth/me')
            if status==401:return {'passed':True,'session_revoked':True}
        return None
    if observe:return None
    if action!='login':need(cookie is not None)
    once(intent_name,{'binding':binding,'action':action})
    if action=='login':
        status,_,headers=http('/api/auth/simulate',{'username':'replica.admin'});need(status==200)
        values=headers.get_all('Set-Cookie',[]) if hasattr(headers,'get_all') else []
        cookie='; '.join(v.split(';')[0] for v in values);need(cookie and len(cookie)<=16384)
        once('session.json',{'cookie':cookie})
        status,body,_=http('/api/auth/me');need(status==200 and json.loads(body)['user']['username']=='replica.admin')
        result={'passed':True,'authenticated':True}
    elif action.endswith('.submit'):
        status,body,_=http('/api/documents/'+request['doc_id']+'/regenerate',{});need(status==200)
        need(json.loads(body)['id']==request['doc_id']);result={'passed':True,'document_id':request['doc_id']}
    else:
        status,_,_=http('/api/auth/logout',{});need(status==200)
        status,_,_=http('/api/auth/me');need(status==401);result={'passed':True,'session_revoked':True}
    once(result_name,result)
    return result


SQL_SOURCE='''import json,hashlib
from sqlalchemy import text
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
from app.db.session import get_engine
s=get_settings();identity=enforce_runtime_identity(s)
assert s.model_mode=='stub' and s.auth_provider=='simulation' and s.embedding_provider=='fake'
assert s.deployment_mode=='distributed' and s.artifact_backend=='s3'
assert identity.fingerprint==EXPECTED['runtime_fingerprint'] and identity.installation_id==EXPECTED['installation_id']
with get_engine().connect() as session:
 session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
 corpus=[]
 for r in session.execute(text("SELECT d.id,d.status,d.problem,d.deleted_at,o.sha256,g.active_generation_id FROM documents d JOIN artifact_bindings b ON b.owner_type='original' AND b.owner_id=d.id AND b.logical_name='original' JOIN artifact_objects o ON o.object_id=b.object_id LEFT JOIN document_generation_states g ON g.doc_id=d.id ORDER BY d.id")).mappings():
  concepts=[dict(c) for c in session.execute(text('SELECT slug,title,content FROM okf_concepts WHERE doc_id=:id ORDER BY slug'),{'id':r['id']}).mappings()]
  corpus.append({**dict(r),'deleted_at':str(r['deleted_at']) if r['deleted_at'] else None,'concept_sha256':hashlib.sha256(json.dumps(concepts,sort_keys=True).encode()).hexdigest()})
 selected=[r for r in corpus if r['id']==EXPECTED['doc_id']];assert len(selected)==1
 d=selected[0];assert d['deleted_at'] is None
 pub=dict(doc_id=d['id'],active_generation_id=d['active_generation_id'],concept_sha256=d['concept_sha256'],original_sha256=d['sha256'],active_count=session.scalar(text("SELECT count(*) FROM document_generations WHERE doc_id=:id AND phase='active'"),{'id':d['id']}))
 works=[dict(r) for r in session.execute(text("SELECT id,kind,doc_id,status,attempts,fence,error_code,cancel_requested,owner_id,lease_until FROM work_items WHERE kind='document' AND doc_id=:id ORDER BY created_at,id"),{'id':d['id']}).mappings()]
 maintenance=dict(session.execute(text('SELECT enabled,epoch FROM replica_maintenance_state WHERE id=1')).mappings().one())
 result=dict(publication=pub,works=works,corpus=corpus,maintenance=maintenance,
  model_owners=session.scalar(text("SELECT count(*) FROM replica_capacity_slots WHERE resource='llm' AND owner_id IS NOT NULL")),
  running=session.scalar(text("SELECT count(*) FROM work_items WHERE status='running'")),
  chats=session.scalar(text("SELECT count(*) FROM chat_runs WHERE status='running'")))
 print(json.dumps(result,default=lambda value:value.isoformat()))
'''


class NativeAdapter:
    def __init__(self,manifest,journal):
        self.owned=OwnedAdapter(manifest,journal);self.private=self.owned.private;self.manifest=manifest
        self.files=PrivateFiles(self.private);extra=manifest.get('provider_fault')
        require(isinstance(extra,dict) and set(extra)=={'recipe','doc_id','pg_journal','pg_report','checks'}
            and extra['recipe']==RECIPE and hexadecimal(extra['doc_id'],16))
        checks=extra['checks']
        require(isinstance(checks,dict) and set(checks)=={'original_sha256','query','export_job','export_part','export_sha256'}
            and hexadecimal(checks['original_sha256'],64) and hexadecimal(checks['export_sha256'],64)
            and hexadecimal(checks['export_job'],32) and type(checks['export_part']) is int and checks['export_part']>0
            and isinstance(checks['query'],str) and 1<=len(checks['query'])<=1000)
        for key in ('pg_journal','pg_report'):
            path=Path(extra[key]).absolute();require(path.parent==self.private and path not in self.owned.paths.values()
                and path!=Path(journal).absolute())
        self.extra=extra
        self.value={**self.owned.value,'recipe':RECIPE,'profile':'basic','deployment_mode':'distributed','artifact_backend':'s3'}

    def objects(self):return json.loads(self.kube.run('get',KINDS,'-o','json'))['items']

    def bind(self,state):
        binding=self.owned.bind(state);self.kube=self.owned.kube;self.authority=self.owned.authority
        self.operation=state['execution_id'];fault_policy(self.value,'model',self.operation)
        child=read_json(self.extra['pg_journal']);report=read_json(self.extra['pg_report'])
        pg=pg_proof(self.manifest,self.authority.record,child,report)
        objects=self.objects();self.snapshot=inventory_binding(objects);self.platform=platform_binding(self.kube,objects)
        self.expected_pods={p['metadata']['uid']:role for role in ('api','worker') for p in role_pods(objects,role)}
        self.pg=pg
        from app.services.diagnostics.paths import assert_no_links
        directory=self.private/('provider-'+self.operation)
        assert_no_links(directory,self.private)
        if not directory.exists():directory.mkdir(mode=0o700)
        self.files=PrivateFiles(directory)
        self.policy=PolicyActions(self.kube,self.value,directory,self.operation,lambda:self.guard())
        # This snapshot is recomputed on resume, not trusted merely because a file exists.
        return {**binding,'provider_recipe':RECIPE,'inventory':self.snapshot,'platform':self.platform,'postgres':pg}

    def guard(self):
        self.kube.scope()
        ns=json.loads(self.kube.run('get','namespace',self.value['namespace'],'-o','json'))
        require(ns['metadata']['uid']==self.value['namespace_uid'])
        self.authority.check(inject=True)
        objects=self.objects()
        require(inventory_binding(objects)==self.snapshot and platform_binding(self.kube,objects)==self.platform)
        policies=json.loads(self.kube.run('get','ciliumnetworkpolicies','-o','json'))['items']
        seen=set()
        for obj in policies:
            provider=obj.get('metadata',{}).get('labels',{}).get('okf-provider-fault')
            require(provider in {'qdrant','model'} and provider not in seen);seen.add(provider)
            expected=self.policy.expected(provider)
            require(self.files.read(provider+'.policy.intent.json')==expected)
            receipt=self.policy.admit(provider,obj)
            if self.files.path(provider+'.policy.created.json').exists():require(self.files.read(provider+'.policy.created.json')==receipt)
        require(len(seen)<=1)
        objects=self.kube.inventory()
        require(verify_runtime(self.kube,objects,self.value)['fingerprint']==self.manifest['runtime_fingerprint'])
        return True

    def before(self,stage,state):
        self.guard()
        if 'baseline' in state['completed']:
            base=state['completed']['baseline']['outcome']['database'];current=self.database()
            require(current['maintenance']==base['maintenance'])
            old={d['id']:d for d in base['corpus']};new={d['id']:d for d in current['corpus']}
            require(len(old)==len(base['corpus']) and len(new)==len(current['corpus']) and set(old)==set(new))
            require(all(new[k]==v for k,v in old.items() if k!=self.extra['doc_id']))

    def api(self):return sorted(role_pods(self.kube.inventory(),'api'),key=lambda p:p['metadata']['name'])[0]['metadata']['name']

    def database(self):
        expected={k:self.manifest[k] for k in ('installation_id','runtime_fingerprint')};expected['doc_id']=self.extra['doc_id']
        result=self.kube.python(self.api(),'EXPECTED='+repr(expected)+'\n'+SQL_SOURCE)
        publication(result['publication']);require(result['maintenance']['enabled'] is False)
        require(1<=len(result['works'])<=17)
        for row in result['works']:work_row(row,self.extra['doc_id'])
        return result

    def model(self):
        pods=role_pods(self.kube.inventory(),'model-stub');require(len(pods)==1)
        result=self.kube.python(pods[0]['metadata']['name'],'''import json,urllib.request
with urllib.request.urlopen('http://127.0.0.1:8000/test/status',timeout=3) as response: result=json.load(response)
print(json.dumps(result))''')
        require(set(result)=={'test_only','active','peak_active','started','finished'} and result['test_only'] is True
            and all(type(result[k]) is int and result[k]>=0 for k in ('active','peak_active','started','finished'))
            and result['started']-result['finished']==result['active'] and result['peak_active']<=1)
        return result

    def network(self):
        rows=[]
        hosts={'postgres':[(self.value['storage_release']+'-postgres',5432)],
            'qdrant':[(self.value['storage_release']+'-qdrant',6333),(self.value['storage_release']+'-qdrant',6334)],
            'artifacts':[(self.value['storage_release']+'-artifacts',9000)],'model':[('model-stub',8000)]}
        for role in ('api','worker'):
            pods=role_pods(self.kube.inventory(),role);require(len(pods)==2)
            for pod in pods:
                source='''import socket,json,urllib.request
result={}
for name,targets in HOSTS.items():
 states=[]
 for host,port in targets:
  try:
   with socket.create_connection((host,port),timeout=1):pass
   states.append(True)
  except OSError:states.append(False)
 assert len(set(states))==1
 result[name]=states[0]
with urllib.request.urlopen('http://127.0.0.1:PORT/health/live',timeout=3) as response:live=response.status
print(json.dumps({'connectivity':result,'live':live}))
'''.replace('HOSTS',repr(hosts)).replace('PORT','8000' if role=='api' else '8001')
                result=self.kube.python(pod['metadata']['name'],source)
                statuses=pod['status']['containerStatuses']
                rows.append(dict(pod_uid=pod['metadata']['uid'],role=role,restart_count=statuses[0]['restartCount'],**result))
        return rows

    def rpc(self,action,state,*,observe):
        self.guard()
        request=dict(execution_id=self.operation,doc_id=self.extra['doc_id'],action=action,observe=observe,
            **{k:self.manifest[k] for k in ('namespace','installation_id','runtime_fingerprint')})
        if action=='check':request['checks']=self.extra['checks']
        root='/tmp/okf-provider-'+self.operation
        source=inspect.getsource(pod_http)+'\nprint(__import__("json").dumps(pod_http('+repr(request)+','+repr(root)+')))'
        return self.kube.python(self.api(),source)

    def positive(self):
        rows=self.network();require(len(rows)==4 and all(r['live']==200 and r['restart_count']==0
            and set(r['connectivity'])=={'postgres','qdrant','model','artifacts'}
            and all(v is True for v in r['connectivity'].values()) for r in rows))
        require({r['pod_uid']:r['role'] for r in rows}==self.expected_pods)
        return rows

    def case_base(self,provider,state):
        prior='baseline' if provider=='qdrant' else 'qdrant.verify'
        return state['completed'][prior]['outcome']['database']

    def execute(self,stage,state):return self.dispatch(stage,state,observe=False)
    def observe(self,stage,state):return self.dispatch(stage,state,observe=True)

    def dispatch(self,stage,state,*,observe):
        if stage=='preflight':
            baseline=self.owned.baseline();rows=self.positive();provider=self.model();require(provider['active']==0)
            return dict(passed=True,baseline=baseline,network=rows,provider=provider,postgres=self.pg)
        if stage=='baseline':
            login=self.rpc('login',state,observe=observe)
            if login is None:return None
            database=self.database();require(not any(database[k] for k in ('model_owners','running','chats')))
            require(all(w['status']=='succeeded' for w in database['works']))
            require(database['publication']['original_sha256']==self.extra['checks']['original_sha256'])
            functional=self.rpc('check',state,observe=True);require(functional and functional.get('passed') is True)
            return dict(passed=True,database=database,provider=self.model(),functional=functional)
        if stage=='final':
            self.positive();provider=self.model();database=self.database()
            require(provider['active']==0 and not any(database[k] for k in ('model_owners','running','chats')))
            require(not json.loads(self.kube.run('get','ciliumnetworkpolicies','-o','json'))['items'])
            require(all(state['completed'][p+'.verify']['outcome']['passed'] is True for p in ('qdrant','model')))
            verified=state['completed']['model.verify']['outcome']['database']
            require(all(database[k]==verified[k] for k in ('publication','works','corpus','maintenance')))
            logout=self.rpc('logout',state,observe=observe)
            if logout is None:return None
            return dict(passed=True,provider=provider,postgres=self.pg,session_revoked=True,policies_absent=True)
        provider,action=stage.split('.');base=self.case_base(provider,state)
        if action=='inject':
            if not observe:
                self.positive();idle=self.database();require(not any(idle[k] for k in ('model_owners','running','chats')))
                require(self.model()['active']==0)
            result=self.policy.observe_create(provider) if observe else self.policy.create(provider)
            if result is None:return None
            rows=self.network();controls=assert_network(rows,self.expected_pods,provider)
            return dict(passed=True,policy=result['evidence'],network=controls,provider=self.model())
        if action=='submit':
            current=self.database();old={w['id'] for w in base['works']};added=[w for w in current['works'] if w['id'] not in old]
            if observe:
                receipt=self.rpc(stage,state,observe=True)
                if receipt is None and not added:return None
            else:
                require(not added and current['publication']==base['publication'])
                rows=self.network();assert_network(rows,self.expected_pods,provider)
                receipt=self.rpc(stage,state,observe=False)
                if receipt is None:return None
                current=self.database();added=[w for w in current['works'] if w['id'] not in old]
            require(len(added)==1 and set(w['id'] for w in current['works'])==old|{added[0]['id']})
            return dict(passed=True,work_id=added[0]['id'])
        if action=='observe':
            current=self.database();rows=self.network();assert_network(rows,self.expected_pods,provider)
            queued=assert_outage(base['publication'],current['publication'],[w['id'] for w in base['works']],current['works'],provider)
            require(queued['id']==state['completed'][provider+'.submit']['outcome']['work_id'])
            model=self.model();before=state['completed'][provider+'.inject']['outcome']['provider']
            if provider=='model':require(model==before and model['active']==0)
            require(not any(current[k] for k in ('model_owners','running','chats')))
            return dict(passed=True,queued=queued,provider=model,database=current)
        if action=='heal':
            result=self.policy.observe_heal(provider) if observe else self.policy.heal(provider)
            if result is None:return None
            rows=self.positive();return dict(passed=True,policy=result['evidence'],network=rows)
        require(action=='verify')
        current=self.database();queued=state['completed'][provider+'.observe']['outcome']['queued']
        recovered=assert_recovered(base['publication'],current['publication'],[w['id'] for w in base['works']],
            current['works'],queued,base['publication']['original_sha256'])
        require(not any(current[k] for k in ('model_owners','running','chats')) and self.model()['active']==0)
        require(next(d for d in current['corpus'] if d['id']==self.extra['doc_id'])['status']=='done'
            and next(d for d in current['corpus'] if d['id']==self.extra['doc_id'])['problem'] is None)
        functional=self.rpc('check',state,observe=True);require(functional and functional.get('passed') is True)
        return dict(passed=True,recovered=recovered,database=current,functional=functional)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True);parser.add_argument('--journal',required=True)
    parser.add_argument('--stage',choices=STAGES,required=True)
    flags=parser.add_mutually_exclusive_group();flags.add_argument('--apply',action='store_true');flags.add_argument('--observe',action='store_true')
    args=parser.parse_args()
    try:
        manifest=read_json(args.manifest);adapter=NativeAdapter(manifest,args.journal)
        run=ProviderRun(args.journal,adapter);print(json.dumps(run.execute(args.stage,apply=args.apply,observe=args.observe)))
    except Exception as exc:
        print(json.dumps({'provider_native_refused':type(exc).__name__,'qualification_complete':False,'certification':None}))
        return 1
    return 0


if __name__=='__main__':sys.exit(main())
