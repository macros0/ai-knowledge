"""Fixed native OIDC recipe admission; no customer endpoint or receipt inputs."""
from __future__ import annotations

import re
import json
import base64
from hashlib import sha256
from http.cookiejar import CookieJar
import http.client
import os
from urllib.parse import urlsplit

from test_scripts.replica_frontend_acceptance import validate_frontend_inventory
from test_scripts.replica_frontend_native import Coordinator, STAGES, digest
from test_scripts.replica_frontend_oidc_http import OIDCProbe
from test_scripts.replica_frontend_oidc_business import GatewayFrontendHTTP

RECIPE = 'owned-keycloak-local-v1'
PLATFORM_NAMESPACE = 'okf-f2-gateway-system'
GROUP = 'gateway.networking.k8s.io'
CHILD_MODULES = ('replica_frontend_http','replica_frontend_oidc_http','replica_frontend_oidc_business')

OIDC_LOSS_CLIENT = r'''import hashlib,json,os,pathlib,stat,sys,types
from uuid import uuid4
p=json.loads(pathlib.Path(sys.argv[1]).read_text());root=pathlib.Path(p['directory'])
meta=root.lstat();assert stat.S_ISDIR(meta.st_mode) and not root.is_symlink() and meta.st_uid==os.getuid() and meta.st_mode&0o077==0
def load(name):
 path=root/name;fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as f:
  info=os.fstat(f.fileno());assert stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.getuid() and info.st_mode&0o077==0
  raw=f.read(1048577);assert len(raw)<=1048576
 return json.loads(raw)
capsule=load('capsule.json')
assert hashlib.sha256(json.dumps(capsule,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()==p['capsule_sha256']
package=types.ModuleType('test_scripts');package.__path__=[];sys.modules['test_scripts']=package
for name in ('replica_frontend_http','replica_frontend_oidc_http','replica_frontend_oidc_business'):
 source=load('module-'+name.replace('_','-')+'.json')['source']
 assert hashlib.sha256(source.encode()).hexdigest()==capsule[name]
 module=types.ModuleType('test_scripts.'+name);module.__file__=str(root/(name+'.py'));sys.modules[module.__name__]=module
 exec(compile(source,name+'.py','exec'),module.__dict__)
from test_scripts.replica_frontend_oidc_http import OIDCProbe
from test_scripts.replica_frontend_oidc_business import GatewayFrontendHTTP
probe=OIDCProbe(root,p['origin'],p['identity'],p['port'],p['username'],'admin',p['execution_id'])
def save(state):
 name='progress-'+uuid4().hex+'.pending';probe.files.once(name,state)
 probe.files.path('loss.progress.json');os.replace(probe.files.path(name),root/'loss.progress.json');probe.files.sync()
save(dict(terminal=False,sources_seen=False,result_seen=False,protocol_error=False,error_seen=False,event_count=0))
GatewayFrontendHTTP(probe).loss_stream(save)
'''


def require(condition):
    if not condition: raise ValueError('Owned OIDC scope refused')


def validate_oidc_inputs(value):
    require(value.get('functional_recipe') == RECIPE and value.get('profile') == 'basic'
        and value.get('deployment_mode') == 'frontend-split')
    run = value.get('run_id', '')
    require(isinstance(run, str) and re.fullmatch('[a-z0-9]{8,32}', run))
    require(value.get('oidc_access_namespace') == 'okf-f2-' + run and value.get('oidc_username') == 'lab-admin')
    for key in ('oidc_access_namespace_uid','oidc_platform_namespace_uid','oidc_controller_uid','oidc_gateway_class_uid'):
        require(isinstance(value.get(key), str) and re.fullmatch('[0-9a-f-]{36}', value[key]))
    images = value.get('oidc_expected_images')
    require(isinstance(images, dict) and set(images) == {'gateway','keycloak','controller'})
    require(all(isinstance(x,str) and re.fullmatch('sha256:[0-9a-f]{64}',x) for x in images.values()))


def one(objects, kind, name):
    matches = [o for o in objects if o['kind']==kind and o['metadata']['name']==name]
    require(len(matches)==1)
    return matches[0]


def namespace(objects, name, identity, value, *, platform=False):
    obj = one(objects,'Namespace',name);meta=obj['metadata'];labels=meta.get('labels',{})
    require(meta['uid']==identity and not meta.get('deletionTimestamp') and labels.get('okf-purpose')=='replica-test')
    if platform:require(re.fullmatch('[0-9a-f]{32}',labels.get('okf-f2-platform','')))
    else:
        require(labels.get('okf-run-id')==value['run_id'] and labels.get('okf-installation-id')==value['installation_id']
            and labels.get('app.kubernetes.io/managed-by')=='okf-replica-acceptance' and labels.get('okf-f2-gateway')=='true')
    require(all(o['metadata'].get('namespace')==name for o in objects if o['kind']!='Namespace'))
    return obj


def ready_conditions(obj, names):
    found = obj.get('status',{}).get('conditions',[])
    for name in names:
        rows = [x for x in found if x['type']==name]
        require(len(rows)==1 and rows[0].get('status')=='True'
            and rows[0].get('observedGeneration')==obj['metadata']['generation'])


def safe_pod(spec):
    require(not any(spec.get(k) for k in ('hostNetwork','hostPID','hostIPC','initContainers')))
    require(not any('hostPath' in v for v in spec.get('volumes',[])))
    for c in spec.get('containers',[]):
        require(not c.get('securityContext',{}).get('privileged'))
        require(not any(p.get('hostPort') for p in c.get('ports',[])))


def workload(objects, name, image, *, identity=None, gateway=None):
    dep = one(objects,'Deployment',name);spec=dep['spec'];status=dep.get('status',{})
    require((identity is None or dep['metadata']['uid']==identity) and spec['replicas']==1
        and status.get('observedGeneration')==dep['metadata']['generation']
        and all(status.get(k)==1 for k in ('readyReplicas','availableReplicas','updatedReplicas')))
    if gateway:
        refs=dep['metadata'].get('ownerReferences',[])
        require(len(refs)==1 and refs[0].get('kind')=='Gateway' and refs[0].get('name')==gateway['metadata']['name']
            and refs[0].get('uid')==gateway['metadata']['uid'])
    else:require(not dep['metadata'].get('ownerReferences'))
    selector=spec['selector']['matchLabels'];require(bool(selector))
    pods=[p for p in objects if p['kind']=='Pod' and all(p['metadata'].get('labels',{}).get(k)==v for k,v in selector.items())]
    require(len(pods)==1);pod=pods[0];safe_pod(spec['template']['spec']);safe_pod(pod['spec'])
    cs=pod.get('status',{}).get('containerStatuses',[])
    require(pod['status'].get('phase')=='Running' and len(cs)==len(pod['spec']['containers'])==len(spec['template']['spec']['containers'])==1)
    require(cs[0].get('ready') is True and cs[0].get('restartCount')==0 and cs[0].get('imageID','').endswith(image)
        and pod['spec']['containers'][0]['image']==spec['template']['spec']['containers'][0]['image'])
    refs=pod['metadata'].get('ownerReferences',[]);require(len(refs)==1 and refs[0]['kind']=='ReplicaSet')
    rs=one(objects,'ReplicaSet',refs[0]['name']);require(rs['metadata']['uid']==refs[0]['uid'])
    owners=rs['metadata'].get('ownerReferences',[]);require(len(owners)==1 and owners[0]['kind']=='Deployment' and owners[0]['uid']==dep['metadata']['uid'])
    return pod


def endpoints(objects, service, pods):
    expected={p['metadata']['uid']:p for p in pods};found=set()
    for ep in objects:
        if ep['kind']!='EndpointSlice' or ep['metadata'].get('labels',{}).get('kubernetes.io/service-name')!=service['metadata']['name']:continue
        refs=ep['metadata'].get('ownerReferences',[])
        require(len(refs)==1 and refs[0]['kind']=='Service' and refs[0]['uid']==service['metadata']['uid'])
        for e in ep.get('endpoints',[]):
            if e.get('conditions',{}).get('ready') is not True:continue
            ref=e.get('targetRef',{});identity=ref.get('uid')
            require(ref.get('kind')=='Pod' and identity in expected and not e['conditions'].get('terminating'))
            require(ref.get('namespace',service['metadata']['namespace'])==service['metadata']['namespace'])
            require(e.get('addresses')==[expected[identity]['status']['podIP']]);found.add(identity)
    require(found==set(expected))


def internal_service(objects, name, pods):
    svc=one(objects,'Service',name);spec=svc['spec'];selector=spec.get('selector',{})
    require(spec.get('type','ClusterIP')=='ClusterIP' and not spec.get('externalName') and not spec.get('externalIPs')
        and spec.get('clusterIP')!='None' and selector and not any(p.get('nodePort') for p in spec.get('ports',[])))
    selected=[p for p in objects if p['kind']=='Pod' and all(p['metadata'].get('labels',{}).get(k)==v for k,v in selector.items())]
    require({p['metadata']['uid'] for p in selected}=={p['metadata']['uid'] for p in pods})
    endpoints(objects,svc,pods)
    return svc


def listener(objects, name, host, access, namespace_name):
    obj=one(objects,'ListenerSet',name);spec=obj['spec'];parent=spec['parentRef']
    require(parent.get('group')==GROUP and parent.get('kind')=='Gateway' and parent.get('name')=='home-gateway'
        and parent.get('namespace',namespace_name)==access and len(spec['listeners'])==1)
    item=spec['listeners'][0];tls=item.get('tls',{});certs=tls.get('certificateRefs',[])
    require(item['hostname']==host and item['port']==443 and item['protocol']=='HTTPS' and tls.get('mode')=='Terminate'
        and len(certs)==1 and certs[0].get('name')=='lab-tls' and certs[0].get('namespace',namespace_name)==namespace_name
        and certs[0].get('kind','Secret')=='Secret' and certs[0].get('group','')==''
        and item['allowedRoutes']=={'namespaces':{'from':'Same'}})
    ready_conditions(obj,('Accepted','ResolvedRefs'))


def route(objects, name, host, parent, backend, port, slot=None):
    obj=one(objects,'HTTPRoute',name);spec=obj['spec']
    def parent_ref(ref):
        require(ref.get('name')==parent and ref.get('kind')=='ListenerSet' and ref.get('group',GROUP)==GROUP
            and ref.get('namespace',obj['metadata']['namespace'])==obj['metadata']['namespace']
            and ref.get('port') is None and ref.get('sectionName') is None)
    refs=spec.get('parentRefs',[]);require(len(refs)==1);parent_ref(refs[0])
    require(spec.get('hostnames')==[host] and len(spec['rules'])==1)
    rule=spec['rules'][0];expected_match={'path':{'type':'PathPrefix','value':'/'}}
    if slot:expected_match['headers']=[dict(type='Exact',name='X-Okf-Probe-Slot',value=slot)]
    backends=rule.get('backendRefs',[]);require(len(backends)==1);target=backends[0]
    require(target.get('name')==backend and target.get('port')==port and target.get('kind','Service')=='Service'
        and target.get('group','')=='' and target.get('namespace',obj['metadata']['namespace'])==obj['metadata']['namespace']
        and target.get('weight',1)==1 and not target.get('filters'))
    require(rule.get('matches')==[expected_match]
        and rule.get('timeouts')=={'request':'300s','backendRequest':'300s'} and not rule.get('filters'))
    parents=obj.get('status',{}).get('parents',[]);require(len(parents)==1)
    parent_ref(parents[0].get('parentRef',{}));require(parents[0].get('controllerName')=='kgateway.dev/kgateway')
    ready_conditions({'metadata':obj['metadata'],'status':{'conditions':parents[0]['conditions']}},('Accepted','ResolvedRefs'))


def redirect_route(objects,hosts):
    obj=one(objects,'HTTPRoute','https-redirect');spec=obj['spec'];ns=obj['metadata']['namespace']
    def parent(ref):
        require(ref.get('group',GROUP)==GROUP and ref.get('kind','Gateway')=='Gateway' and ref.get('name')=='home-gateway'
            and ref.get('namespace',ns)==ns and ref.get('sectionName')=='http' and ref.get('port') is None)
    refs=spec.get('parentRefs',[]);require(len(refs)==1);parent(refs[0])
    require(spec.get('hostnames')==hosts and spec.get('rules')==[dict(matches=[{'path':dict(type='PathPrefix',value='/')}],
        filters=[dict(type='RequestRedirect',requestRedirect=dict(scheme='https',statusCode=308))])])
    parents=obj.get('status',{}).get('parents',[]);require(len(parents)==1);parent(parents[0]['parentRef'])
    require(parents[0].get('controllerName')=='kgateway.dev/kgateway')
    ready_conditions({'metadata':obj['metadata'],'status':{'conditions':parents[0]['conditions']}},('Accepted','ResolvedRefs'))


def validate_oidc_graph(value, app, access, platform, gatewayclass, *, slot_bindings=None):
    """Return hashes and public identities only; no credential selection or action."""
    try:
        validate_oidc_inputs(value);validate_frontend_inventory(app,value)
        ns=value['namespace'];an=value['oidc_access_namespace'];images=value['oidc_expected_images']
        namespace(app,ns,value['namespace_uid'],value)
        namespace(access,an,value['oidc_access_namespace_uid'],value)
        namespace(platform,PLATFORM_NAMESPACE,value['oidc_platform_namespace_uid'],value,platform=True)
        require(not any(o['kind'] in {'Job','CronJob','DaemonSet','StatefulSet','PersistentVolumeClaim'} for o in access))
        require({o['metadata']['name'] for o in access if o['kind']=='Deployment'}=={'home-gateway','keycloak'})
        require({o['metadata']['name'] for o in platform if o['kind']=='Deployment'}=={'okf-f2-kgateway'})
        require(gatewayclass['kind']=='GatewayClass' and gatewayclass['metadata']['name']=='kgateway'
            and gatewayclass['metadata']['uid']==value['oidc_gateway_class_uid']
            and gatewayclass['spec']['controllerName']=='kgateway.dev/kgateway')
        ready_conditions(gatewayclass,('Accepted',))
        gw=one(access,'Gateway','home-gateway');spec=gw['spec']
        require(spec['gatewayClassName']=='kgateway' and spec['infrastructure']['parametersRef']==dict(name='home-gateway',kind='GatewayParameters',group='gateway.kgateway.dev'))
        ready_conditions(gw,('Accepted','Programmed'))
        expected_namespaces={'from':'Selector','selector':{'matchLabels':{'okf-run-id':value['run_id'],'okf-purpose':'replica-test'}}}
        require(spec['allowedListeners']=={'namespaces':expected_namespaces} and len(spec['listeners'])==1)
        require(spec['listeners'][0]==dict(name='http',protocol='HTTP',port=80,allowedRoutes={'namespaces':expected_namespaces}))
        gp=one(access,'GatewayParameters','home-gateway')['spec']['kube'];require(gp['service']['type']=='ClusterIP')
        image=gp['envoyContainer']['image'];require(image.get('registry')=='cr.kgateway.dev/kgateway-dev'
            and image.get('repository')=='envoy-wrapper' and image.get('digest')==images['gateway'])
        proxy=workload(access,'home-gateway',images['gateway'],gateway=gw)
        keycloak=workload(access,'keycloak',images['keycloak'])
        controller=workload(platform,'okf-f2-kgateway',images['controller'],identity=value['oidc_controller_uid'])
        svc=internal_service(access,'home-gateway',[proxy]);internal_service(access,'keycloak',[keycloak])
        ports={}
        for name,number in [('http',80),('https',443)]:
            found=[p for p in svc['spec']['ports'] if p['port']==number];require(len(found)==1);target=found[0]['targetPort']
            if isinstance(target,str):
                candidates=[p['containerPort'] for c in proxy['spec']['containers'] for p in c.get('ports',[]) if p.get('name')==target]
                require(len(candidates)==1);target=candidates[0]
            require(type(target) is int and target in {80,443,8080,8443});ports[name]=target
        host='knowledge.'+an+'.svc.cluster.local';identity='identity.'+an+'.svc.cluster.local'
        for name in ('knowledge','identity'):
            alias=one(access,'Service',name)['spec'];require(alias.get('type')=='ExternalName' and alias.get('externalName')=='home-gateway.'+an+'.svc.cluster.local')
        listener(app,'frontend-listeners',host,an,ns);listener(access,'identity-listeners',identity,an,an)
        route(app,'frontend',host,'frontend-listeners','frontend',3000)
        route(access,'identity',identity,'identity-listeners','keycloak',8080)
        redirect_route(access,[host,identity])
        require({o['metadata']['name'] for o in access if o['kind']=='HTTPRoute'}=={'identity','https-redirect'})
        require({o['metadata']['name'] for o in app if o['kind']=='HTTPRoute'}=={'frontend','frontend-slot-first','frontend-slot-second'})
        for objects,expected in [(app,{'frontend-listeners'}),(access,{'identity-listeners'})]:
            require({o['metadata']['name'] for o in objects if o['kind']=='ListenerSet'}==expected)
        for kind in ('Gateway','GatewayParameters'):
            require(not any(o['kind']==kind for o in app))
            require({o['metadata']['name'] for o in access if o['kind']==kind}=={'home-gateway'})
        fronts=[p for p in app if p['kind']=='Pod' and p['metadata'].get('labels',{}).get('okf-role')=='frontend'];slots={}
        if slot_bindings is not None:
            require(set(slot_bindings)=={'first','second'} and slot_bindings['second'] is not None)
        for slot in ('first','second'):
            selected=[p for p in fronts if p['metadata']['labels'].get('okf-f2-probe-slot')==slot]
            expected=None if slot_bindings is None else slot_bindings[slot]
            require(len(selected)==(0 if slot_bindings is not None and expected is None else 1))
            if expected is not None:require(selected[0]['metadata']['uid']==expected)
            helper=internal_service(app,'frontend-slot-'+slot,selected)
            require(helper['spec']['selector']=={'okf-f2-probe-slot':slot}
                and [(p['port'],p['targetPort']) for p in helper['spec']['ports']]==[(3000,3000)]
                and all(p.get('protocol','TCP')=='TCP' and p.get('appProtocol') in {None,'http'} for p in helper['spec']['ports']))
            route(app,'frontend-slot-'+slot,host,'frontend-listeners','frontend-slot-'+slot,3000,slot)
            slots[slot]={k:selected[0]['metadata'][k] for k in ('name','uid')} if selected else None
        stable=[]
        for o in [*app,*access,*platform,gatewayclass]:
            if o['kind'] in {'ReplicaSet','EndpointSlice'} or (o['kind']=='Pod' and o['metadata'].get('labels',{}).get('okf-role')=='frontend'):continue
            meta=o['metadata'];stable.append(dict(kind=o['kind'],namespace=meta.get('namespace'),name=meta['name'],uid=meta['uid'],
                content_sha256=digest({k:o.get(k) for k in ('spec','data','binaryData','type','immutable')})))
        public=lambda p:{k:p['metadata'][k] for k in ('name','uid')}
        return dict(resources_sha256=digest(sorted(stable,key=lambda x:(x['kind'],x['namespace'] or '',x['name']))),
            gateway=public(proxy),keycloak=public(keycloak),controller=public(controller),ports=ports,slot_frontends=slots,
            origin='https://'+host,identity='https://'+identity)
    except (KeyError,TypeError,IndexError,AttributeError,StopIteration):
        raise ValueError('Malformed owned OIDC observation') from None


def validate_oidc_scope(value, app, access, platform, gatewayclass, *, slot_bindings=None):
    proof=validate_oidc_graph(value,app,access,platform,gatewayclass,slot_bindings=slot_bindings)
    for objects in (app,access):
        secret=one(objects,'Secret','lab-tls')
        require(secret.get('type')=='kubernetes.io/tls' and set(secret.get('data',{}))=={'tls.crt','tls.key'})
    return proof


def private_configuration(app,access,origin):
    """Admit a synthetic import document, never a live/customer realm or password input."""
    def decode(objects,name,key,keys):
        secret=one(objects,'Secret',name)
        require(secret.get('type')=='Opaque' and set(secret.get('data',{}))==keys)
        try:
            raw=base64.b64decode(secret['data'][key],validate=True)
            require(len(raw)<=65536)
            return raw
        except (ValueError,TypeError):raise ValueError('Invalid owned secret encoding') from None
    try:
        probe=json.loads(decode(app,'synthetic-probe','probe.json',{'probe.json','ca.crt'}))
        require(set(probe)<={'origin','passwords','client_type','token_auth_method','pkce_method','userinfo_source'}
            and probe.get('origin')==origin)
        for field,expected in [('client_type','confidential'),('token_auth_method','client_secret_basic'),('pkce_method','none'),('userinfo_source','auto')]:
            require(probe.get(field,expected)==expected)
        roles=('viewer','editor','admin','security','unmapped');passwords=probe['passwords']
        require(set(passwords)==set(roles) and all(isinstance(p,str) and 16<=len(p)<=256 for p in passwords.values()))
        realm=json.loads(decode(access,'synthetic-realm','realm.json',{'realm.json'}))
        secret=realm['clients'][0]['secret'];require(isinstance(secret,str) and 24<=len(secret)<=256)
        expected=dict(realm='okf-home',enabled=True,sslRequired='external',registrationAllowed=False,
            groups=[dict(name='lab',subGroups=[{'name':r} for r in roles if r!='unmapped'])],
            clients=[dict(clientId='knowledge',secret=secret,protocol='openid-connect',publicClient=False,
                standardFlowEnabled=True,directAccessGrantsEnabled=False,serviceAccountsEnabled=False,
                redirectUris=[origin+'/api/auth/callback'],webOrigins=[origin],attributes={'post.logout.redirect.uris':origin+'/'},
                protocolMappers=[dict(name='full-groups',protocol='openid-connect',protocolMapper='oidc-group-membership-mapper',
                    config={'claim.name':'group','full.path':'true','id.token.claim':'true','access.token.claim':'true','userinfo.token.claim':'true'})])],
            users=[dict(username='lab-'+role,enabled=True,emailVerified=True,email=role+'@example.test',firstName='Synthetic',lastName=role,
                groups=[] if role=='unmapped' else ['/lab/'+role],credentials=[dict(type='password',value=passwords[role],temporary=False)]) for role in roles])
        # Import user order is immaterial; all other import fields are the fixed recipe.
        require(len(realm['users'])==len(roles))
        realm['users']=sorted(realm['users'],key=lambda u:u['username'])
        expected['users']=sorted(expected['users'],key=lambda u:u['username'])
        require(realm==expected)
        ca=base64.b64decode(one(app,'Secret','synthetic-probe')['data']['ca.crt'],validate=True)
        require(0<len(ca)<=65536 and b'PRIVATE KEY' not in ca)
        return dict(password=passwords['admin'],ca=ca,client_secret_sha256=sha256(secret.encode()).hexdigest())
    except (KeyError,TypeError,IndexError,AttributeError,UnicodeError):
        raise ValueError('Invalid owned realm/probe configuration') from None


class OIDCCoordinator(Coordinator):
    RECIPE = RECIPE
    DYNAMIC_SCOPE = {'frontends','oidc_slot_frontends'}
    HANDLE_SLOTS = ('gw-https','gw-http','client')

    def __init__(self,directory,value,*,probe_factory=OIDCProbe,http_factory=GatewayFrontendHTTP,**kwargs):
        validate_oidc_inputs(value)
        super().__init__(directory,value,http_factory=http_factory,**kwargs)
        self.probe_factory=probe_factory

    def attest(self,request):
        require(request.get('stage')=='final' and all(self.files.path('native.'+s+'.result.json').exists() for s in STAGES))
        require(self.files.path('loss.delete-ack.json').exists() and self.files.path('oidc.replacement-label.ack.json').exists())
        result=self.dispatch(request,observe=True)
        require(result is not None and result['status']=='passed')
        return dict(recipe=self.RECIPE,**{k:request[k] for k in ('execution_id','code_sha256','binding_sha256')},
            capsule_sha256=digest(self.files.read('capsule.json')),
            receipts_sha256={s:digest(self.files.read('native.'+s+'.result.json')) for s in STAGES})

    def snapshot(self):
        v=self.value
        spaces=[(v['namespace'],v['namespace_uid'],False),
            (v['oidc_access_namespace'],v['oidc_access_namespace_uid'],False),
            (PLATFORM_NAMESPACE,v['oidc_platform_namespace_uid'],True)]
        groups=[]
        for name,identity,platform in spaces:
            ns=json.loads(self.kube.run('get','namespace',name,'-o','json'))
            namespace([ns],name,identity,v,platform=platform)
            groups.append([ns])
        resources=('deployments,statefulsets,replicasets,pods,services,endpointslices,configmaps,'
            'persistentvolumeclaims,networkpolicies,jobs,cronjobs,daemonsets,gateways.gateway.networking.k8s.io,'
            'listenersets.gateway.networking.k8s.io,httproutes.gateway.networking.k8s.io,gatewayparameters.gateway.kgateway.dev')
        for (name,_,_),objects in zip(spaces,groups):
            objects.extend(json.loads(self.kube.run('get',resources,'--namespace',name,'-o','json'))['items'])
        cls=json.loads(self.kube.run('get','gatewayclass','kgateway','-o','json'))
        # No Secret data is fetched until every namespace and route graph is admitted.
        bindings=self.slot_bindings(groups[0])
        validate_oidc_graph(v,*groups,cls,slot_bindings=bindings)
        for (name,_,_),objects in zip(spaces,groups):
            objects.extend(json.loads(self.kube.run('get','secrets','--namespace',name,'-o','json'))['items'])
        oidc=validate_oidc_scope(v,*groups,cls,slot_bindings=bindings)
        self._oidc_credentials=private_configuration(groups[0],groups[1],oidc['origin'])
        oidc['client_secret_sha256']=self._oidc_credentials['client_secret_sha256']
        result=super().snapshot(groups[0])
        result['oidc_slot_frontends']=oidc.pop('slot_frontends')
        result['oidc']=oidc
        result['oidc_nodes']=[]
        for name in sorted({o['spec']['nodeName'] for objects in groups for o in objects if o['kind']=='Pod'}):
            node=json.loads(self.kube.run('get','node',name,'-o','json'))
            result['oidc_nodes'].append(dict(name=name,uid=node['metadata']['uid']))
        return result

    def slot_bindings(self,app):
        if not self.files.path('scope.json').exists():return None
        original=self.files.read('scope.json');slots=original['oidc_slot_frontends']
        bindings={s:slots[s]['uid'] for s in ('first','second')}
        if self.files.path('loss.delete-intent.json').exists():
            intent=self.files.read('loss.delete-intent.json')
            require(intent['victim']==slots['first'] and intent['delete_options']['preconditions']=={'uid':slots['first']['uid']})
            labeled=[o for o in app if o['kind']=='Pod' and o['metadata'].get('labels',{}).get('okf-f2-probe-slot')=='first']
            if not labeled:bindings['first']=None
            elif labeled[0]['metadata']['uid']!=slots['first']['uid']:
                patch=self.files.read('oidc.replacement-label.intent.json')
                require({k:labeled[0]['metadata'][k] for k in ('name','uid')}==patch['target'])
                bindings['first']=patch['target']['uid']
        return bindings

    def guard(self):
        actual=self.snapshot()
        if (self.files.path('scope.json').exists() and self.files.path('loss.delete-intent.json').exists()
                and not self.files.path('loss.delete-ack.json').exists()):
            original=self.files.read('scope.json');intent=self.files.read('loss.delete-intent.json')
            baseline=self.files.read('loss.baseline.json');victim=original['oidc_slot_frontends']['first']
            require({k:v for k,v in actual.items() if k not in self.DYNAMIC_SCOPE}
                =={k:v for k,v in original.items() if k not in self.DYNAMIC_SCOPE})
            require(intent['victim']==baseline['victim']==victim and intent['delete_options']==dict(apiVersion='v1',kind='DeleteOptions',
                gracePeriodSeconds=2,preconditions={'uid':victim['uid']}))
            require(baseline['model']['active']==0 and baseline['model']['started']==baseline['model']['finished'])
            require(intent['gate']==dict(first_sources_frame=True,model=dict(started=baseline['model']['started']+1,
                finished=baseline['model']['finished'],active=1)))
            if not any(p['uid']==victim['uid'] for p in actual['frontends']):
                survivor=original['oidc_slot_frontends']['second'];require(survivor in actual['frontends'])
                old=self.kube.run('get','pod',victim['name'],'--ignore-not-found','-o','json')
                require(not old.strip())
                state=self.files.read('loss.progress.json')
                require(state.get('sources_seen') is True and state.get('result_seen') is False
                    and state.get('error_seen') is False and state.get('protocol_error') is False)
                self.files.once('loss.delete-ack.json',{**intent,'acknowledgement':'observed-victim-gone'})
        return self.check_scope(actual)

    def loss_victim(self):
        return self.files.read('scope.json')['oidc_slot_frontends']['first']

    def loss_client(self,victim):
        business=self.http();snapshot=self.files.read('scope.json')
        require(victim==snapshot['oidc_slot_frontends']['first'])
        capsule=self.files.read('capsule.json')
        for name in CHILD_MODULES:
            source=self.files.read('module-'+name.replace('_','-')+'.json')['source']
            require(isinstance(source,str) and sha256(source.encode()).hexdigest()==capsule[name])
        probe=business.probe
        return OIDC_LOSS_CLIENT,dict(directory=str(self.files.directory),origin=probe.origin,identity=probe.identity,port=probe.port,
            username=self.value['oidc_username'],execution_id=self.execution_id,capsule_sha256=digest(capsule))

    def active_progress(self,progress):
        return super().active_progress(progress) and progress.get('protocol_error') is False and progress.get('error_seen') is False

    def interrupted_progress(self,progress):
        return super().interrupted_progress(progress) and progress.get('protocol_error') is False and progress.get('error_seen') is False

    def replacement(self,snapshot,*,observe=False):
        original=self.files.read('scope.json')
        new=[p for p in snapshot['frontends'] if p not in original['frontends']]
        require(len(new)==1)
        target=new[0];intent_name='oidc.replacement-label.intent.json'
        exists=self.files.path(intent_name).exists()
        if exists:
            intent=self.files.read(intent_name);require(intent['target']==target)
        else:
            if observe:return None
            pod=json.loads(self.kube.run('get','pod',target['name'],'-o','json'))
            require(pod['metadata']['uid']==target['uid'] and not pod['metadata'].get('deletionTimestamp')
                and 'okf-f2-probe-slot' not in pod['metadata'].get('labels',{}))
            rv=pod['metadata']['resourceVersion'];require(isinstance(rv,str) and rv.isdigit())
            patch=[dict(op='test',path='/metadata/uid',value=target['uid']),
                dict(op='test',path='/metadata/resourceVersion',value=rv),
                dict(op='add',path='/metadata/labels/okf-f2-probe-slot',value='first')]
            intent=dict(target=target,patch=patch)
            self.files.once(intent_name,intent)
            self.kube.run('patch','pod',target['name'],'--type=json','-p',json.dumps(patch),'-o','json')
        # A lost patch ACK is adopted only from actual UID/slot/EndpointSlice observations.
        actual=self.guard()
        if actual['oidc_slot_frontends']['first']!=target:
            if exists:return None
            raise ValueError('Replacement slot has not converged; observe the same label intent')
        if not self.files.path('oidc.replacement-label.ack.json').exists():self.files.once('oidc.replacement-label.ack.json',intent)
        business=self.http();business.shared_session()
        require(business.publish().get('status')=='passed')
        export=business.export_observe();require(export.get('status')=='passed')
        proof=dict(replacement=target,session_survived=True,publication_survived=True,export_sha256=export['export_sha256'])
        self.files.once('replacement.result.json',proof)
        return proof

    def recover_stage(self,stage,snapshot):
        result=super().recover_stage(stage,snapshot)
        if result is None and stage=='replacement' and self.files.path('oidc.replacement-label.intent.json').exists():
            return self.replacement(snapshot,observe=True)
        return result

    def _public_ca(self):
        body=self._oidc_credentials['ca'];path=self.files.path('oidc-ca.pem')
        if path.exists():
            require(path.read_bytes()==body)
        else:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream:
                stream.write(body);stream.flush();os.fsync(stream.fileno())
            self.files.sync()

    def http(self):
        snapshot=self.guard();route=self.processes.route('gw-https')
        parsed=urlsplit(route['url'])
        require(route['pod']==snapshot['oidc']['gateway'] and route.get('namespace')==self.value['oidc_access_namespace']
            and route.get('slot')=='gw-https' and parsed.scheme=='https' and parsed.hostname=='127.0.0.1'
            and parsed.port and not any((parsed.path,parsed.query,parsed.fragment,parsed.username,parsed.password)))
        self._public_ca()
        oidc=snapshot['oidc']
        probe=self.probe_factory(self.files.directory,oidc['origin'],oidc['identity'],parsed.port,
            self.value['oidc_username'],'admin',self.execution_id)
        return self.http_factory(probe)

    @staticmethod
    def anonymous_redirect(route,origin):
        parsed=urlsplit(route['url'])
        require(parsed.scheme=='http' and parsed.hostname=='127.0.0.1' and parsed.port
            and not any((parsed.path,parsed.query,parsed.fragment,parsed.username,parsed.password)))
        connection=http.client.HTTPConnection('127.0.0.1',parsed.port,timeout=20)
        try:
            connection.request('GET','/',headers={'Host':urlsplit(origin).netloc,'Connection':'close'})
            response=connection.getresponse();body=response.read(65537)
            require(response.status in {301,302,307,308} and response.getheader('Location')==origin+'/' and len(body)<=65536)
        finally:connection.close()

    def session(self,snapshot):
        # Only the two proxy sockets are opened. Neither frontend nor backend is forwarded.
        for slot,key,tls in [('gw-https','https',True),('gw-http','http',False)]:
            self.processes.forward_gateway(slot,snapshot['oidc']['gateway'],snapshot['oidc']['ports'][key],tls=tls)
        route=self.processes.route('gw-http')
        require(route['pod']==snapshot['oidc']['gateway'] and route.get('namespace')==self.value['oidc_access_namespace'])
        self.anonymous_redirect(route,snapshot['oidc']['origin'])
        business=self.http();business.probe.reject_unknown_issuer()
        for path in ('/internal/diagnostics/control','/api/internal/diagnostics/control'):
            status,_,_=business.probe._request('GET',business.probe.origin+path,jar=CookieJar())
            require(status==404)
        return business.session(self._oidc_credentials['password'])

    def auth_proof_source(self,snapshot):
        oidc=snapshot['oidc']
        expected=dict(origin=oidc['origin'],identity=oidc['identity'],
            client_secret_sha256=oidc['client_secret_sha256'],
            groups={'/lab/'+r:r for r in ('viewer','editor','admin','security')})
        return '''import hashlib
oidc=json.loads(OIDC_EXPECTED)
assert s.auth_provider=='keycloak_oidc' and s.auth_session_https_only is True
assert s.keycloak_url==oidc['identity'] and s.keycloak_realm=='okf-home' and s.keycloak_client_id=='knowledge'
assert s.sso_redirect_uri==oidc['origin']+'/api/auth/callback' and s.sso_post_logout_redirect_uri==oidc['origin']+'/'
assert s.keycloak_group_path_mode=='full_path' and s.auth_default_role is None
assert s.auth_role_groups==oidc['groups'] and not s.keycloak_field_mapping
assert isinstance(s.keycloak_client_secret,str) and hashlib.sha256(s.keycloak_client_secret.encode()).hexdigest()==oidc['client_secret_sha256']
'''.replace('OIDC_EXPECTED',repr(json.dumps(expected)))
