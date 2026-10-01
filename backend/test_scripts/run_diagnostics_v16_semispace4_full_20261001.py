"""One full stopped ZIP candidate block on measured Node semi-space4; original gates."""
import subprocess

CODE = r'''
import json,os,sys,subprocess,time
from pathlib import Path
root=Path('/opt/okf-diag-levels-20260929');stage='http-1doc-stopped-pve-v16-v11-semispace4-20261001';project='okf-diag-levels-20260929'
sys.path.insert(0,str(root/'backend/test_scripts'))
from probe_diagnostics_http_matrix import measure,_containers,_image_ids
from probe_diagnostics_http import Client
expected={'backend':'sha256:0a805ae89c3d42360981e170d845d150445912bb277946a52d5b75d9506de388','frontend':'sha256:a37ae1505d48528941a849e8ca7c07e568e6c453bdfa59e52626d5fba35b4799'}
output=root/(stage+'.json');status_file=root/(stage+'-status.json');assert not output.exists() and not status_file.exists()
containers=_containers(project);assert _image_ids(containers)==expected
original=json.loads(subprocess.check_output(['docker','inspect',containers['frontend']]))[0]
assert original['Config']['Cmd']==['node','diagnostics-runner.mjs']
labels=original['Config']['Labels'];assert labels['com.docker.compose.project']==project
files=labels['com.docker.compose.project.config_files'].split(',')
assert all(Path(f).resolve().is_relative_to(root) for f in files)
envfile=root/'runtime-1doc-bench.env';assert labels['com.docker.compose.project.environment_file']==str(envfile)
env={**os.environ,'OKF_RUNTIME_ENV_FILE':str(envfile)}
compose=['docker','compose','--env-file',str(envfile),'-p',project]
for f in files:compose+=['-f',f]
override=root/'compose-frontend-semispace4-full-20261001.json';assert not override.exists()
override.write_text(json.dumps({'services':{'frontend':{'command':['node','--max-semi-space-size=4','diagnostics-runner.mjs']}}},indent=2)+'\n')
rows=[];record={'stage':stage,'localization_only':False,'started_epoch':time.time(),'runtime_flags_original':original['Config']['Cmd'],'images':expected}
def healthy():
 for _ in range(80):
  try:
   c=Client('http://127.0.0.1:18084');c.login();s=c.call('GET','/api/admin/diagnostics/status')
   assert s.get('runtime',{}).get('available') is True
   assert s.get('session',{}).get('session') is None
   assert not s.get('recorder',{}).get('queued')
   return
  except Exception:time.sleep(.5)
 raise TimeoutError('Frontend test health wait failed')
def recreate(extra=None):
 command=compose+(['-f',str(override)] if extra else [])+['up','-d','--no-deps','--force-recreate','frontend']
 subprocess.run(command,cwd=root,env=env,check=True,stdout=subprocess.DEVNULL)
 healthy();assert _image_ids(_containers(project))==expected
 current=json.loads(subprocess.check_output(['docker','inspect',_containers(project)['frontend']]))[0]
 assert sorted(current['Config']['Env'])==sorted(original['Config']['Env'])
 assert sorted(current['Mounts'],key=lambda m:m['Destination'])==sorted(original['Mounts'],key=lambda m:m['Destination']), 'Frontend mount details differ'
 assert current['Config']['Cmd']==(['node','--max-semi-space-size=4','diagnostics-runner.mjs'] if extra else original['Config']['Cmd'])
 # Next process.title overwrites child argv. Verify parent flag and Node executable; fork inherits execArgv by documented contract.
 if extra:
  pid=current['State']['Pid'];children=Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
  assert len(children)==1
  assert b'--max-semi-space-size=4' in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
  assert Path(f'/proc/{children[0]}/exe').resolve()==Path(f'/proc/{pid}/exe').resolve()
  record['flag_verification']='parent cmdline + Node fork default execArgv; child title overwrites argv, no direct child assertion'
try:
 healthy()
 prior=root/'frontend-semispace4-continuation-v3-v16-v11-20261001-status.json'
 prior_status=json.loads(prior.read_text());assert prior_status['complete'] is True and prior_status['original_frontend_command_restored'] is True
 record['control_reference']=prior.name
 record['runtime_candidate_command']=['node','--max-semi-space-size=4','diagnostics-runner.mjs']
 record['original_gate_limits']={'p95_growth_percent':20,'backend_rss_growth_mib':128,'frontend_rss_growth_mib':32}
 print(json.dumps({'stage':stage,'phase':'recreate-candidate','at_monotonic':time.monotonic()}),flush=True)
 recreate(True)
 command=['python3',str(root/'backend/test_scripts/probe_diagnostics_http_matrix.py'),'--base-url','http://127.0.0.1:18084','--corpus-size','1','--output',str(output),'--spool-root',str(root/'diagnostics-1doc'),'--repeats','3','--warmup-seconds','30','--measurement-seconds','60','--min-requests','1000','--with-bundle','--stopped-bundle','--bundle-poll-interval','3','--admin-prefix','diag.stop']
 result=subprocess.run(command,cwd=root,check=False);record['probe_exit_code']=result.returncode
 assert result.returncode==0
 assert _image_ids(_containers(project))==expected
 from run_diagnostics_final_ct import validate_matrix
 record['gates']=validate_matrix(output,1,bundle=True,stopped=True)
 record['acceptance']='passed';record['complete']=True
except BaseException as exc:
 record['acceptance']='failed';record['complete']=False;record['failure_type']=type(exc).__name__;record['failed_gates']=getattr(exc,'failed_gates',[])
 raise
finally:
 try:
  recreate(False);record['original_frontend_command_restored']=True
  c=Client('http://127.0.0.1:18084');c.login();s=c.call('GET','/api/admin/diagnostics/status')
  record['post']={'capture_off':s.get('session',{}).get('session') is None,'runtime_available':s.get('runtime',{}).get('available'),'recorder':{k:s.get('recorder',{}).get(k) for k in ('queued','dropped','storage_errors')},'images':_image_ids(_containers(project))}
 finally:
  record['finished_epoch']=time.time();status_file.write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record),flush=True)
'''

if __name__ == '__main__':
    import json
    import threading
    import time
    from pathlib import Path
    stage='http-1doc-stopped-pve-v16-v11-semispace4-20261001'
    out=Path('/root/okf-memory-budget-20261001T004114Z')
    path=out/(stage+'-pve-metrics.jsonl');assert not path.exists()
    finished=threading.Event();status={'stage':stage,'started_epoch':time.time(),'observer_errors':[]}
    def numeric_sample():
        mem={}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key,_,value=line.partition(':')
            if key in ('MemTotal','MemAvailable'):mem[key]=int(value.split()[0])*1024
        vm=dict(line.split() for line in Path('/proc/vmstat').read_text().splitlines())
        io=next(line for line in Path('/proc/pressure/io').read_text().splitlines() if line.startswith('full '))
        row={'at_unix':time.time(),'at_monotonic':time.monotonic(),'mem':mem,'swap_in_pages':int(vm['pswpin']),'swap_out_pages':int(vm['pswpout']),'io_full_total_us':int(io.split('total=')[1]),'guests':{}}
        for guest in ('101','102'):
            cg=Path('/sys/fs/cgroup/lxc')/guest
            row['guests'][guest]={'memory_current_bytes':int((cg/'memory.current').read_text()),'swap_current_bytes':int((cg/'memory.swap.current').read_text()),'memory_events':{k:int(v) for k,v in (line.split() for line in (cg/'memory.events').read_text().splitlines())}}
        return row
    def observe():
        try:
            with path.open('x') as sink:
                while not finished.is_set():
                    sink.write(json.dumps(numeric_sample())+'\n');sink.flush()
                    if finished.wait(2):break
        except Exception as exc:status['observer_errors'].append(type(exc).__name__)
    worker=threading.Thread(target=observe);worker.start()
    try:
        result=subprocess.run(['pct','exec','102','--','python3','-c',CODE],check=False)
        status['exit_code']=result.returncode
    finally:
        finished.set();worker.join(timeout=3);status['finished_epoch']=time.time()
        (out/(stage+'-pve-status.json')).write_text(json.dumps(status,indent=2)+'\n')
        print(json.dumps(status),flush=True)
    raise SystemExit(result.returncode)
