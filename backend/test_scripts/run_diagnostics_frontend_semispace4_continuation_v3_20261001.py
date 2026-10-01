"""Continue unmeasured Node trial; compare all mount fields independent of Docker list order."""
import subprocess

CODE = r'''
import json,os,sys,subprocess,time
from pathlib import Path
root=Path('/opt/okf-diag-levels-20260929');stage='frontend-semispace4-continuation-v3-v16-v11-20261001';project='okf-diag-levels-20260929'
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
override=root/'compose-frontend-semispace4-continuation-v3-20261001.json';assert not override.exists()
override.write_text(json.dumps({'services':{'frontend':{'command':['node','--max-semi-space-size=4','diagnostics-runner.mjs']}}},indent=2)+'\n')
rows=[];record={'stage':stage,'localization_only':True,'started_epoch':time.time(),'runtime_flags_original':original['Config']['Cmd'],'images':expected}
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
 prior=root/'frontend-semispace4-control-v16-v11-20261001.json'
 prior_data=json.loads(prior.read_text());assert len(prior_data['rows'])==3 and all(r['treatment']=='default' for r in prior_data['rows'])
 record['default_reference']=prior.name
 record['default_reference_sha256']=__import__('hashlib').sha256(prior.read_bytes()).hexdigest()
 for treatment in ('semispace4',):
  print(json.dumps({'treatment':treatment,'phase':'recreate','at_monotonic':time.monotonic()}),flush=True)
  recreate(treatment=='semispace4')
  for i,mode in enumerate(('baseline','standard','baseline')):
   print(json.dumps({'treatment':treatment,'mode':mode,'phase':'start','at_monotonic':time.monotonic()}),flush=True)
   row=measure('http://127.0.0.1:18084',concurrency=8,warmup_seconds=30,measurement_seconds=60,min_requests=1000,mode=mode,repeat=i+1,corpus_size=1,spool_root=root/'diagnostics-1doc',build_bundle=mode=='standard',stop_before_bundle=mode=='standard',actor_username='diag.stop02' if treatment=='default' else 'diag.stop03',containers=_containers(project))
   row['treatment']=treatment;rows.append(row)
   output.write_text(json.dumps({'stage':stage,'localization_only':True,'images':expected,'rows':rows},indent=2)+'\n')
   print(json.dumps({'treatment':treatment,'mode':mode,'phase':'end','at_monotonic':time.monotonic(),'requests':row['requests'],'p95_ms':row['p95_ms'],'frontend_peak':row['peak_rss_bytes']['frontend']}),flush=True)
 record['complete']=True
except BaseException as exc:
 record['complete']=False;record['failure_type']=type(exc).__name__
 raise
finally:
 try:
  recreate(False);record['original_frontend_command_restored']=True
  c=Client('http://127.0.0.1:18084');c.login();s=c.call('GET','/api/admin/diagnostics/status')
  record['post']={'capture_off':s.get('session',{}).get('session') is None,'runtime_available':s.get('runtime',{}).get('available'),'recorder':{k:s.get('recorder',{}).get(k) for k in ('queued','dropped','storage_errors')},'images':_image_ids(_containers(project))}
 finally:
  record['finished_epoch']=time.time();status_file.write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record),flush=True)
'''

if __name__ == "__main__":
    result = subprocess.run(["pct", "exec", "102", "--", "python3", "-c", CODE], check=False)
    raise SystemExit(result.returncode)
