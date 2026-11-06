"""Owned OIDC lifecycle, using native observations and one-shot mutation journals."""
import copy
import json
from urllib.parse import urlsplit

import pytest

from tests.test_replica_frontend_native import envelope
from tests.test_replica_frontend_oidc_native import NativeKube, module, value
from tests.test_replica_frontend_acceptance import uid


class Probe:
    calls=[]
    def __init__(self,directory,origin,identity,port,username,role,execution):
        from test_scripts.replica_frontend_http import PrivateFiles
        self.files=PrivateFiles(directory);self.origin=origin;self.identity=identity;self.port=port
        self.username=username;self.execution_id=execution
        self.calls.append(('probe',origin,identity,port,username,role))
    def reject_unknown_issuer(self):self.calls.append(('unknown-ca',))
    def _request(self,method,url,**kwargs):
        self.calls.append(('anonymous',method,url,kwargs));return 404,b'',None


class HTTP:
    def __init__(self,probe):self.probe=probe;self.files=probe.files
    def session(self,password):
        Probe.calls.append(('login',password))
        result=dict(shared_routes=3,csrf='verified-real-sso',internal_routes_hidden=True)
        self.files.once('session.result.json',result);return result
    def shared_session(self):Probe.calls.append(('shared',))
    def publish(self):return dict(status='passed',verified_routes=3)
    def export_observe(self):return dict(status='passed',export_sha256='e'*64)
    def effect(self,name,result):self.files.once(name+'.result.json',result);return result
    def upload(self):return self.effect('upload',dict(document_id='a'*16,original_sha256='f'*64))
    def export(self):return self.effect('export',dict(job_id=7))
    def stream(self):
        Probe.kube.metrics.update(started=2,finished=2)
        return self.effect('stream',dict(unbuffered_frontends=2))
    def logout(self):return self.effect('logout',dict(old_session_revoked_routes=3))


class Processes:
    def __init__(self,files):self.files=files;self.calls=[];self.routes={}
    def forward_gateway(self,slot,pod,port,*,tls):
        self.calls.append((slot,copy.deepcopy(pod),port,tls))
        scheme='https' if tls else 'http'
        self.routes[slot]=dict(url=scheme+'://127.0.0.1:'+str(25400+len(self.routes)),pod=pod,slot=slot,namespace=value()['oidc_access_namespace'])
        return self.routes[slot]
    def route(self,slot):return self.routes[slot]
    def describe(self,pid):return None if self.kube.client_gone else {'pid':pid}
    def start_client(self,program,configuration):
        self.calls.append(('client',program,configuration))
        self.kube.metrics.update(started=3,finished=2,active=1)
        self.files.once('client.pid.json',{'pid':1234})
        self.files.once('loss.progress.json',dict(terminal=False,sources_seen=True,result_seen=False,protocol_error=False,error_seen=False))
    def stop(self,slot):
        self.calls.append(('stop',slot))
        if not self.files.path(slot+'.closed.json').exists():self.files.once(slot+'.closed.json',dict(original_process_gone=True))


class LifecycleKube(NativeKube):
    def __init__(self):
        super().__init__();self.metrics=dict(started=0,finished=0,active=0);self.client_gone=False
    def python(self,pod,source):
        if 'FRONTEND_MODEL_METRICS' in source:return self.metrics.copy()
        if 'FRONTEND_FINAL_PROOF' in source:return dict(quiescent=True,original_sha256='f'*64,model=self.metrics.copy())
        return super().python(pod,source)
    def run(self,*args,source=None):
        if args[:2]==('get','pod'):
            found=next((p for p in self.parts[1] if p['kind']=='Pod' and p['metadata']['name']==args[2]),None)
            return json.dumps(found) if found else ''
        if args[0]=='delete':
            self.calls.append((*args,json.loads(source)))
            return json.dumps(dict(kind='Status',status='Success'))
        if args[:2]==('patch','pod'):
            assert source is None and '-p' in args
            patch=json.loads(args[args.index('-p')+1]);self.calls.append((*args,patch))
            pod=next(p for p in self.parts[1] if p['kind']=='Pod' and p['metadata']['name']==args[2])
            assert patch[:2]==[dict(op='test',path='/metadata/uid',value=pod['metadata']['uid']),
                dict(op='test',path='/metadata/resourceVersion',value=pod['metadata']['resourceVersion'])]
            assert patch[2]==dict(op='add',path='/metadata/labels/okf-f2-probe-slot',value='first')
            pod['metadata']['labels']['okf-f2-probe-slot']='first'
            for ep in self.parts[1]:
                if ep['kind']=='EndpointSlice' and ep['metadata']['labels'].get('kubernetes.io/service-name')=='frontend-slot-first':
                    ep['endpoints']=[dict(addresses=[pod['status']['podIP']],conditions={'ready':True},targetRef=dict(kind='Pod',
                        name=pod['metadata']['name'],namespace=value()['namespace'],uid=pod['metadata']['uid']))]
            return json.dumps(pod)
        return super().run(*args,source=source)


def coordinator(tmp_path):
    from test_scripts.replica_frontend_http import PrivateFiles
    Probe.calls=[];kube=LifecycleKube();Probe.kube=kube;process=Processes(PrivateFiles(tmp_path));process.kube=kube
    current=module().OIDCCoordinator(tmp_path,value(),kube=kube,processes=process,http_factory=HTTP,probe_factory=Probe)
    current.anonymous_redirect=lambda route,origin:Probe.calls.append(('http-redirect',route,origin))
    current.dispatch(envelope())
    return current,kube,process


def test_session_uses_only_two_gateway_forwards_and_private_native_password(tmp_path):
    current,_,process=coordinator(tmp_path)
    result=current.dispatch(envelope('session'))
    assert [c[0] for c in process.calls]==['gw-https','gw-http']
    assert [(c[2],c[3]) for c in process.calls]==[(8443,True),(8080,False)]
    assert all(c[1]==current.files.read('scope.json')['oidc']['gateway'] for c in process.calls)
    assert (tmp_path/'oidc-ca.pem').read_bytes()==b'CA'
    login=next(i for i,c in enumerate(Probe.calls) if c[0]=='login')
    assert all(next(i for i,c in enumerate(Probe.calls) if c[0]==control)<login for control in ['http-redirect','unknown-ca','anonymous'])
    assert Probe.calls[login][1]=='SYNTHETIC_PRIVATE_admin'
    assert 'PRIVATE' not in json.dumps(result)


def test_session_terminal_receipt_adopts_without_login_or_forward_replay(tmp_path):
    current,_,process=coordinator(tmp_path);request=envelope('session')
    result=current.dispatch(request);count=len(Probe.calls)
    assert current.dispatch(request,observe=True)==result
    assert len(process.calls)==2 and len(Probe.calls)==count


@pytest.mark.parametrize('fault',['gateway_uid','namespace','plain_tls'])
def test_http_refuses_changed_gateway_forward_before_a_probe_is_constructed(fault,tmp_path):
    current,_,process=coordinator(tmp_path);current.dispatch(envelope('session'))
    route=process.routes['gw-https']
    if fault=='gateway_uid':route['pod']={'name':'foreign','uid':'f'*36}
    elif fault=='namespace':route['namespace']='customer'
    else:route['url']='http://127.0.0.1:25400'
    count=len(Probe.calls)
    with pytest.raises(ValueError):current.http()
    assert len(Probe.calls)==count


def test_cookie_free_http_redirect_is_bounded_and_does_not_follow(monkeypatch):
    import http.client
    calls=[]
    class Connection:
        def __init__(self,host,port,timeout):calls.append((host,port,timeout))
        def request(self,method,path,headers):calls.append((method,path,headers))
        def getresponse(self):
            class Response:
                status=308
                def read(self,size):return b''
                def getheader(self,key):return 'https://knowledge.okf-f2-abcdefgh.svc.cluster.local/'
            return Response()
        def close(self):calls.append('closed')
    monkeypatch.setattr(http.client,'HTTPConnection',Connection)
    assert hasattr(module().OIDCCoordinator,'anonymous_redirect'),'Native HTTP redirect control missing'
    origin='https://knowledge.okf-f2-abcdefgh.svc.cluster.local'
    module().OIDCCoordinator.anonymous_redirect({'url':'http://127.0.0.1:25401'},origin)
    assert calls[0]==('127.0.0.1',25401,20)
    assert calls[1]==('GET','/',{'Host':urlsplit(origin).netloc,'Connection':'close'}) and calls[-1]=='closed'


def before_loss(tmp_path):
    from hashlib import sha256
    from pathlib import Path
    current,kube,process=coordinator(tmp_path)
    for stage in ['session','upload','publish','export','export-observe','stream']:current.dispatch(envelope(stage))
    names=('replica_frontend_http','replica_frontend_oidc_http','replica_frontend_oidc_business')
    sources={n:(Path(__file__).resolve().parents[1]/'test_scripts'/(n+'.py')).read_text() for n in names}
    current.files.once('capsule.json',{n:sha256(s.encode()).hexdigest() for n,s in sources.items()})
    for n,s in sources.items():current.files.once('module-'+n.replace('_','-')+'.json',{'source':s})
    current.sleep=lambda _:None
    return current,kube,process


def replaced(current,kube):
    victim=current.files.read('scope.json')['oidc_slot_frontends']['first']
    old=next(p for p in kube.parts[1] if p['kind']=='Pod' and p['metadata']['uid']==victim['uid'])
    old['metadata'].update(name='frontend-new',uid=uid('frontend-new'),resourceVersion='99')
    old['metadata']['labels'].pop('okf-f2-probe-slot');old['status']['podIP']='10.1.0.3'
    for ep in kube.parts[1]:
        if ep['kind']=='EndpointSlice':
            if ep['metadata']['labels'].get('kubernetes.io/service-name')=='frontend-slot-first':ep['endpoints']=[]
            else:
                for endpoint in ep['endpoints']:
                    if endpoint.get('targetRef',{}).get('uid')==victim['uid']:
                        endpoint['targetRef'].update(uid=old['metadata']['uid'],name=old['metadata']['name'])
                        endpoint['addresses']=[old['status']['podIP']]
    kube.client_gone=True;kube.metrics.update(finished=3,active=0)
    progress=current.files.read('loss.progress.json');progress.update(terminal=True)
    current.files.path('loss.progress.json').write_text(json.dumps(progress))


def test_loss_uses_verified_tls_child_and_exactly_one_uid_delete(tmp_path):
    current,kube,process=before_loss(tmp_path)
    request=envelope('loss');result=current.dispatch(request)
    deletes=[c for c in kube.calls if c[0]=='delete']
    assert len(deletes)==1 and result['status']=='passed'
    assert deletes[0][-1]['preconditions']['uid']==current.files.read('scope.json')['oidc_slot_frontends']['first']['uid']
    child=next(c for c in process.calls if c[0]=='client')
    assert 'GatewayFrontendHTTP' in child[1] and 'hashlib.sha256' in child[1]
    assert 'PRIVATE' not in json.dumps(child[2])
    assert current.dispatch(request,observe=True)==result
    assert len([c for c in process.calls if c[0]=='client'])==1


@pytest.mark.parametrize('fault',['protocol_error','error_seen','tampered_module'])
def test_bad_tls_stream_or_child_source_never_allows_delete(fault,tmp_path):
    current,kube,process=before_loss(tmp_path)
    start=process.start_client
    def changed(*args):
        start(*args)
        state=current.files.read('loss.progress.json');state[fault]=True
        current.files.path('loss.progress.json').write_text(json.dumps(state))
    if fault=='tampered_module':current.files.path('module-replica-frontend-oidc-http.json').write_text(json.dumps({'source':'tampered'}))
    else:process.start_client=changed
    with pytest.raises(ValueError):current.dispatch(envelope('loss'))
    assert not [c for c in kube.calls if c[0]=='delete']


def test_loss_observation_accepts_api_null_slice_only_after_owned_delete(tmp_path):
    current,kube,_=before_loss(tmp_path);current.dispatch(envelope('loss'));replaced(current,kube)
    ep=next(o for o in kube.parts[1] if o['kind']=='EndpointSlice' and o['metadata']['labels'].get('kubernetes.io/service-name')=='frontend-slot-first')
    ep['endpoints']=None
    assert current.dispatch(envelope('loss-observe'))['status']=='passed'
    assert len([c for c in kube.calls if c[0]=='delete'])==1


def test_replacement_patches_one_uid_and_resource_version_then_preserves_session(tmp_path):
    current,kube,process=before_loss(tmp_path);current.dispatch(envelope('loss'));replaced(current,kube)
    assert current.dispatch(envelope('loss-observe'))['status']=='passed'
    request=envelope('replacement');result=current.dispatch(request)
    assert result['status']=='passed' and result['evidence']['session_survived']
    assert len([c for c in kube.calls if c[0]=='patch'])==1
    assert current.dispatch(request,observe=True)==result
    assert len([c for c in kube.calls if c[0]=='patch'])==1
    assert len([c for c in process.calls if c[0] in ['gw-http','gw-https']])==2
    current.dispatch(envelope('logout'));final=current.dispatch(envelope('final'))
    assert final['evidence']['owned_handles_closed']==3
    assert [c[1] for c in process.calls if c[0]=='stop']==['gw-https','gw-http','client']


def test_lost_patch_ack_is_observed_without_resending_the_patch(tmp_path):
    current,kube,_=before_loss(tmp_path);current.dispatch(envelope('loss'));replaced(current,kube)
    current.dispatch(envelope('loss-observe'))
    run=kube.run
    def lost(*args,**kwargs):
        result=run(*args,**kwargs)
        if args[0]=='patch':raise OSError('synthetic lost ACK')
        return result
    kube.run=lost;request=envelope('replacement')
    with pytest.raises(OSError):current.dispatch(request)
    assert not current.files.path('replacement.result.json').exists()
    assert current.dispatch(request,observe=True)['status']=='passed'
    assert len([c for c in kube.calls if c[0]=='patch'])==1


def test_lost_delete_ack_is_observed_from_the_same_victim_uid_without_replay(tmp_path):
    current,kube,process=before_loss(tmp_path);run=kube.run
    def lost(*args,**kwargs):
        result=run(*args,**kwargs)
        if args[0]=='delete':raise OSError('synthetic lost ACK')
        return result
    kube.run=lost;request=envelope('loss')
    with pytest.raises(OSError):current.dispatch(request)
    replaced(current,kube)
    assert current.dispatch(request,observe=True)['status']=='passed'
    assert len([c for c in kube.calls if c[0]=='delete'])==1
    assert len([c for c in process.calls if c[0]=='client'])==1


def test_replacement_uses_supported_inline_json_patch_not_stdin_patch_file(tmp_path):
    current,kube,_=before_loss(tmp_path);current.dispatch(envelope('loss'));replaced(current,kube)
    current.dispatch(envelope('loss-observe'));run=kube.run
    def supported(*args,source=None):
        if args[0]=='patch':
            assert '--patch-file=-' not in args and '-p' in args,'kubectl patch-file does not support stdin'
            assert source is None
        return run(*args,source=source)
    kube.run=supported
    assert current.dispatch(envelope('replacement'))['status']=='passed'
