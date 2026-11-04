"""Owned Linux frontend coordinator; native actions never accept caller receipts.

The fixed transport selects a protected directory and sends only recipe inputs
and the parent's intent tuple. No credentials, shell program, native path or
passed evidence is an input to this protocol.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
import time

from test_scripts.replica_frontend_acceptance import validate_frontend_inventory
from test_scripts.replica_frontend_http import PrivateFiles, FrontendHTTP
from test_scripts.replica_frontend_process import OwnedProcesses
from test_scripts.replica_runtime_acceptance import Kubernetes, role_pods, validate_live_inputs

STAGES = ('preflight', 'session', 'upload', 'publish', 'export', 'export-observe',
          'stream', 'loss', 'loss-observe', 'replacement', 'logout', 'final')
IDENTITY = ('execution_id', 'code_sha256', 'binding_sha256')
READ_ONLY = {'preflight', 'publish', 'export-observe', 'loss-observe'}

LOSS_CLIENT = r'''import json,os,pathlib,sys,time,urllib.request
from http.cookiejar import Cookie,CookieJar
from uuid import uuid4
p=json.loads(pathlib.Path(sys.argv[1]).read_text());d=pathlib.Path(p['directory'])
state=dict(terminal=False,sources_seen=False,result_seen=False,phase='starting',event_count=0)
def save():
 name=d/('progress-'+uuid4().hex+'.pending');fd=os.open(name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as out:json.dump(state,out);out.flush();os.fsync(out.fileno())
 os.replace(name,d/'loss.progress.json');fd=os.open(d,os.O_RDONLY|os.O_DIRECTORY)
 try:os.fsync(fd)
 finally:os.close(fd)
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise ValueError('redirect')
jar=CookieJar()
for item in json.loads((d/'cookies.json').read_text()):jar.set_cookie(Cookie(**item))
client=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPCookieProcessor(jar))
save()
try:
 req=urllib.request.Request(p['url']+'/api/chat/stream',data=json.dumps(dict(query='Aquila frontend continuity',mode='bm25',use_glossary=False)).encode(),headers={'Content-Type':'application/json'})
 with client.open(req,timeout=40) as response:
  assert response.status==200 and response.headers['Content-Type'].startswith('application/x-ndjson')
  assert response.headers['Cache-Control']=='no-store' and response.headers['X-Accel-Buffering']=='no'
  size=0;start=time.monotonic()
  while True:
   line=response.readline(1048577)
   if not line:break
   size+=len(line);assert size<=1048576 and state['event_count']<512 and time.monotonic()-start<45
   if not line.strip():continue
   kind=json.loads(line)['type'];assert kind in {'sources','ping','delta','result','error'}
   state['event_count']+=1;state['sources_seen']|=kind=='sources';state['result_seen']|=kind=='result'
   assert kind!='error';state['phase']='streaming';save()
 state['phase']='eof'
except BaseException:state['phase']='interrupted'
state['terminal']=True;save()
'''


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_request(request):
    if (not isinstance(request, dict) or set(request) != {*IDENTITY, 'stage', 'intent_id'}
            or request.get('stage') not in STAGES
            or any(not isinstance(request.get(key), str) or not re.fullmatch('[0-9a-f]{' + str(size) + '}', request[key])
                   for key, size in [('execution_id', 32), ('intent_id', 32), ('code_sha256', 64), ('binding_sha256', 64)])):
        raise ValueError('Invalid native frontend intent')


def require_receipt(receipt, request):
    if (not isinstance(receipt, dict) or set(receipt) != {*request, 'version', 'status', 'evidence'}
            or type(receipt.get('version')) is not int or receipt['version'] != 1
            or receipt.get('status') != 'passed' or not isinstance(receipt.get('evidence'), dict)
            or any(receipt.get(key) != value for key, value in request.items())):
        raise ValueError('Native terminal receipt identity differs')
    return receipt


class Coordinator:
    def __init__(self, directory, value, *, kube=None, processes=None, http_factory=FrontendHTTP):
        validate_live_inputs(value)
        if (value.get('deployment_mode') != 'frontend-split'
                or value.get('profile') != 'basic'
                or value.get('functional_recipe') != 'owned-simulation-local-v1'
                or not re.fullmatch('sha256:[0-9a-f]{64}', value.get('expected_model_image_id', ''))):
            raise ValueError('Explicit isolated frontend functional recipe required')
        self.value, self.files = value, PrivateFiles(directory)
        self.kube = kube or Kubernetes(value)
        self.processes = processes or OwnedProcesses(directory, value)
        self.http_factory, self.sleep = http_factory, time.sleep

    def snapshot(self):
        namespace = json.loads(self.kube.run('get', 'namespace', self.value['namespace'], '-o', 'json'))
        objects = json.loads(self.kube.run('get', 'deployments,statefulsets,replicasets,pods,services,endpointslices,'
            'configmaps,secrets,persistentvolumeclaims,networkpolicies,jobs,cronjobs,daemonsets', '-o', 'json'))['items']
        objects = [namespace, *objects]
        admission = validate_frontend_inventory(objects, self.value)
        if any(o['kind'] in {'Job', 'CronJob', 'DaemonSet'} for o in objects):
            raise ValueError('Unexpected native controller')
        models = role_pods(objects, 'model-stub')
        deployments = [o for o in objects if o['kind'] == 'Deployment' and o['metadata']['name'] == 'model-stub']
        if len(models) != 1 or len(deployments) != 1:
            raise ValueError('One owned model stub required')
        model, deployment = models[0], deployments[0]
        cs = model.get('status', {}).get('containerStatuses', [])
        if (deployment['spec'].get('replicas') != 1 or model['status'].get('phase') != 'Running'
                or deployment['spec'].get('selector', {}).get('matchLabels', {}).get('okf-role') != 'model-stub'
                or deployment.get('status', {}).get('observedGeneration') != deployment['metadata'].get('generation')
                or any(deployment.get('status', {}).get(k) != 1 for k in ('readyReplicas', 'updatedReplicas', 'availableReplicas'))
                or len(cs) != 1 or cs[0].get('ready') is not True or cs[0].get('restartCount') != 0
                or not cs[0].get('imageID', '').endswith(self.value['expected_model_image_id'])
                or model['metadata'].get('labels', {}).get('app.kubernetes.io/instance') != self.value['application_release']):
            raise ValueError('Owned model image/readiness differs')
        owners = model['metadata'].get('ownerReferences', [])
        sets = [o for o in objects if o['kind'] == 'ReplicaSet' and len(owners) == 1
                and owners[0].get('kind') == 'ReplicaSet' and o['metadata']['uid'] == owners[0]['uid']]
        if (len(sets) != 1 or len(sets[0]['metadata'].get('ownerReferences', [])) != 1
                or sets[0]['metadata']['ownerReferences'][0].get('kind') != 'Deployment'
                or sets[0]['metadata']['ownerReferences'][0].get('uid') != deployment['metadata']['uid']):
            raise ValueError('Owned model controller differs')
        provider_services = {}
        for role, port in [('postgres',5432),('qdrant',6333)]:
            services = [o for o in objects if o['kind']=='Service'
                        and any(p.get('port')==port for p in o.get('spec',{}).get('ports',[]))]
            if len(services)!=1: raise ValueError('One internal provider Service required')
            service=services[0];selector=service['spec'].get('selector',{})
            if service['spec'].get('type','ClusterIP')!='ClusterIP' or service['spec'].get('externalName') or not selector:
                raise ValueError('Native provider Service must be internal and selected')
            targets=[p for p in objects if p['kind']=='Pod'
                     and all(p['metadata'].get('labels',{}).get(key)==value for key,value in selector.items())]
            if len(targets)!=1: raise ValueError('One owned provider Pod required')
            refs=targets[0]['metadata'].get('ownerReferences',[])
            owners=[o for o in objects if o['kind']=='StatefulSet' and len(refs)==1
                    and refs[0].get('kind')=='StatefulSet' and o['metadata']['uid']==refs[0].get('uid')]
            if len(owners)!=1 or owners[0]['spec'].get('replicas')!=1:
                raise ValueError('Provider StatefulSet ownership differs')
            provider_services[role]=service['metadata']['name']
        claims = [o for o in objects if o['kind'] == 'PersistentVolumeClaim']
        if not claims or any(c.get('status', {}).get('phase') != 'Bound' for c in claims):
            raise ValueError('Bound native claims required')
        names = {c['metadata']['name'] for c in claims}
        for pod in (o for o in objects if o['kind'] == 'Pod'):
            for volume in pod.get('spec', {}).get('volumes', []):
                if 'persistentVolumeClaim' in volume and volume['persistentVolumeClaim']['claimName'] not in names:
                    raise ValueError('Unobserved native claim')
        stable = []
        for claim in claims:
            volume = json.loads(self.kube.run('get', 'pv', claim['spec']['volumeName'], '-o', 'json'))
            ref = volume['spec']['claimRef']
            if ref.get('uid') != claim['metadata']['uid'] or ref.get('namespace') != self.value['namespace']:
                raise ValueError('Native physical volume binding differs')
            stable.append({'kind': 'PersistentVolume', 'name': claim['spec']['volumeName'], 'uid': volume['metadata']['uid']})
        for node_name in sorted({p['spec']['nodeName'] for p in objects if p['kind'] == 'Pod'}):
            node = json.loads(self.kube.run('get', 'node', node_name, '-o', 'json'))
            stable.append({'kind': 'Node', 'name': node_name, 'uid': node['metadata']['uid']})
        for obj in objects:
            kind, meta = obj['kind'], obj['metadata']
            if kind in {'ReplicaSet', 'EndpointSlice'} or (kind == 'Pod' and meta.get('labels', {}).get('okf-role') == 'frontend'):
                continue
            item = {'kind': kind, 'name': meta['name'], 'uid': meta['uid'], 'spec': obj.get('spec', {})}
            if kind in {'ConfigMap', 'Secret'}:
                item['data_sha256'] = digest({k: obj.get(k) for k in ('data', 'binaryData', 'immutable', 'type')})
            if kind == 'Pod':
                if meta.get('deletionTimestamp'): raise ValueError('A pinned native Pod is retiring')
                statuses = obj.get('status', {}).get('containerStatuses', [])
                if not statuses or any(c.get('ready') is not True or c.get('restartCount') != 0 for c in statuses):
                    raise ValueError('Pinned native Pod restarted or is not ready')
                item['image_ids'] = [c['imageID'] for c in statuses]
            stable.append(item)
        backend = next(p for p in role_pods(objects, 'application'))
        fronts = sorted(role_pods(objects, 'frontend'), key=lambda p: p['metadata']['uid'])
        selected = lambda p: {k: p['metadata'][k] for k in ('name', 'uid')}
        return {'resources_sha256': digest(sorted(stable, key=lambda o: (o['kind'], o['name']))),
                'backend': selected(backend), 'model': selected(model), 'frontends': [selected(p) for p in fronts],
                'backend_pod_uid': admission['backend_pod_uid'], 'provider_services':provider_services}

    def guard(self):
        actual = self.snapshot()
        if self.files.path('scope.json').exists():
            original = self.files.read('scope.json')
            if {k:v for k,v in actual.items() if k!='frontends'} != {k:v for k,v in original.items() if k!='frontends'}:
                raise ValueError('Native frontend configuration/controller/claim binding changed')
            if actual['frontends'] != original['frontends']:
                if not self.files.path('loss.delete-ack.json').exists(): raise ValueError('Unplanned frontend replacement')
                victim = self.files.read('loss.delete-ack.json')['victim']
                survivor = next(p for p in original['frontends'] if p['uid'] != victim['uid'])
                if survivor not in actual['frontends'] or any(p['uid']==victim['uid'] for p in actual['frontends']):
                    raise ValueError('Unexpected frontend membership after owned loss')
        return actual

    def http(self):
        first = 'pf-0r' if self.files.path('pf-0r.pid.json').exists() else 'pf-0'
        routes = [self.processes.route(slot) for slot in (first, 'pf-1', 'pf-2')]
        snapshot = self.guard()
        current = {p['uid'] for p in [*snapshot['frontends'], snapshot['backend']]}
        if any(route['pod']['uid'] not in current for route in routes): raise ValueError('Native route targets an old Pod')
        return self.http_factory(self.files.directory, [r['url'] for r in routes],
                                 self.value['simulation_username'], self.execution_id)

    def metrics(self):
        original = self.files.read('scope.json')
        source = '''# FRONTEND_MODEL_METRICS
import json,urllib.request
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
value=json.load(opener.open('http://model-stub:8000/test/status',timeout=3))
print(json.dumps({k:value[k] for k in ['started','finished','active']}))
'''
        return self.kube.python(original['backend']['name'], source)

    def session(self, snapshot):
        for index, pod in enumerate([*snapshot['frontends'], snapshot['backend']]):
            self.processes.forward('pf-' + str(index), pod, 8000 if index == 2 else 3000)
        return self.http().session()

    def loss(self, snapshot):
        baseline = self.metrics()
        if baseline['active'] != 0 or baseline['started'] != baseline['finished']:
            raise ValueError('Model must be idle before frontend loss')
        victim = self.files.read('scope.json')['frontends'][0]
        route = self.processes.route('pf-0')
        if route['pod'] != victim: raise ValueError('Native victim forward differs')
        self.files.once('loss.baseline.json', {'model': baseline, 'victim': victim})
        self.processes.start_client(LOSS_CLIENT, {'directory': str(self.files.directory), 'url': route['url']})
        gate = None
        for _ in range(80):
            path = self.files.path('loss.progress.json')
            progress = self.files.read(path.name) if path.exists() else {}
            model = self.metrics()
            client = self.files.read('client.pid.json')
            if self.processes.describe(client['pid']) != client: break
            if (progress.get('sources_seen') is True and not progress.get('terminal') and not progress.get('result_seen')
                    and model['active'] == 1 and model['started'] == baseline['started'] + 1
                    and model['finished'] == baseline['finished']):
                gate = {'first_sources_frame': True, 'model': model}; break
            self.sleep(.1)
        if gate is None: raise ValueError('No actual source-frame/model-active gate; no Pod deletion')
        self.guard()
        current = json.loads(self.kube.run('get', 'pod', victim['name'], '-o', 'json'))
        if current['metadata']['uid'] != victim['uid'] or current['metadata'].get('deletionTimestamp'):
            raise ValueError('Native victim changed before deletion')
        progress = self.files.read('loss.progress.json')
        model = self.metrics()
        if (self.processes.describe(client['pid']) != client or progress.get('sources_seen') is not True
                or progress.get('terminal') or progress.get('result_seen') or model['active'] != 1
                or model['started'] != baseline['started']+1 or model['finished'] != baseline['finished']):
            raise ValueError('Native stream/model gate ended before DELETE; no deletion')
        gate['model'] = model
        options = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'gracePeriodSeconds': 2, 'preconditions': {'uid': victim['uid']}}
        evidence = {'victim': victim, 'gate': gate, 'delete_options': options}
        self.files.once('loss.delete-intent.json', evidence)
        uri = '/api/v1/namespaces/' + self.value['namespace'] + '/pods/' + victim['name']
        answer = json.loads(self.kube.run('delete', '--raw', uri, '-f', '-', source=json.dumps(options)))
        if not ((answer.get('kind') == 'Status' and answer.get('status') == 'Success')
                or (answer.get('kind') == 'Pod' and answer.get('metadata', {}).get('uid') == victim['uid'])):
            raise ValueError('Unknown DELETE acknowledgement; inspect same intent')
        self.files.once('loss.delete-ack.json', evidence)
        return evidence

    def loss_observe(self):
        original = self.files.read('loss.baseline.json')
        client = self.files.read('client.pid.json')
        model = self.metrics()
        if model['started'] > original['model']['started'] + 1: raise ValueError('Model was automatically called again')
        if self.processes.describe(client['pid']) == client or model['active'] != 0:
            return {'status': 'pending'}
        progress = self.files.read('loss.progress.json')
        if not progress.get('terminal') or not progress.get('sources_seen') or progress.get('result_seen'):
            raise ValueError('Lost stream has no proven interrupted terminal result')
        if model['started'] != model['finished'] or model['finished'] != original['model']['started'] + 1:
            return {'status': 'pending'}
        old = self.kube.run('get', 'pod', original['victim']['name'], '--ignore-not-found', '-o', 'json')
        if old.strip(): return {'status': 'pending'}
        return {'interrupted_stream': True, 'model': model, 'client_gone': True, 'victim_gone': True}

    def replacement(self, snapshot):
        original = self.files.read('scope.json')
        new = [p for p in snapshot['frontends'] if p not in original['frontends']]
        if len(new) != 1: raise ValueError('One owned replacement frontend required')
        self.processes.stop('pf-0')
        if self.files.path('pf-0r.intent.json').exists(): self.processes.route('pf-0r')
        else: self.processes.forward('pf-0r', new[0], 3000)
        http = self.http(); http.shared_session()
        if http.publish().get('status') != 'passed': raise ValueError('Publication did not survive replacement')
        export = http.export_observe()
        if export.get('status') != 'passed': raise ValueError('ZIP did not survive replacement')
        proof = {'replacement': new[0], 'session_survived': True, 'publication_survived': True, 'export_sha256': export['export_sha256']}
        self.files.once('replacement.result.json', proof)
        return proof

    def final(self):
        if self.files.read('logout.result.json').get('old_session_revoked_routes') != 3:
            raise ValueError('Server-side logout required before native closure')
        upload = self.files.read('upload.result.json')
        original = self.files.read('scope.json')
        calls = self.files.read('loss.baseline.json')['model']['started']+1
        expected = {**upload, 'namespace': self.value['namespace'], 'run_id': self.value['run_id'], 'model_calls': calls}
        source = '''# FRONTEND_FINAL_PROOF
import hashlib,json,urllib.request
from sqlalchemy import text
from sqlalchemy.engine import make_url
from app.config import get_settings
from app.db.session import get_engine
p=json.loads(EXPECTED);s=get_settings()
assert s.deployment_namespace==p['namespace'] and make_url(s.db_url).database=='replica_test_'+p['run_id']
with get_engine().connect() as c:
 c.execute(text('SET TRANSACTION READ ONLY'))
 doc=c.execute(text('SELECT status,problem,file_hash FROM documents WHERE id=:id'),{'id':p['document_id']}).mappings().one()
 assert doc['status']=='done' and doc['problem'] is None and doc['file_hash']==p['original_sha256']
 assert c.scalar(text('SELECT count(*) FROM documents'))==1
 assert c.scalar(text('SELECT count(*) FROM auth_sessions'))==0
 assert c.scalar(text("SELECT count(*) FROM jobs WHERE status NOT IN ('completed','cancelled','failed')"))==0
 assert c.scalar(text("SELECT count(*) FROM work_items WHERE status IN ('running','queued')"))==0
 assert c.scalar(text("SELECT count(*) FROM chat_runs WHERE status='running'"))==0
 assert c.scalar(text('SELECT count(*) FROM diagnostic_sessions WHERE active_slot IS NOT NULL'))==0
 assert c.scalar(text('SELECT count(*) FROM replica_capacity_slots WHERE owner_id IS NOT NULL OR release_required'))==0
files=list(s.uploads_dir.glob(p['document_id']+'.*'));assert len(files)==1
with files[0].open('rb') as stream:raw=stream.read(8*1024*1024+1)
assert len(raw)<=8*1024*1024 and hashlib.sha256(raw).hexdigest()==p['original_sha256']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
model=json.load(opener.open('http://model-stub:8000/test/status',timeout=3))
assert model['started']==model['finished']==p['model_calls'] and model['active']==0
print(json.dumps(dict(quiescent=True,original_sha256=p['original_sha256'],model={k:model[k] for k in ['started','finished','active']})))
'''.replace('EXPECTED', repr(json.dumps(expected)))
        def proof():
            value = self.kube.python(original['backend']['name'], source)
            if (value.get('quiescent') is not True or value.get('original_sha256') != upload['original_sha256']
                    or value.get('model') != {'started':calls,'finished':calls,'active':0}):
                raise ValueError('Native closure proof differs')
            return value
        proof()
        for slot in ('pf-0','pf-0r','pf-1','pf-2','client'):
            self.processes.stop(slot)
            if self.files.read(slot+'.closed.json').get('original_process_gone') is not True:
                raise ValueError('Native owned handle closure incomplete')
        self.guard()
        return {**proof(), 'owned_handles_closed':5}

    def perform(self, stage, snapshot):
        if stage == 'preflight':
            self.fresh_proof(snapshot)
            if not self.files.path('scope.json').exists(): self.files.once('scope.json', snapshot)
            return {'scope_sha256': digest(snapshot), 'fresh_isolated_fixture': True}
        if stage == 'session': return self.session(snapshot)
        if stage == 'loss': return self.loss(snapshot)
        if stage == 'loss-observe': return self.loss_observe()
        if stage == 'replacement': return self.replacement(snapshot)
        if stage == 'final': return self.final()
        function = {'upload': 'upload', 'publish': 'publish', 'export': 'export',
                    'export-observe': 'export_observe', 'stream': 'stream', 'logout': 'logout'}[stage]
        return getattr(self.http(), function)()

    def fresh_proof(self, snapshot):
        source = '''import json,urllib.request
from urllib.parse import urlsplit
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.engine import make_url
from app.config import get_settings
from app.db.session import get_engine
p=json.loads(EXPECTED)
s=get_settings()
assert s.environment=='development' and s.app_role=='combined' and s.deployment_mode=='compact' and s.okf_deployment_schema is None
assert s.resilience_class=='basic'
assert s.deployment_namespace==p['namespace'] and make_url(s.db_url).database=='replica_test_'+p['run_id']
database=make_url(s.db_url);vector=urlsplit(s.qdrant_url)
def local_hosts(role):
 name=p['provider_services'][role]
 return {name,name+'.'+p['namespace']+'.svc',name+'.'+p['namespace']+'.svc.cluster.local'}
assert database.host in local_hosts('postgres') and database.port in {None,5432}
assert vector.scheme=='https' and vector.hostname in local_hosts('qdrant') and vector.port==6333
assert vector.path in {'','/'} and not vector.query and not vector.fragment and not vector.username and not vector.password
assert database.query.get('sslmode')=='verify-full' and database.query.get('sslrootcert') and Path(database.query['sslrootcert']).is_file()
assert s.qdrant_collection=='replica_test_'+p['run_id'] and s.artifact_backend=='local' and not s.artifact_uploads_enabled
assert s.auth_provider=='simulation' and s.embedding_provider=='fake' and s.model_mode=='stub'
assert s.llm_base_url=='http://model-stub:8000/v1'
assert not any(directory.exists() and any(directory.iterdir()) for directory in [s.uploads_dir,s.okf_dir,s.staging_dir,s.exports_dir])
with get_engine().connect() as c:
 c.execute(text('SET TRANSACTION READ ONLY'))
 counts={name:c.scalar(text('SELECT count(*) FROM '+name)) for name in ['documents','jobs','auth_sessions','work_items','chat_runs','diagnostic_sessions']}
 assert not any(counts.values())
 assert c.scalar(text('SELECT NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user'))
 assert c.scalar(text('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()'))
from app.services.vector_store import shared_qdrant_client
client=shared_qdrant_client(s)
if client.collection_exists(s.qdrant_collection):assert client.count(s.qdrant_collection,exact=True).count==0
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
model=json.load(opener.open('http://model-stub:8000/test/status',timeout=3))
assert model['started']==model['finished']==model['active']==0
print(json.dumps(dict(fresh=True,model={k:model[k] for k in ['started','finished','active']})))
'''.replace('EXPECTED', repr(json.dumps({**{k: self.value[k] for k in ('namespace', 'run_id')},
                                       'provider_services':snapshot['provider_services']})))
        proof = self.kube.python(snapshot['backend']['name'], source)
        if proof.get('fresh') is not True or proof.get('model') != {'started': 0, 'finished': 0, 'active': 0}:
            raise ValueError('Native application is not a fresh isolated fixture')

    def dispatch(self, request, *, observe=False):
        validate_request(request)
        self.execution_id = request['execution_id']
        binding = {**{k: request[k] for k in IDENTITY}, 'inputs_sha256': digest(self.value)}
        binding_path = self.files.path('native-binding.json')
        if binding_path.exists():
            if self.files.read('native-binding.json') != binding: raise ValueError('Native frontend identity differs')
        elif observe: raise ValueError('No native execution to observe')
        else: self.files.once('native-binding.json', binding)
        stage = request['stage']
        for previous in STAGES[:STAGES.index(stage)]:
            path = self.files.path('native.' + previous + '.result.json')
            if not path.exists(): raise ValueError('Native preceding stage incomplete')
            receipt = self.files.read(path.name)
            prior_request = self.files.read('native.'+previous+'.intent.json')
            validate_request(prior_request)
            require_receipt(receipt,prior_request)
            if prior_request['stage'] != previous or any(receipt[k] != request[k] for k in IDENTITY):
                raise ValueError('Native preceding stage identity differs')
        intent, result = 'native.' + stage + '.intent.json', 'native.' + stage + '.result.json'
        exists = self.files.path(intent).exists()
        if exists:
            if self.files.read(intent) != request: raise ValueError('Native stage intent differs')
            if self.files.path(result).exists():
                self.guard()
                return require_receipt(self.files.read(result),request)
        elif observe: raise ValueError('No native intent to observe')
        else: self.files.once(intent, request)
        snapshot = self.guard()
        if exists and stage not in READ_ONLY and stage != 'final':
            terminal = {'loss': 'loss.delete-ack.json', 'replacement': 'replacement.result.json'}.get(stage, stage + '.result.json')
            if not self.files.path(terminal).exists(): return None
            evidence = self.files.read(terminal)
        else: evidence = self.perform(stage, snapshot)
        status = evidence.pop('status', 'passed')
        if status not in {'passed', 'pending'}: raise ValueError('Unknown native stage outcome')
        receipt = {**request, 'version': 1, 'status': status, 'evidence': evidence}
        if status == 'passed': self.files.once(result, receipt)
        return receipt
