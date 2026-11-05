"""Exercise native admission and real HTTP intents without a Kubernetes server."""
import copy
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import urllib.request
from uuid import UUID
from zipfile import ZipFile

import pytest


def export_fixture():
    original=b'provider-native-synthetic-original'
    output=BytesIO()
    with ZipFile(output,'w') as archive:
        archive.writestr('original.docx',original)
    raw=output.getvalue()
    return original,raw,dict(original_sha256=sha256(original).hexdigest(),query='synthetic marker',
        export_job=17,export_part=1,export_sha256=sha256(raw).hexdigest())


def tool():
    path=Path(__file__).resolve().parents[2]/'scripts/kubernetes/replica_provider_native.py'
    assert path.is_file(),'Native provider adapter missing'
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('provider_native_test',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def pod(role,index):
    return dict(kind='Pod',metadata=dict(name=role+str(index),uid=str(UUID(int=index)),
        labels={'okf-role':role,'app.kubernetes.io/instance':'app'}),
        spec=dict(nodeName='owned-node',containers=[dict(name=role,image='pinned')]),
        status=dict(phase='Running',podIP='10.1.0.'+str(index),containerStatuses=[dict(name=role,
            restartCount=0,ready=True,imageID='sha256:'+'a'*64,containerID='containerd://'+'b'*64)]))


def objects():
    return [pod(role,i) for i,role in enumerate(['api','api','worker','worker','model-stub'],1)]+[
        dict(kind='Secret',metadata=dict(name='runtime',uid=str(UUID(int=20)),labels={}),data={'secret':'private-canary'})]


def test_native_binding_ignores_readiness_but_detects_container_and_secret_changes():
    m=tool();before=objects();sealed=m.inventory_binding(before)
    changed=copy.deepcopy(before);changed[0]['status']['containerStatuses'][0]['ready']=False
    assert m.inventory_binding(changed)==sealed
    assert 'private-canary' not in json.dumps(sealed)
    changed[0]['status']['containerStatuses'][0]['containerID']='containerd://'+'c'*64
    assert m.inventory_binding(changed)!=sealed
    changed=copy.deepcopy(before);changed[-1]['data']['secret']='new-secret'
    assert m.inventory_binding(changed)!=sealed


@pytest.mark.parametrize('change',['restart','terminating','pending','missing-status','unknown-writer'])
def test_native_binding_refuses_uncertain_processes(change):
    m=tool();rows=objects()
    if change=='restart':rows[0]['status']['containerStatuses'][0]['restartCount']=1
    if change=='terminating':rows[0]['metadata']['deletionTimestamp']='now'
    if change=='pending':rows[0]['status']['phase']='Pending'
    if change=='missing-status':rows[0]['status']['containerStatuses']=[]
    if change=='unknown-writer':rows.append(pod('unrecognized-writer',8))
    with pytest.raises(ValueError):m.inventory_binding(rows)


def test_terminal_job_pods_do_not_invalidate_application_identity():
    m=tool();rows=objects();expected=m.inventory_binding(rows)
    job=pod('unknown',9);job['status']['phase']='Succeeded'
    job['metadata']['ownerReferences']=[{'kind':'Job','uid':str(UUID(int=90))}]
    assert m.inventory_binding(rows+[job])==expected


@pytest.mark.parametrize('kind',['Job','HorizontalPodAutoscaler','ReplicationController'])
def test_unknown_active_controllers_cannot_enter_the_fault_fixture(kind):
    m=tool();extra=dict(kind=kind,metadata=dict(name='foreign',uid=str(UUID(int=89))),spec={})
    with pytest.raises(ValueError):m.inventory_binding(objects()+[extra])


def test_job_ttl_cleanup_does_not_change_admitted_runtime_inputs():
    m=tool();extra=dict(kind='Job',metadata=dict(name='completed',uid=str(UUID(int=89))),spec={},status={'succeeded':1})
    assert m.inventory_binding(objects()+[extra])==m.inventory_binding(objects())


def pg_inputs():
    binding=dict(namespace='replica-test-faults01',namespace_uid=str(UUID(int=100)),
        installation_id='1'*32,runtime_fingerprint='2'*64)
    authority=dict(source_journal_sha256='3'*64,backup_id='4'*32,backup_authentication='5'*64,
        backup_archive='protected',validated_at='2026-11-05T00:00:00+00:00')
    child=dict(phase='verified',fault_authority=copy.deepcopy(authority),operation_id='6'*32,
        namespace_uid=binding['namespace_uid'],fingerprint=binding['runtime_fingerprint'])
    report=dict(status='owned-postgres-partition-stale-return-passed',**binding,
        fault_operation_id='6'*32,policy_removed=True,normal_entrypoint_restored=True,
        capacity={'owned_slots_at_finish':0},runtime={'images':{'api':'sha256:'+'7'*64}})
    return binding,authority,child,report


def test_pg_proof_requires_current_same_installation_and_authenticated_copy():
    m=tool();args=pg_inputs()
    assert m.pg_proof(*args)['operation_id']=='6'*32
    args[2]['fault_authority']['validated_at']='2026-11-05T01:00:00+00:00'
    assert m.pg_proof(*args)['operation_id']=='6'*32


@pytest.mark.parametrize('fault',['namespace','fingerprint','backup','source','phase','policy','owner','operation'])
def test_foreign_or_incomplete_pg_proof_cannot_close_provider_matrix(fault):
    m=tool();binding,authority,child,report=pg_inputs()
    if fault=='namespace':report['namespace_uid']=str(UUID(int=999))
    if fault=='fingerprint':report['runtime_fingerprint']='f'*64
    if fault=='backup':child['fault_authority']['backup_id']='f'*32
    if fault=='source':child['fault_authority']['source_journal_sha256']='f'*64
    if fault=='phase':child['phase']='normal-running'
    if fault=='policy':report['policy_removed']=False
    if fault=='owner':report['capacity']['owned_slots_at_finish']=1
    if fault=='operation':report['fault_operation_id']='f'*32
    with pytest.raises(ValueError):m.pg_proof(binding,authority,child,report)


@pytest.fixture
def rpc(tmp_path,monkeypatch):
    m=tool();calls=[];accepted={'value':False};fail={'value':False};revoked={'value':False}
    original_doc,export_raw,checks=export_fixture()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')));calls.append(self.path)
            if self.path=='/api/auth/simulate':
                self.send_response(200);self.send_header('Set-Cookie','session=private-cookie; Path=/');self.end_headers();self.wfile.write(b'{}')
            elif self.path=='/api/documents/'+ '1'*16+'/regenerate':
                assert self.headers.get('Cookie')=='session=private-cookie'
                accepted['value']=True
                if fail['value']:self.connection.shutdown(2);self.connection.close();return
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'id':'1'*16}).encode())
            elif self.path=='/api/auth/logout':
                revoked['value']=True;self.send_response(200);self.end_headers();self.wfile.write(b'{}')
            elif self.path=='/api/search':
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'hits':[{'doc_id':'1'*16}]}).encode())
            else:raise AssertionError(self.path)
        def do_GET(self):
            calls.append(self.path)
            if revoked['value'] and self.path=='/api/auth/me' and fail.get('anonymous_me'):
                user=dict(user_id='anonymous',username='anonymous',groups=[],roles=[])
                if fail.get('user_field'):user[fail['user_field']]=['privileged'] if fail['user_field'] in {'groups','roles'} else 'replica.admin'
                self.send_response(200);self.end_headers()
                self.wfile.write(json.dumps(dict(mode=fail.get('mode','simulation'),user=user)).encode());return
            allow_download=fail.get('download_allowed') and self.path=='/api/documents/'+'1'*16+'/download'
            self.send_response(200 if revoked['value'] and allow_download else 401 if revoked['value'] else 200);self.end_headers()
            if self.path=='/api/documents/'+'1'*16+'/download':self.wfile.write(original_doc)
            elif self.path=='/api/jobs/17/export/1':self.wfile.write(export_raw)
            else:self.wfile.write(b'{"user":{"username":"replica.admin"}}')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    original=urllib.request.OpenerDirector.open
    def redirected(opener,request,*args,**kwargs):
        assert request.full_url.startswith('http://127.0.0.1:8000/')
        request.full_url=request.full_url.replace(':8000/',':'+str(server.server_port)+'/',1)
        return original(opener,request,*args,**kwargs)
    monkeypatch.setattr(urllib.request.OpenerDirector,'open',redirected)
    import app.config
    import app.deployment.runtime_identity
    settings=SimpleNamespace(model_mode='stub',auth_provider='simulation',embedding_provider='fake',
        deployment_mode='distributed',artifact_backend='s3',
        deployment_namespace='replica-test-faults01',llm_base_url='http://model-stub:8000/v1')
    monkeypatch.setattr(app.config,'get_settings',lambda:settings)
    monkeypatch.setattr(app.deployment.runtime_identity,'enforce_runtime_identity',lambda _:SimpleNamespace(
        fingerprint='2'*64,installation_id='3'*32))
    request=dict(execution_id='4'*32,namespace='replica-test-faults01',runtime_fingerprint='2'*64,
        installation_id='3'*32,doc_id='1'*16,checks=checks)
    directory=tmp_path/'rpc'
    yield m,request,directory,calls,accepted,fail,settings
    server.shutdown();server.server_close();thread.join(timeout=3)


def test_rpc_lost_response_observes_cached_result_without_another_regeneration(rpc):
    m,request,directory,calls,accepted,fail,_=rpc
    assert m.pod_http({**request,'action':'login','observe':False},directory)['passed']
    assert m.pod_http({**request,'action':'qdrant.submit','observe':False},directory)['passed']
    result=m.pod_http({**request,'action':'qdrant.submit','observe':True},directory)
    assert result=={'passed':True,'document_id':'1'*16}
    assert accepted['value'] and calls.count('/api/documents/'+'1'*16+'/regenerate')==1
    assert 'private-cookie' not in json.dumps(result)


def test_rpc_unknown_http_ack_never_reissues_post(rpc):
    m,request,directory,calls,accepted,fail,_=rpc
    m.pod_http({**request,'action':'login','observe':False},directory);fail['value']=True
    with pytest.raises(Exception):m.pod_http({**request,'action':'model.submit','observe':False},directory)
    assert accepted['value']
    assert m.pod_http({**request,'action':'model.submit','observe':True},directory) is None
    assert m.pod_http({**request,'action':'model.submit','observe':False},directory) is None
    assert calls.count('/api/documents/'+'1'*16+'/regenerate')==1


def test_observer_cannot_create_a_login_or_remote_directory(rpc):
    m,request,directory,calls,*_=rpc
    assert m.pod_http({**request,'action':'login','observe':True},directory) is None
    assert calls==[] and not directory.exists()


def test_changed_runtime_is_refused_before_any_http_or_intent(rpc):
    m,request,directory,calls,_,_,settings=rpc;settings.model_mode='real'
    with pytest.raises(ValueError):m.pod_http({**request,'action':'login','observe':False},directory)
    assert calls==[] and not directory.exists()


def test_remote_receipts_reject_aliases_and_changed_binding(rpc,tmp_path):
    m,request,directory,calls,*_=rpc
    m.pod_http({**request,'action':'login','observe':False},directory)
    count=len(calls)
    with pytest.raises(ValueError):m.pod_http({**request,'doc_id':'a'*16,'action':'model.submit','observe':False},directory)
    outside=tmp_path/'outside';outside.write_text('{}');(directory/'model.submit.intent.json').hardlink_to(outside)
    with pytest.raises(ValueError):m.pod_http({**request,'action':'model.submit','observe':False},directory)
    assert len(calls)==count


def test_final_logout_is_explicit_and_old_session_is_revoked(rpc):
    m,request,directory,calls,*_=rpc
    m.pod_http({**request,'action':'login','observe':False},directory)
    assert m.pod_http({**request,'action':'logout','observe':False},directory)=={'passed':True,'session_revoked':True}
    assert m.pod_http({**request,'action':'logout','observe':True},directory)['session_revoked']
    assert calls.count('/api/auth/logout')==1


def test_logout_accepts_actual_anonymous_me_only_with_denied_protected_download(rpc):
    m,request,directory,calls,_,fail,_=rpc;fail['anonymous_me']=True
    m.pod_http({**request,'action':'login','observe':False},directory)
    assert m.pod_http({**request,'action':'logout','observe':False},directory)['session_revoked']
    (directory/'logout.result.json').unlink()
    assert m.pod_http({**request,'action':'logout','observe':True},directory)['session_revoked']
    assert m.pod_http({**request,'action':'logout','observe':False},directory)['session_revoked']
    assert calls.count('/api/auth/logout')==1
    assert calls.count('/api/documents/'+'1'*16+'/download')==3


@pytest.mark.parametrize('fault',['user_id','username','groups','roles','mode','download','401-me-download'])
def test_logout_refuses_remaining_identity_or_protected_access_without_reposting(rpc,fault):
    m,request,directory,calls,_,fail,_=rpc
    fail['anonymous_me']=fault!='401-me-download'
    if fault in {'user_id','username','groups','roles'}:fail['user_field']=fault
    if fault=='mode':fail['mode']='disabled'
    if fault in {'download','401-me-download'}:fail['download_allowed']=True
    m.pod_http({**request,'action':'login','observe':False},directory)
    with pytest.raises(ValueError):m.pod_http({**request,'action':'logout','observe':False},directory)
    assert m.pod_http({**request,'action':'logout','observe':True},directory) is None
    assert calls.count('/api/auth/logout')==1


@pytest.mark.parametrize('field,value',[('deployment_mode','compact'),('artifact_backend','local')])
def test_http_admission_checks_actual_storage_and_runtime_mode(rpc,field,value):
    m,request,directory,calls,_,_,settings=rpc
    settings.deployment_mode='distributed';settings.artifact_backend='s3';setattr(settings,field,value)
    with pytest.raises(ValueError):m.pod_http({**request,'action':'login','observe':False},directory)
    assert not calls and not directory.exists()


def test_resuming_login_after_receipt_write_loss_only_checks_existing_session(rpc,monkeypatch):
    m,request,directory,calls,*_=rpc
    m.pod_http({**request,'action':'login','observe':False},directory)
    (directory/'login.result.json').unlink()  # Result ACK/write lost; committed session remains.
    assert m.pod_http({**request,'action':'login','observe':True},directory)=={'passed':True,'authenticated':True}
    assert calls.count('/api/auth/simulate')==1


def test_functional_rpc_downloads_integer_export_job_from_real_http_contract(rpc):
    m,request,directory,calls,*_=rpc
    m.pod_http({**request,'action':'login','observe':False},directory)
    result=m.pod_http({**request,'action':'check','observe':True},directory)
    assert result['download_search_export_verified'] is True
    assert calls[-3:]==['/api/documents/'+'1'*16+'/download','/api/search','/api/jobs/17/export/1']


@pytest.mark.parametrize('job_id',[True,0,-1,'17','9'*32,2147483648])
def test_functional_rpc_refuses_invalid_export_job_before_download(rpc,job_id):
    m,request,directory,calls,*_=rpc
    m.pod_http({**request,'action':'login','observe':False},directory)
    count=len(calls)
    request['checks']['export_job']=job_id
    with pytest.raises(ValueError):m.pod_http({**request,'action':'check','observe':True},directory)
    assert len(calls)==count


def native_adapter(tmp_path,monkeypatch):
    m=tool();binding,authority,child,report=pg_inputs()
    pg_journal=tmp_path/'pg.json';pg_report=tmp_path/'pg-report.json'
    pg_journal.write_text(json.dumps(child));pg_report.write_text(json.dumps(report))
    for p in (pg_journal,pg_report):p.chmod(0o600)
    manifest={**binding,'provider_fault':dict(recipe='owned-managed-s3-dependencies-v1',doc_id='1'*16,
        pg_journal=str(pg_journal),pg_report=str(pg_report),checks=dict(original_sha256='8'*64,
        query='synthetic marker',export_job=17,export_part=1,export_sha256='a'*64))}
    value=dict(run_id='faults01',application_release='app',storage_release='storage',**binding)
    class Kube:
        def run(self,*args,**kwargs):
            assert args==('get',m.KINDS,'-o','json')
            return json.dumps({'items':objects()})
    class Owned:
        def __init__(self,manifest,journal):
            self.private=tmp_path;self.value=value;self.config={'installation_id':'1'*32}
            self.paths={'config':tmp_path/'config.json','source_journal':tmp_path/'upgrade.json'}
        def bind(self,state):
            self.kube=Kube();self.authority=SimpleNamespace(record=authority)
            return {'admission':authority,'images':{'api':'sha256:'+'7'*64}}
    monkeypatch.setattr(m,'OwnedAdapter',Owned)
    monkeypatch.setattr(m,'platform_binding',lambda kube,objects:{'nodes':{'owned-node':str(UUID(int=200))},'volumes':{}})
    return m,m.NativeAdapter(manifest,tmp_path/'provider.json'),manifest


def test_native_binding_uses_real_config_contract_and_separates_operation_receipts(tmp_path,monkeypatch):
    m,adapter,_=native_adapter(tmp_path,monkeypatch)
    one={'execution_id':'b'*32,'admission':None};two={'execution_id':'c'*32,'admission':None}
    first=adapter.bind(one)
    path1=adapter.policy.files.directory
    second=adapter.bind(two)
    assert first['postgres']['operation_id']=='6'*32 and second['postgres']['operation_id']=='6'*32
    assert path1!=adapter.policy.files.directory and path1.parent==adapter.private


@pytest.mark.parametrize('field,value',[('export_part',0),('export_job','../foreign'),('export_job',True),
    ('export_job',0),('export_job',-1),('export_job','17'),('export_job','9'*32),('export_job',2147483648),
    ('export_sha256','missing'),('query','')])
def test_invalid_functional_receipts_are_refused_before_native_admission(tmp_path,monkeypatch,field,value):
    m,adapter,manifest=native_adapter(tmp_path,monkeypatch)
    manifest['provider_fault']['checks'][field]=value
    with pytest.raises(ValueError):m.NativeAdapter(manifest,tmp_path/'next.json')


def platform():
    claims=[dict(kind='PersistentVolumeClaim',metadata=dict(name='claim'+str(i),namespace='replica-test-faults01',uid=str(UUID(int=100+i))),
        spec={'volumeName':'pv'+str(i)},status={'phase':'Bound'}) for i in range(3)]
    node=dict(kind='Node',metadata={'name':'owned-node','uid':str(UUID(int=200))},spec={'providerID':'kind://docker/owned'},
        status={'conditions':[{'type':'Ready','status':'True'}]})
    volumes={c['spec']['volumeName']:dict(kind='PersistentVolume',metadata={'name':c['spec']['volumeName'],'uid':str(UUID(int=300+i))},
        spec={'claimRef':{k:c['metadata'][k] for k in ('uid','namespace','name')},'storageClassName':'owned'}) for i,c in enumerate(claims)}
    class Kube:
        def run(self,*args,**kwargs):
            if args==('get','node','owned-node','-o','json'):return json.dumps(node)
            assert args[0:2]==('get','pv') and args[-2:]==('-o','json')
            return json.dumps(volumes[args[2]])
    return Kube(),objects()+claims,node,volumes


def test_node_and_volume_replacement_change_native_admission():
    m=tool();kube,rows,node,volumes=platform();before=m.platform_binding(kube,rows)
    node['metadata']['uid']=str(UUID(int=999))
    assert m.platform_binding(kube,rows)!=before
    node['metadata']['uid']=str(UUID(int=200));volumes['pv0']['metadata']['uid']=str(UUID(int=998))
    assert m.platform_binding(kube,rows)!=before


@pytest.mark.parametrize('fault',['node-not-ready','foreign-claim','unbound-claim'])
def test_unreachable_node_or_mismatched_pv_claim_is_refused(fault):
    m=tool();kube,rows,node,volumes=platform()
    if fault=='node-not-ready':node['status']['conditions'][0]['status']='Unknown'
    if fault=='foreign-claim':volumes['pv0']['spec']['claimRef']['uid']=str(UUID(int=999))
    if fault=='unbound-claim':rows[-1]['status']['phase']='Pending'
    with pytest.raises(ValueError):m.platform_binding(kube,rows)


def test_sql_observer_starts_readonly_before_queries_and_reads_real_corpus(monkeypatch,capsys):
    """The session_scope business guard already queries PG before SET isolation."""
    from contextlib import contextmanager
    from sqlalchemy import create_engine,text
    import app.config
    import app.db.session
    import app.deployment.runtime_identity
    m=tool();engine=create_engine('sqlite://')
    ddl=["CREATE TABLE documents(id TEXT,status TEXT,problem TEXT,deleted_at TEXT)",
        "CREATE TABLE artifact_bindings(owner_type TEXT,owner_id TEXT,logical_name TEXT,object_id TEXT)",
        "CREATE TABLE artifact_objects(object_id TEXT,sha256 TEXT)",
        "CREATE TABLE document_generation_states(doc_id TEXT,active_generation_id TEXT)",
        "CREATE TABLE okf_concepts(doc_id TEXT,slug TEXT,title TEXT,content TEXT)",
        "CREATE TABLE document_generations(doc_id TEXT,phase TEXT)",
        "CREATE TABLE work_items(id TEXT,kind TEXT,doc_id TEXT,status TEXT,attempts INTEGER,fence INTEGER,error_code TEXT,cancel_requested BOOLEAN,owner_id TEXT,lease_until TEXT,created_at TEXT)",
        "CREATE TABLE replica_maintenance_state(id INTEGER,enabled BOOLEAN,epoch INTEGER)",
        "CREATE TABLE replica_capacity_slots(resource TEXT,owner_id TEXT)","CREATE TABLE chat_runs(status TEXT)"]
    with engine.begin() as c:
        for sql in ddl:c.execute(text(sql))
        c.execute(text('INSERT INTO documents VALUES (:id,\'done\',NULL,NULL)'),{'id':'1'*16})
        c.execute(text("INSERT INTO artifact_bindings VALUES ('original',:id,'original','object')"),{'id':'1'*16})
        c.execute(text("INSERT INTO artifact_objects VALUES ('object',:sha)"),{'sha':'4'*64})
        c.execute(text('INSERT INTO document_generation_states VALUES (:id,:gen)'),{'id':'1'*16,'gen':'2'*32})
        c.execute(text("INSERT INTO document_generations VALUES (:id,'active')"),{'id':'1'*16})
        c.execute(text("INSERT INTO okf_concepts VALUES (:id,'concept','Synthetic title','Synthetic content')"),{'id':'1'*16})
        c.execute(text("INSERT INTO work_items VALUES (:work,'document',:id,'succeeded',1,1,NULL,0,NULL,NULL,'now')"),{'id':'1'*16,'work':'a'*32})
        c.execute(text('INSERT INTO replica_maintenance_state VALUES (1,0,12)'))
    seen=[]
    class Connection:
        def __init__(self,connection):self.connection=connection
        def execute(self,statement,*args,**kwargs):
            if not seen:
                assert str(statement)=='SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
                seen.append('readonly-first')
                return self.connection.execute(text('PRAGMA query_only=ON'))
            return self.connection.execute(statement,*args,**kwargs)
        def scalar(self,*args,**kwargs):return self.execute(*args,**kwargs).scalar()
    class Engine:
        @contextmanager
        def connect(self):
            with engine.connect() as connection:
                yield Connection(connection)
                with pytest.raises(Exception):connection.execute(text("DELETE FROM documents"))
    def business_session_forbidden():raise AssertionError('Business session starts queries before isolation is set')
    monkeypatch.setattr(app.db.session,'session_scope',business_session_forbidden)
    monkeypatch.setattr(app.db.session,'get_engine',lambda:Engine())
    monkeypatch.setattr(app.config,'get_settings',lambda:SimpleNamespace(model_mode='stub',auth_provider='simulation',
        embedding_provider='fake',deployment_mode='distributed',artifact_backend='s3'))
    monkeypatch.setattr(app.deployment.runtime_identity,'enforce_runtime_identity',lambda _:SimpleNamespace(
        fingerprint='5'*64,installation_id='6'*32))
    expected=dict(doc_id='1'*16,runtime_fingerprint='5'*64,installation_id='6'*32)
    exec(compile(m.SQL_SOURCE,'native-provider-sql','exec'),{'EXPECTED':expected})
    observed=json.loads(capsys.readouterr().out)
    assert seen==['readonly-first'] and observed['publication']['active_count']==1
    assert observed['publication']['original_sha256']=='4'*64 and observed['works'][0]['id']=='a'*32
    assert len(observed['corpus'])==1 and observed['running']==observed['chats']==observed['model_owners']==0
    with engine.connect() as connection:assert connection.scalar(text('SELECT count(*) FROM documents'))==1
    engine.dispose()


@pytest.mark.parametrize('field',['publication','works','corpus'])
def test_final_cannot_accept_changed_target_after_the_last_verified_recovery(field):
    m=tool();adapter=m.NativeAdapter.__new__(m.NativeAdapter)
    original=dict(publication={'active_generation_id':'a'*32},works=[{'id':'b'*32}],
        corpus=[{'id':'c'*16}],maintenance={'epoch':12,'enabled':False},model_owners=0,running=0,chats=0)
    changed=copy.deepcopy(original);changed[field]=[] if isinstance(original[field],list) else {'active_generation_id':'d'*32}
    calls=[]
    adapter.positive=lambda:[];adapter.model=lambda:{'active':0};adapter.database=lambda:changed
    adapter.kube=SimpleNamespace(run=lambda *a,**k:'{"items":[]}')
    adapter.rpc=lambda *a,**k:calls.append(a) or {'passed':True};adapter.pg={}
    state={'completed':{'qdrant.verify':{'outcome':{'passed':True}},
        'model.verify':{'outcome':{'passed':True,'database':original}}}}
    with pytest.raises(ValueError):adapter.dispatch('final',state,observe=False)
    assert calls==[]
    adapter.database=lambda:original
    assert adapter.dispatch('final',state,observe=False)['passed'] is True
    assert len(calls)==1
