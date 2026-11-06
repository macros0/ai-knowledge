"""Native OIDC admission: only own namespaces, immutable images and exact routes."""
import base64
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

from tests.test_replica_frontend_native import objects as app_objects, value as simulation_value
from tests.test_replica_frontend_acceptance import uid


def module():
    name='test_scripts.replica_frontend_oidc_native'
    assert importlib.util.find_spec(name) is not None, 'Native real-OIDC coordinator missing'
    return importlib.import_module(name)


def value():
    return {**simulation_value(),'functional_recipe':'owned-keycloak-local-v1',
        'oidc_access_namespace':'okf-f2-abcdefgh','oidc_access_namespace_uid':uid('access'),
        'oidc_platform_namespace_uid':uid('platform'),'oidc_controller_uid':uid('controller'),
        'oidc_gateway_class_uid':uid('gatewayclass'),'oidc_username':'lab-admin',
        'oidc_expected_images':{k:'sha256:'+n*64 for k,n in [('gateway','5'),('keycloak','6'),('controller','7')]}}


def labels():
    return {'okf-purpose':'replica-test','okf-run-id':'abcdefgh','okf-installation-id':'3'*32,
        'app.kubernetes.io/managed-by':'okf-replica-acceptance'}


def obj(kind,name,namespace=None,**extra):
    meta=dict(name=name,uid=uid((namespace or '')+name),generation=1,labels=labels())
    if namespace:meta['namespace']=namespace
    return dict(kind=kind,metadata=meta,**extra)


def conditions(*names):
    return [{'type':n,'status':'True','observedGeneration':1} for n in names]


def workload(namespace,name,image,*,gateway_uid=None,identity=None):
    selector={'app':name}
    dep=obj('Deployment',name,namespace,spec=dict(replicas=1,selector={'matchLabels':selector},
        template={'metadata':{'labels':selector},'spec':{'containers':[{'name':name,'image':'pinned-'+name}]}}),
        status=dict(observedGeneration=1,readyReplicas=1,availableReplicas=1,updatedReplicas=1))
    if identity:dep['metadata']['uid']=identity
    if gateway_uid:dep['metadata']['ownerReferences']=[dict(kind='Gateway',name='home-gateway',uid=gateway_uid)]
    rs=obj('ReplicaSet',name+'-rs',namespace)
    rs['metadata']['ownerReferences']=[dict(kind='Deployment',name=name,uid=dep['metadata']['uid'])]
    pod=obj('Pod',name+'-0',namespace,spec=dict(nodeName='owned-node',containers=[dict(name=name,image='pinned-'+name,
        ports=[dict(name='http',containerPort=8080),dict(name='https',containerPort=8443)])]),
        status=dict(phase='Running',podIP='10.2.0.'+('10' if name=='home-gateway' else '11'),
                    containerStatuses=[dict(name=name,ready=True,restartCount=0,imageID=image)]))
    pod['metadata']['labels'].update(selector);pod['metadata']['ownerReferences']=[dict(kind='ReplicaSet',name=name+'-rs',uid=rs['metadata']['uid'])]
    return [dep,rs,pod]


def service(namespace,name,selector,ports,pods,*,gateway_uid=None):
    svc=obj('Service',name,namespace,spec=dict(type='ClusterIP',selector=selector,ports=ports))
    if gateway_uid:svc['metadata']['ownerReferences']=[dict(kind='Gateway',name='home-gateway',uid=gateway_uid)]
    ep=obj('EndpointSlice',name+'-slice',namespace,endpoints=[dict(addresses=[p['status']['podIP']],conditions={'ready':True},
        targetRef=dict(kind='Pod',namespace=namespace,name=p['metadata']['name'],uid=p['metadata']['uid'])) for p in pods])
    ep['metadata']['labels']['kubernetes.io/service-name']=name
    ep['metadata']['ownerReferences']=[dict(kind='Service',name=name,uid=svc['metadata']['uid'])]
    return [svc,ep]


def listener(namespace,access,name,host):
    item=obj('ListenerSet',name,namespace,spec=dict(parentRef=dict(name='home-gateway',namespace=access,kind='Gateway',group='gateway.networking.k8s.io'),
        listeners=[dict(name='https',hostname=host,port=443,protocol='HTTPS',tls=dict(mode='Terminate',certificateRefs=[{'name':'lab-tls'}]),
                        allowedRoutes={'namespaces':{'from':'Same'}})]))
    item['status']=actual_listener_status(item)
    return item


def route(namespace,name,host,parent,backend,port,slot=None):
    match={'path':{'type':'PathPrefix','value':'/'}}
    if slot:match['headers']=[dict(type='Exact',name='X-Okf-Probe-Slot',value=slot)]
    spec=dict(parentRefs=[dict(name=parent,kind='ListenerSet',group='gateway.networking.k8s.io')],hostnames=[host],
        rules=[dict(matches=[match],timeouts={'request':'300s','backendRequest':'300s'},backendRefs=[dict(name=backend,port=port)])])
    return obj('HTTPRoute',name,namespace,spec=spec,status={'parents':[dict(parentRef=spec['parentRefs'][0],controllerName='kgateway.dev/kgateway',
        conditions=conditions('Accepted','ResolvedRefs'))]})


def world():
    v=value();app=app_objects();ns=v['namespace'];access=v['oidc_access_namespace'];platform='okf-f2-gateway-system'
    app[0]['metadata']['labels']['okf-f2-gateway']='true'
    for o in app[1:]:o['metadata']['namespace']=ns
    fronts=sorted([p for p in app if p['kind']=='Pod' and p['metadata'].get('labels',{}).get('okf-role')=='frontend'],key=lambda p:p['metadata']['uid'])
    for i,p in enumerate(fronts):p['status']['podIP']='10.1.0.'+str(i+1)
    host='knowledge.'+access+'.svc.cluster.local';identity='identity.'+access+'.svc.cluster.local'
    app.append(listener(ns,access,'frontend-listeners',host))
    app.append(route(ns,'frontend',host,'frontend-listeners','frontend',3000))
    for slot,pod in zip(['first','second'],fronts):
        pod['metadata']['labels']['okf-f2-probe-slot']=slot
        app+=service(ns,'frontend-slot-'+slot,{'okf-f2-probe-slot':slot},[dict(port=3000,targetPort=3000)],[pod])
        app.append(route(ns,'frontend-slot-'+slot,host,'frontend-listeners','frontend-slot-'+slot,3000,slot))
    gateway=obj('Gateway','home-gateway',access,spec=dict(gatewayClassName='kgateway',
        infrastructure={'parametersRef':dict(name='home-gateway',kind='GatewayParameters',group='gateway.kgateway.dev')},
        allowedListeners={'namespaces':{'from':'Selector','selector':{'matchLabels':{'okf-run-id':'abcdefgh','okf-purpose':'replica-test'}}}},
        listeners=[dict(name='http',protocol='HTTP',port=80,allowedRoutes={'namespaces':{'from':'Selector',
            'selector':{'matchLabels':{'okf-run-id':'abcdefgh','okf-purpose':'replica-test'}}}})]),status={'conditions':conditions('Accepted','Programmed')})
    access_ns=obj('Namespace',access);access_ns['metadata']['uid']=v['oidc_access_namespace_uid']
    access_ns['metadata']['labels']['okf-f2-gateway']='true'
    a=[access_ns,gateway,obj('GatewayParameters','home-gateway',access,spec={'kube':{'service':{'type':'ClusterIP'},
        'envoyContainer':{'image':dict(registry='cr.kgateway.dev/kgateway-dev',repository='envoy-wrapper',digest=v['oidc_expected_images']['gateway'])}}})]
    g=workload(access,'home-gateway',v['oidc_expected_images']['gateway'],gateway_uid=gateway['metadata']['uid']);a+=g
    kc=workload(access,'keycloak',v['oidc_expected_images']['keycloak']);a+=kc
    a+=service(access,'home-gateway',{'app':'home-gateway'},[dict(port=80,targetPort='http'),dict(port=443,targetPort='https')],[g[-1]],gateway_uid=gateway['metadata']['uid'])
    a+=service(access,'keycloak',{'app':'keycloak'},[dict(port=8080,targetPort=8080)],[kc[-1]])
    a+=[listener(access,access,'identity-listeners',identity),route(access,'identity',identity,'identity-listeners','keycloak',8080)]
    redirect=obj('HTTPRoute','https-redirect',access,spec=dict(parentRefs=[dict(group='gateway.networking.k8s.io',kind='Gateway',name='home-gateway',sectionName='http')],
        hostnames=[host,identity],rules=[dict(matches=[{'path':dict(type='PathPrefix',value='/')}],
            filters=[dict(type='RequestRedirect',requestRedirect=dict(scheme='https',statusCode=308))])]))
    redirect['status']={'parents':[dict(parentRef=redirect['spec']['parentRefs'][0],controllerName='kgateway.dev/kgateway',conditions=conditions('Accepted','ResolvedRefs'))]}
    a.append(redirect)
    for name in ['knowledge','identity']:a.append(obj('Service',name,access,spec=dict(type='ExternalName',externalName='home-gateway.'+access+'.svc.cluster.local')))
    for space,destination in [(ns,app),(access,a)]:destination.append(obj('Secret','lab-tls',space,type='kubernetes.io/tls',data={'tls.crt':'Q0E=','tls.key':'UFJJVkFURV9DQU5BUlk='}))
    probe={'origin':'https://'+host,'passwords':{r:'SYNTHETIC_PRIVATE_'+r for r in ['viewer','editor','admin','security','unmapped']}}
    app.append(obj('Secret','synthetic-probe',ns,type='Opaque',data={'probe.json':base64.b64encode(json.dumps(probe).encode()).decode(),'ca.crt':'Q0E='}))
    path=Path(__file__).resolve().parents[2]/'scripts/kubernetes/home_access.py'
    spec=importlib.util.spec_from_file_location('oidc_native_fixture_home',path)
    home=importlib.util.module_from_spec(spec);spec.loader.exec_module(home)
    realm=home.realm_document(probe['origin'],'SYNTHETIC_PRIVATE_CLIENT_SECRET_123',probe['passwords'])
    a.append(obj('Secret','synthetic-realm',access,type='Opaque',data={'realm.json':base64.b64encode(json.dumps(realm).encode()).decode()}))
    pn=obj('Namespace',platform);pn['metadata']['uid']=v['oidc_platform_namespace_uid'];pn['metadata']['labels']={'okf-purpose':'replica-test','okf-f2-platform':'a'*32}
    operators=[pn,*workload(platform,'okf-f2-kgateway',v['oidc_expected_images']['controller'],identity=v['oidc_controller_uid'])]
    c=obj('GatewayClass','kgateway',spec={'controllerName':'kgateway.dev/kgateway'},status={'conditions':conditions('Accepted')});c['metadata']['uid']=v['oidc_gateway_class_uid']
    return v,app,a,operators,c


def validate(parts):return module().validate_oidc_scope(*parts)


def test_oidc_scope_binds_owned_gateway_idp_routes_and_no_private_values():
    parts=world();proof=validate(parts)
    assert proof['gateway']['uid']==next(o['metadata']['uid'] for o in parts[2] if o['kind']=='Pod' and o['metadata']['name']=='home-gateway-0')
    assert proof['ports']=={'http':8080,'https':8443}
    assert set(proof['slot_frontends'])=={'first','second'}
    assert len(proof['resources_sha256'])==64 and 'PRIVATE' not in json.dumps(proof)


@pytest.mark.parametrize('fault',['access_uid','platform_uid','controller_uid','gatewayclass_uid','gateway_image','keycloak_image',
    'gateway_owner','foreign_endpoint','foreign_address','external_alias','load_balancer','route_backend','unresolved_route','extra_route'])
def test_wrong_oidc_scope_is_refused_before_a_password_can_be_selected(fault):
    parts=world();v,app,a,p,c=parts
    if fault=='access_uid':a[0]['metadata']['uid']=uid('foreign')
    elif fault=='platform_uid':p[0]['metadata']['uid']=uid('foreign')
    elif fault=='controller_uid':next(o for o in p if o['kind']=='Deployment')['metadata']['uid']=uid('foreign')
    elif fault=='gatewayclass_uid':c['metadata']['uid']=uid('foreign')
    elif fault in ['gateway_image','keycloak_image']:
        name='home-gateway-0' if fault=='gateway_image' else 'keycloak-0'
        next(o for o in a if o['kind']=='Pod' and o['metadata']['name']==name)['status']['containerStatuses'][0]['imageID']='sha256:'+'f'*64
    elif fault=='gateway_owner':next(o for o in a if o['kind']=='Deployment' and o['metadata']['name']=='home-gateway')['metadata']['ownerReferences'][0]['uid']=uid('foreign')
    elif fault in ['foreign_endpoint','foreign_address']:
        ep=next(o for o in app if o['kind']=='EndpointSlice' and o['metadata']['labels'].get('kubernetes.io/service-name')=='frontend-slot-first')
        if fault=='foreign_endpoint':ep['endpoints'][0]['targetRef']['uid']=uid('foreign')
        else:ep['endpoints'][0]['addresses']=['203.0.113.8']
    elif fault=='external_alias':next(o for o in a if o['kind']=='Service' and o['metadata']['name']=='identity')['spec']['externalName']='customer.invalid'
    elif fault=='load_balancer':next(o for o in a if o['kind']=='Service' and o['metadata']['name']=='home-gateway')['spec']['type']='LoadBalancer'
    elif fault=='route_backend':next(o for o in app if o['kind']=='HTTPRoute' and o['metadata']['name']=='frontend')['spec']['rules'][0]['backendRefs'][0]['name']='backend'
    elif fault=='unresolved_route':next(o for o in a if o['kind']=='HTTPRoute')['status']['parents'][0]['conditions'][1]['status']='False'
    elif fault=='extra_route':a.append(route(v['oidc_access_namespace'],'foreign','customer.invalid','identity-listeners','keycloak',8080))
    with pytest.raises(ValueError):validate(parts)


class NativeKube:
    def __init__(self):
        from tests.test_replica_frontend_native import Kube
        self.base=Kube();self.parts=world();self.calls=[];self.sources=[]

    def run(self,*args,source=None):
        self.calls.append(args)
        v,app,access,platform,cls=self.parts
        namespace=args[args.index('--namespace')+1] if '--namespace' in args else v['namespace']
        items={v['namespace']:app,v['oidc_access_namespace']:access,'okf-f2-gateway-system':platform}[namespace]
        if args[:2]==('get','namespace'):
            return json.dumps(next(p[0] for p in [app,access,platform] if p[0]['metadata']['name']==args[2]))
        if args[:2]==('get','gatewayclass'):return json.dumps(cls)
        if args[1] in {'pv','node'}:return self.base.run(*args,source=source)
        kinds=set(args[1].split(','))
        secrets=kinds=={'secrets'}
        return json.dumps({'items':[o for o in items[1:] if (o['kind']=='Secret')==secrets]})

    def python(self,pod,source):
        self.sources.append(source)
        return {'fresh':True,'model':dict(started=0,finished=0,active=0)}


def native(tmp_path):
    kube=NativeKube()
    cls=getattr(module(),'OIDCCoordinator',None)
    assert cls is not None,'Explicit native OIDC coordinator missing'
    return cls(tmp_path,value(),kube=kube),kube


def test_native_preflight_uses_real_recipe_and_binds_private_scope(tmp_path):
    from tests.test_replica_frontend_native import envelope
    current,kube=native(tmp_path)
    result=current.dispatch(envelope())
    assert result['status']=='passed'
    assert 'PRIVATE' not in json.dumps(result)+(tmp_path/'scope.json').read_text()
    assert len(kube.sources)==1 and "s.auth_provider=='keycloak_oidc'" in kube.sources[0]
    assert "s.auth_provider=='simulation'" not in kube.sources[0]


@pytest.mark.parametrize('scope',[1,2,3])
def test_native_checks_all_namespace_uids_before_secret_reads(scope,tmp_path):
    from tests.test_replica_frontend_native import envelope
    current,kube=native(tmp_path)
    kube.parts[scope][0]['metadata']['uid']=uid('foreign')
    with pytest.raises(ValueError):current.dispatch(envelope())
    assert not any(call[1]=='secrets' or 'secrets' in call[1].split(',') for call in kube.calls)
    assert not kube.sources


def test_native_checks_gateway_graph_before_secret_reads(tmp_path):
    from tests.test_replica_frontend_native import envelope
    current,kube=native(tmp_path)
    next(o for o in kube.parts[2] if o['kind']=='Service' and o['metadata']['name']=='identity')['spec']['externalName']='foreign.invalid'
    with pytest.raises(ValueError):current.dispatch(envelope())
    assert not any('secrets' in call[1].split(',') for call in kube.calls)


def test_simulation_coordinator_never_accepts_oidc_recipe(tmp_path):
    from test_scripts.replica_frontend_native import Coordinator
    with pytest.raises(ValueError):Coordinator(tmp_path,value())


@pytest.mark.parametrize('fault',['external_idp','federation','public_client','extra_client','redirect','pkce','group_mapper','extra_user','password','probe_origin','client_secret'])
def test_private_realm_and_probe_are_fixed_before_backend_or_login(fault,tmp_path):
    from tests.test_replica_frontend_native import envelope
    current,kube=native(tmp_path)
    probe=next(o for o in kube.parts[1] if o['metadata']['name']=='synthetic-probe')
    secret=next(o for o in kube.parts[2] if o['metadata']['name']=='synthetic-realm')
    realm=json.loads(base64.b64decode(secret['data']['realm.json']))
    if fault=='external_idp':realm['identityProviders']=[{'alias':'customer'}]
    elif fault=='federation':realm['userFederationProviders']=[{'providerName':'ldap'}]
    elif fault=='public_client':realm['clients'][0]['publicClient']=True
    elif fault=='extra_client':realm['clients'].append(dict(realm['clients'][0],clientId='foreign'))
    elif fault=='redirect':realm['clients'][0]['redirectUris']=['https://foreign.invalid']
    elif fault=='pkce':realm['clients'][0]['attributes']['pkce.code.challenge.method']='S256'
    elif fault=='group_mapper':realm['clients'][0]['protocolMappers'][0]['config']['full.path']='false'
    elif fault=='extra_user':realm['users'].append(dict(realm['users'][0],username='foreign'))
    elif fault=='password':realm['users'][0]['credentials'][0]['value']='foreign'
    elif fault=='probe_origin':
        body=json.loads(base64.b64decode(probe['data']['probe.json']));body['origin']='https://foreign.invalid'
        probe['data']['probe.json']=base64.b64encode(json.dumps(body).encode()).decode()
    elif fault=='client_secret':realm['clients'][0]['secret']=''
    secret['data']['realm.json']=base64.b64encode(json.dumps(realm).encode()).decode()
    with pytest.raises(ValueError):current.dispatch(envelope())
    assert not kube.sources


def test_backend_proof_rejects_wrong_client_secret_before_sql(tmp_path):
    from types import SimpleNamespace
    current,_=native(tmp_path)
    current.snapshot()
    source=current.auth_proof_source(current.snapshot())
    settings=SimpleNamespace(auth_provider='keycloak_oidc',auth_session_https_only=True,
        keycloak_url='https://identity.okf-f2-abcdefgh.svc.cluster.local',keycloak_realm='okf-home',keycloak_client_id='knowledge',
        sso_redirect_uri='https://knowledge.okf-f2-abcdefgh.svc.cluster.local/api/auth/callback',
        sso_post_logout_redirect_uri='https://knowledge.okf-f2-abcdefgh.svc.cluster.local/',
        keycloak_group_path_mode='full_path',auth_default_role=None,auth_role_groups={r:r for r in []},
        keycloak_field_mapping=None,keycloak_client_secret='WRONG')
    settings.auth_role_groups={'/lab/'+r:r for r in ['viewer','editor','admin','security']}
    with pytest.raises(AssertionError):exec(source,{'s':settings,'json':json})
    settings.keycloak_client_secret='SYNTHETIC_PRIVATE_CLIENT_SECRET_123'
    exec(source,{'s':settings,'json':json})


def _assert_only_explicit_lost_first_slot_may_have_no_endpoint(empty_endpoints):
    parts=world();v,app,_,_,_=parts
    fronts={o['metadata']['labels']['okf-f2-probe-slot']:o for o in app if o['kind']=='Pod' and 'okf-f2-probe-slot' in o['metadata']['labels']}
    first=fronts['first'];del first['metadata']['labels']['okf-f2-probe-slot']
    ep=next(o for o in app if o['kind']=='EndpointSlice' and o['metadata']['labels'].get('kubernetes.io/service-name')=='frontend-slot-first')
    ep['endpoints']=empty_endpoints
    with pytest.raises(ValueError):validate(parts)
    observed=module().validate_oidc_scope(*parts,slot_bindings={'first':None,'second':fronts['second']['metadata']['uid']})
    assert observed['slot_frontends']['first'] is None


def test_only_explicit_lost_first_slot_may_temporarily_have_no_endpoint():
    _assert_only_explicit_lost_first_slot_may_have_no_endpoint([])


def test_null_slice_is_admitted_only_for_explicit_lost_first_slot():
    _assert_only_explicit_lost_first_slot_may_have_no_endpoint(None)


def test_null_endpoint_slice_cannot_hide_an_expected_live_frontend():
    parts=world()
    ep=next(o for o in parts[1] if o['kind']=='EndpointSlice' and o['metadata']['labels'].get('kubernetes.io/service-name')=='frontend-slot-first')
    ep['endpoints']=None
    with pytest.raises(ValueError):validate(parts)


def test_explicit_slot_binding_rejects_an_unjournaled_replacement():
    parts=world();app=parts[1]
    slots={o['metadata']['labels']['okf-f2-probe-slot']:o['metadata']['uid'] for o in app if o['kind']=='Pod' and 'okf-f2-probe-slot' in o['metadata']['labels']}
    slots['first']=uid('foreign-replacement')
    with pytest.raises(ValueError):module().validate_oidc_scope(*parts,slot_bindings=slots)


def test_gateway_node_recreation_invalidates_even_cached_preflight(tmp_path):
    from tests.test_replica_frontend_native import envelope
    current,kube=native(tmp_path);run=kube.run;kube.gateway_node_uid=uid('gateway-node')
    for pod in kube.parts[2]:
        if pod['kind']=='Pod':pod['spec']['nodeName']='gateway-node'
    def nodes(*args,**kwargs):
        if args[:3]==('get','node','gateway-node'):return json.dumps({'metadata':{'uid':kube.gateway_node_uid}})
        return run(*args,**kwargs)
    kube.run=nodes;request=envelope();current.dispatch(request)
    kube.gateway_node_uid=uid('recreated-node')
    with pytest.raises(ValueError):current.dispatch(request,observe=True)


@pytest.mark.parametrize('fault',['absent','foreign_hostname','backend','redirect_host','unresolved'])
def test_anonymous_http_redirect_is_admitted_before_credentials(fault):
    parts=world();redirect=next(o for o in parts[2] if o['kind']=='HTTPRoute' and o['metadata']['name']=='https-redirect')
    if fault=='absent':parts[2].remove(redirect)
    elif fault=='foreign_hostname':redirect['spec']['hostnames'].append('foreign.invalid')
    elif fault=='backend':redirect['spec']['rules'][0]['backendRefs']=[dict(name='keycloak',port=8080)]
    elif fault=='redirect_host':redirect['spec']['rules'][0]['filters'][0]['requestRedirect']['hostname']='foreign.invalid'
    else:redirect['status']['parents'][0]['conditions'][0]['status']='False'
    with pytest.raises(ValueError):validate(parts)


def actual_listener_status(item):
    return dict(conditions=conditions('Accepted','Programmed'),listeners=[dict(name=item['spec']['listeners'][0]['name'],
        conditions=[*conditions('Accepted','ResolvedRefs','Programmed'),dict(type='Conflicted',status='False',observedGeneration=1)],
        supportedKinds=[dict(group='gateway.networking.k8s.io',kind='HTTPRoute')])])


def test_real_listener_set_status_resolves_references_on_its_named_listener():
    parts=world()
    for objects in parts[1:3]:
        for item in objects:
            if item['kind']=='ListenerSet':item['status']=actual_listener_status(item)
    assert validate(parts)['gateway']


@pytest.mark.parametrize('fault',['missing_listener','refs','conflict','stale','name','kind'])
def test_listener_level_reference_or_conflict_failure_is_not_hidden_by_top_level_ready(fault):
    parts=world();item=next(o for o in parts[2] if o['kind']=='ListenerSet');item['status']=actual_listener_status(item)
    # A legacy/top-level ResolvedRefs cannot substitute for the named listener proof.
    item['status']['conditions'].extend(conditions('ResolvedRefs'))
    row=item['status']['listeners'][0]
    if fault=='missing_listener':item['status']['listeners']=[]
    elif fault=='refs':next(c for c in row['conditions'] if c['type']=='ResolvedRefs')['status']='False'
    elif fault=='conflict':next(c for c in row['conditions'] if c['type']=='Conflicted')['status']='True'
    elif fault=='stale':next(c for c in row['conditions'] if c['type']=='ResolvedRefs')['observedGeneration']=0
    elif fault=='name':row['name']='foreign'
    else:row['supportedKinds']=[dict(group='gateway.networking.k8s.io',kind='GRPCRoute')]
    with pytest.raises(ValueError):validate(parts)


def test_secret_and_listener_changes_invalidate_the_observed_oidc_binding():
    parts=world();before=validate(parts)['resources_sha256']
    next(o for o in parts[2] if o['kind']=='Secret')['data']['tls.key']='TkVXX1BSSVZBVEU='
    assert validate(parts)['resources_sha256']!=before


def test_wrong_recipe_cannot_select_real_oidc_with_simulation_inputs():
    parts=world();parts[0]['functional_recipe']='owned-simulation-local-v1'
    with pytest.raises(ValueError):validate(parts)


def test_observed_kubernetes_default_fields_do_not_change_route_meaning():
    parts=world()
    for items in parts[1:3]:
        for o in items:
            if o['kind']=='HTTPRoute':
                for ref in o['spec']['parentRefs']:ref['namespace']=o['metadata']['namespace']
                for parent in o['status']['parents']:parent['parentRef']['namespace']=o['metadata']['namespace']
                for rule in o['spec']['rules']:
                    for backend in rule.get('backendRefs',[]):backend.update(group='',kind='Service',weight=1)
            elif o['kind']=='Service' and o['metadata']['name'].startswith('frontend-slot-'):
                o['spec']['ports'][0].update(name='http',protocol='TCP')
    assert validate(parts)['slot_frontends']['first']


@pytest.mark.parametrize('fault',['app_discovery','access_discovery','multiple_gateway_owners'])
def test_scope_rejects_unwatched_namespace_or_ambiguous_gateway_owner(fault):
    parts=world()
    if fault=='app_discovery':parts[1][0]['metadata']['labels']['okf-f2-gateway']='false'
    elif fault=='access_discovery':parts[2][0]['metadata']['labels']['okf-f2-gateway']='false'
    else:next(o for o in parts[2] if o['kind']=='Deployment' and o['metadata']['name']=='home-gateway')['metadata']['ownerReferences'].append(dict(kind='Gateway',uid=uid('foreign')))
    with pytest.raises(ValueError):validate(parts)
