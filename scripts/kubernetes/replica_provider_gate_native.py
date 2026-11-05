"""Literal VM103/API SQL gate bridge; no payload-provided SQL or program paths."""
from __future__ import annotations

import json

from replica_provider_gate import gate_scope
from test_scripts.replica_provider_faults import hexadecimal, require

ACTOR_SOURCE = r'''import json,os,select,sys,time,traceback
try:
 from sqlalchemy import text
 from app.config import get_settings
 from app.db.session import get_engine
 from app.deployment.runtime_identity import enforce_runtime_identity
 p=json.loads(sys.stdin.readline());s=get_settings();identity=enforce_runtime_identity(s)
 assert s.model_mode=='stub' and s.auth_provider=='simulation' and s.embedding_provider=='fake' and s.app_role=='api'
 assert s.deployment_namespace==p['namespace'] and identity.fingerprint==p['fingerprint'] and identity.installation_id==p['installation_id']
 assert p['maximum_seconds']==180 and os.getuid()!=0
 with get_engine().connect() as c:
  with c.begin() as tx:
   assert c.scalar(text('SELECT current_database()'))=='replica_test_'+p['run_id']
   assert c.execute(text('SELECT epoch,enabled FROM replica_maintenance_state WHERE id=1')).one()==(p['epoch'],False)
   assert c.scalar(text("SELECT count(*) FROM replica_capacity_slots WHERE resource='llm' AND (owner_id IS NOT NULL OR release_required)"))==0
   c.execute(text("SET LOCAL lock_timeout='5s'"))
   c.execute(text("SET LOCAL idle_in_transaction_session_timeout='180s'"))
   c.execute(text("SELECT set_config('application_name',:name,true)"),{'name':'okf-provider-gate-'+p['operation_id']})
   assert c.execute(text("SELECT resource FROM replica_capacity_resources WHERE resource='llm' FOR UPDATE")).one()==('llm',)
   pg_pid=c.scalar(text('SELECT pg_backend_pid()'))
   started=c.scalar(text('SELECT backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid()')).isoformat()
   ready=dict(operation_id=p['operation_id'],pg_pid=pg_pid,pg_started_at=started,maximum_seconds=180,
    deadline_monotonic=time.monotonic()+180)
   print('ROW_GATE_READY:'+json.dumps(ready),flush=True)
   readable=select.select([sys.stdin],[],[],180)[0]
   line=sys.stdin.readline().strip() if readable else None
   reason='explicit-release' if line=='release '+p['operation_id'] else ('expired' if line is None else 'controller-EOF')
   tx.rollback()
  closed_at=c.scalar(text('SELECT clock_timestamp()')).isoformat();c.rollback()
 print('ROW_GATE_CLOSED:'+json.dumps(dict(operation_id=p['operation_id'],pg_pid=pg_pid,
  pg_started_at=started,reason=reason,rolled_back=True,closed_at=closed_at)),flush=True)
except BaseException as e:
 print('ROW_GATE_ERROR:'+json.dumps(dict(error_type=type(e).__name__,locations=[__import__('pathlib').Path(f.filename).name+':'+str(f.lineno) for f in traceback.extract_tb(e.__traceback__)[-3:]])),flush=True)
 raise SystemExit(1)
'''

SUPERVISOR_SOURCE = r'''import json,os,pathlib,subprocess,sys,time,traceback
ACTOR=ACTOR_PLACEHOLDER
d=pathlib.Path(__file__).parent;p=json.loads((d/'binding.json').read_bytes());result={}
def once(name,value):
 fd=os.open(d/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
def describe(pid):
 root=pathlib.Path('/proc')/str(pid)
 if not root.exists():return None
 values=(root/'stat').read_text().rsplit(') ',1)[1].split()
 if values[0]=='Z':return None
 return dict(pid=pid,start_ticks=values[19],uid=root.stat().st_uid,argv=(root/'cmdline').read_bytes().decode().rstrip('\0').split('\0'))
child=None
try:
 argv=['kubectl','--context','kind-okf-policy','-n',p['namespace'],'exec','-i',p['api_pod'],'--','python','-u','-c',ACTOR]
 once('child-intent.json',{'argv':argv})
 with (d/'actor-stderr.log').open('xb') as err:
  child=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,text=True,bufsize=1)
  handle=describe(child.pid);assert handle and handle['argv']==argv;once('child.json',handle)
  child.stdin.write(json.dumps(p)+'\n');child.stdin.flush()
  line=child.stdout.readline();assert line.startswith('ROW_GATE_READY:')
  ready=json.loads(line.split(':',1)[1]);assert ready['operation_id']==p['operation_id'] and ready['maximum_seconds']==180
  once('ready.json',ready)
  while child.poll() is None and time.monotonic()<ready['deadline_monotonic']+2:
   marker=d/'release'
   if marker.exists():
    info=marker.lstat();assert not marker.is_symlink() and info.st_nlink==1 and info.st_uid==os.getuid()
    assert marker.read_text()==p['operation_id']
    child.stdin.write('release '+p['operation_id']+'\n');child.stdin.flush();break
   time.sleep(.05)
  tail,_=child.communicate(timeout=12)
  closed=[json.loads(v.split(':',1)[1]) for v in tail.splitlines() if v.startswith('ROW_GATE_CLOSED:')]
  assert child.returncode==0 and len(closed)==1
  assert closed[0]['operation_id']==p['operation_id'] and closed[0]['pg_pid']==ready['pg_pid'] and closed[0]['pg_started_at']==ready['pg_started_at']
  result=dict(verified=True,ready=ready,closed=closed[0])
except BaseException as e:
 result=dict(verified=False,error_type=type(e).__name__,locations=[pathlib.Path(f.filename).name+':'+str(f.lineno) for f in traceback.extract_tb(e.__traceback__)[-3:]])
finally:
 if child is not None and child.poll() is None:
  # EOF releases the row gate independently; never kill a target process or
  # acknowledge an external provider on a controller error.
  try:child.stdin.close()
  except OSError:pass
 once('result.json',result)
'''.replace('ACTOR_PLACEHOLDER', repr(ACTOR_SOURCE))

_BACKEND_SOURCE = r'''import json
from datetime import datetime
from sqlalchemy import text
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
from app.db.session import get_engine
p=PROBE_INPUT;s=get_settings();identity=enforce_runtime_identity(s)
assert identity.fingerprint==p['fingerprint'] and s.deployment_namespace==p['namespace']
with get_engine().connect() as c:
 c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
 alive=c.scalar(text('SELECT count(*) FROM pg_stat_activity WHERE pid=:pid AND backend_start=:started'),
  {'pid':p['pg_pid'],'started':datetime.fromisoformat(p['pg_started_at'])})
 print(json.dumps({'sql_backend_gone':alive==0}))
'''

REMOTE_SOURCE = r'''import hashlib,json,os,pathlib,re,stat,subprocess,sys,time
SUPERVISOR=SUPERVISOR_PLACEHOLDER
BACKEND=BACKEND_PLACEHOLDER
p=json.load(sys.stdin);b=p['binding'];action=p['action'];assert action in {'start','observe','release'}
k=['kubectl','--context','kind-okf-policy'];n=k+['-n',b['namespace']]
ns=json.loads(subprocess.check_output(k+['get','namespace',b['namespace'],'-o','json'],timeout=12))
assert ns['metadata']['uid']==b['namespace_uid'] and ns['metadata']['labels']['okf-purpose']=='replica-test'
node=json.loads(subprocess.check_output(k+['get','node',b['node'],'-o','json'],timeout=12))
assert node['metadata']['uid']=='9020a1d9-843e-4947-8808-53477289b863'
api=json.loads(subprocess.check_output(n+['get','pod',b['api_pod'],'-o','json'],timeout=12))
c=api['status']['containerStatuses'][0]
assert api['metadata']['uid']==b['api_pod_uid'] and not api['metadata'].get('deletionTimestamp')
assert api['metadata']['labels']['okf-role']=='api' and api['metadata']['labels']['app.kubernetes.io/instance']==b['application_release']
assert api['spec']['nodeName']==b['node'] and c['containerID']==b['api_container_id'] and c['imageID']==b['api_image_id'] and c['restartCount']==0
assert api['status']['phase']=='Running' and 'running' in c['state']
assert re.fullmatch('[0-9a-f]{32}',b['operation_id']) and b['maximum_seconds']==180
d=pathlib.Path('/home/ubuntu/replica-provider-gate-'+b['operation_id'])
def once(name,value):
 fd=os.open(d/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
def describe(pid):
 root=pathlib.Path('/proc')/str(pid)
 if not root.exists():return None
 values=(root/'stat').read_text().rsplit(') ',1)[1].split()
 if values[0]=='Z':return None
 return dict(pid=pid,start_ticks=values[19],uid=root.stat().st_uid,argv=(root/'cmdline').read_bytes().decode().rstrip('\0').split('\0'))
if action=='start':
 assert c['ready'] is True and not d.exists();d.mkdir(mode=0o700)
 once('binding.json',b)
 fd=os.open(d/'supervisor.py',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:f.write(SUPERVISOR);f.flush();os.fsync(f.fileno())
 argv=['python3',str(d/'supervisor.py')];once('start-intent.json',{'argv':argv})
 with (d/'supervisor.log').open('xb') as log:
  child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
 handle=describe(child.pid);assert handle and handle['argv']==argv and handle['uid']==1000;once('process.json',handle)
else:
 assert d.is_dir() and not d.is_symlink()
assert d.stat().st_uid==1000 and d.stat().st_mode&0o077==0
for name in ['binding.json','supervisor.py','process.json']:
 path=d/name;info=path.lstat();assert stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==1000 and not path.is_symlink()
assert json.loads((d/'binding.json').read_bytes())==b and (d/'supervisor.py').read_text()==SUPERVISOR
handle=json.loads((d/'process.json').read_bytes());current=describe(handle['pid'])
if current is not None:assert current==handle
if action=='release':
 assert current==handle and (d/'ready.json').is_file() and not (d/'release').exists()
 fd=os.open(d/'release',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:f.write(b['operation_id']);f.flush();os.fsync(f.fileno())
for _ in range(150):
 if (d/'result.json').exists():break
 if action!='release' and (d/'ready.json').exists():break
 assert describe(handle['pid'])==handle
 time.sleep(.05)
ready=json.loads((d/'ready.json').read_bytes())
proof=dict(native_process={k:handle[k] for k in ['pid','start_ticks','uid']},pg_pid=ready['pg_pid'],pg_started_at=ready['pg_started_at'],maximum_seconds=180)
proof['native_process']['argv_sha256']=hashlib.sha256(json.dumps(handle['argv'],separators=(',',':')).encode()).hexdigest()
if (d/'result.json').exists():
 result=json.loads((d/'result.json').read_bytes());assert result['verified'] is True and result['ready']==ready
 for _ in range(50):
  if describe(handle['pid']) is None:break
  time.sleep(.05)
 assert describe(handle['pid']) is None
 expected={**b,'pg_pid':ready['pg_pid'],'pg_started_at':ready['pg_started_at']}
 code=BACKEND.replace('PROBE_INPUT',repr(expected))
 raw=subprocess.check_output(n+['exec','-i',b['api_pod'],'--','python','-c',code],timeout=15)
 probe=json.loads(raw);assert probe['sql_backend_gone'] is True
 closed=result['closed'];assert closed['rolled_back'] is True
 print(json.dumps(dict(binding=b,proof=proof,phase='closed',native_alive=False,sql_backend_gone=True,
  rolled_back=True,reason=closed['reason'],closed_at=closed['closed_at'])))
else:
 assert describe(handle['pid'])==handle
 child=json.loads((d/'child.json').read_bytes());assert describe(child['pid'])==child
 print(json.dumps(dict(binding=b,proof=proof,phase='ready',native_alive=True,remaining_seconds=ready['deadline_monotonic']-time.monotonic())))
'''.replace('SUPERVISOR_PLACEHOLDER', repr(SUPERVISOR_SOURCE)).replace('BACKEND_PLACEHOLDER', repr(_BACKEND_SOURCE))

CLAIM_SOURCE = r'''import json
from datetime import datetime
from sqlalchemy import text
from app.config import get_settings
from app.db.session import get_engine
from app.deployment.runtime_identity import enforce_runtime_identity
p=PROBE_INPUT;s=get_settings();identity=enforce_runtime_identity(s)
assert identity.fingerprint==p['fingerprint'] and identity.installation_id==p['installation_id'] and s.deployment_namespace==p['namespace']
with get_engine().connect() as c:
 c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
 gate=c.execute(text('SELECT pid,backend_start FROM pg_stat_activity WHERE pid=:pid AND backend_start=:started AND application_name=:app'),
  {'pid':p['pg_pid'],'started':datetime.fromisoformat(p['pg_started_at']),'app':'okf-provider-gate-'+p['operation_id']}).mappings().one()
 work=dict(c.execute(text('SELECT id,doc_id,kind,status,attempts,fence,owner_id,lease_until,cancel_requested FROM work_items WHERE id=:id'),{'id':p['work_id']}).mappings().one())
 blocked=[dict(v) for v in c.execute(text('SELECT host(client_addr) AS client_addr,pg_blocking_pids(pid) AS blocked_by FROM pg_stat_activity WHERE datname=current_database() AND :gate=ANY(pg_blocking_pids(pid))'),{'gate':p['pg_pid']}).mappings()]
 result=dict(work=work,now=c.scalar(text('SELECT clock_timestamp()')),gate_pid=gate['pid'],gate_started_at=gate['backend_start'],blocked=blocked,
  model_owners=c.scalar(text("SELECT count(*) FROM replica_capacity_slots WHERE resource='llm' AND owner_id IS NOT NULL")),
  running_documents=c.scalar(text("SELECT count(*) FROM work_items WHERE kind='document' AND status='running' AND NOT cancel_requested")),
  running_chats=c.scalar(text("SELECT count(*) FROM chat_runs WHERE status='running'")))
 print(json.dumps(result,default=lambda v:v.isoformat()))
'''


class NativeGate:
    def __init__(self, kube, value, operation, *, remote_call=None):
        if remote_call is None:
            from replica_worker_loss import remote
            remote_call = remote
        self.kube, self.value, self.operation, self.remote = kube, value, operation, remote_call

    def __call__(self, action, binding):
        require(action in {'start', 'observe', 'release'} and isinstance(binding, dict))
        api = json.loads(self.kube.run('get', 'pod', binding.get('api_pod', ''), '-o', 'json'))
        expected = gate_scope(self.value, self.operation, api, binding.get('epoch'))
        require(binding == expected)
        if action == 'start':
            require(api['status']['containerStatuses'][0]['ready'] is True)
        return self.remote(['python3', '-c', REMOTE_SOURCE], source=json.dumps(dict(action=action, binding=binding)))

    def claim_probe(self, binding, proof, work_id):
        require(hexadecimal(work_id, 32))
        api = json.loads(self.kube.run('get', 'pod', binding['api_pod'], '-o', 'json'))
        require(binding == gate_scope(self.value, self.operation, api, binding['epoch']))
        payload = {**binding, 'pg_pid': proof['pg_pid'], 'pg_started_at': proof['pg_started_at'], 'work_id': work_id}
        return self.kube.python(binding['api_pod'], CLAIM_SOURCE.replace('PROBE_INPUT', repr(payload)))
