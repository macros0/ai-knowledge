"""Bounded numeric Node profile on the authorized PVE/CT102 test frontend.
Run on PVE via SSH stdin. No heap/object/environment dumps; not SLA acceptance.
"""
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

STAGE = 'frontend-numeric-memory-v16-v11-20261001'
ROOT = '/opt/okf-diag-levels-20260929'
OUT = Path('/root/okf-memory-budget-20261001T004114Z')
EXPECTED = {'backend': 'sha256:0a805ae89c3d42360981e170d845d150445912bb277946a52d5b75d9506de388',
            'frontend': 'sha256:a37ae1505d48528941a849e8ca7c07e568e6c453bdfa59e52626d5fba35b4799'}
# External observer: enters only the frontend network namespace, not its cgroup.
CLIENT = r'''
import base64,hashlib,json,os,socket,struct,time,urllib.request,urllib.parse
url=json.load(urllib.request.urlopen('http://127.0.0.1:9229/json/list',timeout=3))[0]['webSocketDebuggerUrl']
u=urllib.parse.urlsplit(url); assert u.hostname=='127.0.0.1' and u.port==9229
s=socket.create_connection((u.hostname,u.port),timeout=4);key=base64.b64encode(os.urandom(16)).decode()
s.sendall(('GET '+u.path+' HTTP/1.1\r\nHost: 127.0.0.1:9229\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: '+key+'\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
buf=b''
while b'\r\n\r\n' not in buf:buf+=s.recv(4096)
header,buf=buf.split(b'\r\n\r\n',1)
assert header.startswith(b'HTTP/1.1 101')
assert base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()) in header

def exact(n):
 global buf
 while len(buf)<n:
  chunk=s.recv(max(4096,n-len(buf)))
  if not chunk:raise EOFError('inspector connection closed')
  buf+=chunk
 ans,buf=buf[:n],buf[n:];return ans

def send(message,opcode=1):
 data=json.dumps(message).encode() if opcode==1 else message
 mask=os.urandom(4);size=len(data)
 h=bytes([0x80|opcode,0x80|size]) if size<126 else bytes([0x80|opcode,0x80|126])+struct.pack('!H',size)
 s.sendall(h+mask+bytes(v^mask[i%4] for i,v in enumerate(data)))

def receive():
 while True:
  first,second=exact(2);size=second&127
  if size==126:size=struct.unpack('!H',exact(2))[0]
  if size==127:size=struct.unpack('!Q',exact(8))[0]
  assert size<=1048576 and first&128 and not second&128
  data=exact(size);opcode=first&15
  if opcode==9:send(data,10);continue
  if opcode==8:raise EOFError('inspector closed')
  assert opcode==1
  return json.loads(data)

counter=0
def evaluate(expression):
 global counter
 counter+=1;send({'id':counter,'method':'Runtime.evaluate','params':{'expression':expression,'returnByValue':True,'silent':True}})
 while True:
  result=receive()
  if result.get('id')==counter:
   assert 'error' not in result and 'exceptionDetails' not in result['result']
   return result['result']['result'].get('value')
expr="(()=>{const v=process.getBuiltinModule('v8');return {pid:process.pid,memory:process.memoryUsage(),heap:v.getHeapStatistics(),spaces:v.getHeapSpaceStatistics()}})()"
try:
 deadline=time.monotonic()+float(os.environ.get('PROFILE_SECONDS','300'))
 while time.monotonic()<deadline:
  start=time.monotonic();value=evaluate(expr)
  assert set(value)=={'pid','memory','heap','spaces'} and value['pid']==18
  assert all(type(v) in (int,float) for v in value['memory'].values())
  assert all(type(v) in (int,float) for v in value['heap'].values())
  for space in value['spaces']:
   assert set(space)=={'space_name','space_size','space_used_size','space_available_size','physical_space_size'}
   assert space['space_name'] in {'read_only_space','new_space','old_space','code_space','shared_space','new_large_object_space','large_object_space','code_large_object_space','shared_large_object_space'}
  print(json.dumps({'at_monotonic':start,'node':value}),flush=True)
  time.sleep(max(0,.5-(time.monotonic()-start)))
finally:
 try:evaluate("setTimeout(()=>process.getBuiltinModule('inspector').close(),100);true")
 finally:
  send(b'',8);s.close()
'''

WORKLOAD = r'''
import datetime,json,sys,time
from pathlib import Path
root=Path('/opt/okf-diag-levels-20260929');stage='frontend-numeric-memory-v16-v11-20261001'
sys.path.insert(0,str(root/'backend/test_scripts'))
from probe_diagnostics_http_matrix import measure,_containers,_image_ids
from probe_diagnostics_http import Client
expected=EXPECTED_VALUE
containers=_containers('okf-diag-levels-20260929');assert _image_ids(containers)==expected
output=root/(stage+'.json');assert not output.exists();rows=[]
try:
 for i,mode in enumerate(('baseline','standard','baseline')):
  print(json.dumps({'case':i+1,'mode':mode,'phase':'start','at_monotonic':time.monotonic()}),flush=True)
  row=measure('http://127.0.0.1:18084',concurrency=8,warmup_seconds=30,measurement_seconds=60,min_requests=1000,mode=mode,repeat=i+1,corpus_size=1,spool_root=root/'diagnostics-1doc',build_bundle=(mode=='standard'),stop_before_bundle=(mode=='standard'),actor_username='diag.stop01',containers=containers)
  rows.append(row);output.write_text(json.dumps({'stage':stage,'localization_only':True,'images':expected,'rows':rows},indent=2)+'\n')
  print(json.dumps({'case':i+1,'mode':mode,'phase':'end','at_monotonic':time.monotonic(),'requests':row['requests'],'p95_ms':row['p95_ms'],'frontend_peak':row['peak_rss_bytes']['frontend']}),flush=True)
finally:
 c=Client('http://127.0.0.1:18084');c.login();s=c.call('GET','/api/admin/diagnostics/status')
 (root/(stage+'-post.json')).write_text(json.dumps({'capture_off':s.get('session',{}).get('session') is None,'runtime_available':s.get('runtime',{}).get('available'),'recorder':{k:s.get('recorder',{}).get(k) for k in ('queued','dropped','storage_errors')},'images_after':_image_ids(containers)},indent=2)+'\n')
'''.replace('EXPECTED_VALUE', repr(EXPECTED))


def ct(*args, **kwargs):
    return subprocess.run(['pct', 'exec', '102', '--', *args], check=True, **kwargs)


def ns_command(code):
    return ['pct', 'exec', '102', '--', 'nsenter', '--target', '2701749', '--net',
            'python3', '-c', code]


def port_closed():
    code = "import socket; s=socket.socket(); s.settimeout(1); print(s.connect_ex(('127.0.0.1',9229))!=0); s.close()"
    return subprocess.check_output(ns_command(code)).decode().strip() == 'True'


def smaps(pid):
    fields = {'Rss', 'Pss', 'Private_Clean', 'Private_Dirty', 'Shared_Clean', 'Shared_Dirty', 'Anonymous', 'Swap'}
    result = {}
    for line in (Path('/proc')/str(pid)/'smaps_rollup').read_text().splitlines():
        key, _, value = line.partition(':')
        if key in fields:
            result[key] = int(value.split()[0])*1024
    return result


assert not (OUT/(STAGE+'-status.json')).exists()
assert port_closed()
for pid in (714672, 714731):
    p = Path('/proc')/str(pid)
    assert '/lxc/102/' in (p/'cgroup').read_text()
    assert 'docker-f14e89743cdfbb9212d0e7ccf1500914012902517728f3b4301b6ca2334e99c1.scope' in (p/'cgroup').read_text()
assert (Path('/proc/714731/status')).read_text().split('NSpid:')[1].splitlines()[0].split() == ['714731','2701749','18']
status = {'stage':STAGE,'localization_only':True,'started_epoch':time.time(),'observer_errors':[]}
observer = None
work = None
finished = threading.Event()


def observe_linux():
    try:
        with (OUT/(STAGE+'-smaps.jsonl')).open('x') as sink:
            while not finished.wait(.25):
                row = {'at_monotonic':time.monotonic(), 'collector':smaps(714672), 'next_server':smaps(714731)}
                sink.write(json.dumps(row)+'\n');sink.flush()
    except Exception as exc:
        status['observer_errors'].append(type(exc).__name__)


thread = threading.Thread(target=observe_linux)
try:
    os.kill(714731,signal.SIGUSR1)
    for _ in range(20):
        if not port_closed():break
        time.sleep(.1)
    assert not port_closed()
    with (OUT/(STAGE+'-v8.jsonl')).open('x') as numeric, (OUT/(STAGE+'-observer-private.log')).open('x') as private:
        observer = subprocess.Popen(ns_command(CLIENT),stdout=numeric,stderr=private)
        thread.start()
        # Numeric profiler handshake before load; do not proceed on unsupported schema.
        time.sleep(1)
        assert observer.poll() is None and (OUT/(STAGE+'-v8.jsonl')).stat().st_size>0
        with (OUT/(STAGE+'-cases.jsonl')).open('x') as cases:
            work = subprocess.Popen(['pct','exec','102','--','python3','-c',WORKLOAD],stdout=cases)
            deadline = time.monotonic()+420
            while work.poll() is None and time.monotonic()<deadline:
                assert observer.poll() is None, 'numeric observer exited early'
                time.sleep(1)
            if work.poll() is None:
                work.terminate();raise TimeoutError('bounded workload exceeded deadline')
            status['workload_exit_code']=work.returncode
            assert work.returncode==0
        observer.wait(timeout=45)
        status['numeric_observer_exit_code']=observer.returncode
        assert observer.returncode==0
    status['complete']=True
except BaseException as exc:
    status['complete']=False;status['failure_type']=type(exc).__name__
    raise
finally:
    if work is not None and work.poll() is None:
        work.terminate();work.wait(timeout=10)
    if observer is not None and observer.poll() is None:
        observer.terminate();observer.wait(timeout=10)
    finished.set()
    if thread.ident is not None:thread.join(timeout=3)
    # Close inspector even if client schema/connection failed before its finally.
    if not port_closed():
        close_client = CLIENT[:CLIENT.index('expr=')] + "\ntry:evaluate(\"setTimeout(()=>process.getBuiltinModule('inspector').close(),100);true\")\nfinally:send(b'',8);s.close()\n"
        subprocess.run(ns_command(close_client),check=False,stdout=subprocess.DEVNULL)
        time.sleep(.3)
    status['inspector_closed']=port_closed();status['finished_epoch']=time.time()
    (OUT/(STAGE+'-status.json')).write_text(json.dumps(status,indent=2)+'\n')
    print(json.dumps(status),flush=True)
