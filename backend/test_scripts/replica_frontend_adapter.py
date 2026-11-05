"""Fixed source-only native transport for the selected frontend fixture."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import subprocess

from test_scripts.replica_frontend_native import STAGES, digest, validate_request
from test_scripts.replica_runtime_acceptance import Kubernetes, validate_live_inputs

MODULES = ('replica_runtime_acceptance', 'replica_frontend_acceptance', 'replica_frontend_http',
           'replica_frontend_process', 'replica_frontend_native')
OIDC_MODULES = (*MODULES,'replica_frontend_oidc_http','replica_frontend_oidc_business','replica_frontend_oidc_native')
OIDC_FIELDS = ('oidc_access_namespace','oidc_access_namespace_uid','oidc_platform_namespace_uid',
               'oidc_controller_uid','oidc_gateway_class_uid','oidc_username','oidc_expected_images')
SAFE_FIELDS = ('profile', 'run_id', 'namespace', 'namespace_uid', 'installation_id', 'context', 'application_release',
               'runtime_secret', 'deployment_mode', 'frontend_url', 'simulation_username',
               'functional_recipe', 'expected_image_ids', 'expected_model_image_id')


def capsule_fingerprint(recipe):
    if recipe not in {'owned-keycloak-local-v1','owned-simulation-local-v1'}:raise ValueError('Unknown fixed frontend capsule')
    names=OIDC_MODULES if recipe=='owned-keycloak-local-v1' else MODULES
    return digest({n:sha256((Path(__file__).parent/(n+'.py')).read_text(encoding='utf-8').encode()).hexdigest() for n in names})

BOOTSTRAP = r'''import fcntl,hashlib,json,os,pathlib,re,signal,stat,sys,types
try:
 p=json.load(sys.stdin)
 assert set(p)=={'sources','value','request','mode'} and p['mode'] in {'execute','observe','verify'}
 recipe=p['value'].get('functional_recipe');assert recipe in {'owned-simulation-local-v1','owned-keycloak-local-v1'}
 names=('replica_runtime_acceptance','replica_frontend_acceptance','replica_frontend_http','replica_frontend_process','replica_frontend_native')
 if recipe=='owned-keycloak-local-v1':names+=('replica_frontend_oidc_http','replica_frontend_oidc_business','replica_frontend_oidc_native')
 else:assert p['mode']!='verify'
 assert set(p['sources'])==set(names) and sum(len(v.encode()) for v in p['sources'].values())<2*1024*1024
 assert re.fullmatch('[0-9a-f]{32}',p['request']['execution_id'])
 home=pathlib.Path.home().resolve();root=home
 for part in ('.local','state','okf-frontend-acceptance',p['request']['execution_id']):
  root=root/part
  if not root.exists():
   assert p['mode']=='execute';root.mkdir(mode=0o700)
  meta=root.lstat();assert stat.S_ISDIR(meta.st_mode) and not root.is_symlink() and meta.st_uid==os.getuid()
  if part in ('okf-frontend-acceptance',p['request']['execution_id']):assert meta.st_mode&0o077==0
 lock=root/'native.lock';fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 meta=os.fstat(fd);assert stat.S_ISREG(meta.st_mode) and meta.st_nlink==1 and meta.st_uid==os.getuid()
 fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);signal.alarm(120)
 package=types.ModuleType('test_scripts');package.__path__=[];sys.modules['test_scripts']=package
 for name in names:
  module=types.ModuleType('test_scripts.'+name);module.__file__=str(root/(name+'.py'))
  sys.modules[module.__name__]=module;exec(compile(p['sources'][name],name+'.py','exec'),module.__dict__)
 from test_scripts.replica_frontend_http import PrivateFiles
 if recipe=='owned-keycloak-local-v1':
  from test_scripts.replica_frontend_oidc_native import OIDCCoordinator as Coordinator
 else:
  from test_scripts.replica_frontend_native import Coordinator
 files=PrivateFiles(root);capsule={name:hashlib.sha256(p['sources'][name].encode()).hexdigest() for name in names}
 if files.path('capsule.json').exists():assert files.read('capsule.json')==capsule
 else:
  assert p['mode']=='execute';files.once('capsule.json',capsule)
 if recipe=='owned-keycloak-local-v1':
  for name in ('replica_frontend_http','replica_frontend_oidc_http','replica_frontend_oidc_business'):
   filename='module-'+name.replace('_','-')+'.json';source={'source':p['sources'][name]}
   if files.path(filename).exists():assert files.read(filename)==source
   else:
    assert p['mode']=='execute';files.once(filename,source)
 coordinator=Coordinator(root,p['value'])
 result=coordinator.attest(p['request']) if p['mode']=='verify' else coordinator.dispatch(p['request'],observe=p['mode']=='observe')
 print(json.dumps(dict(ok=True,receipt=result)))
except Exception:
 print(json.dumps(dict(ok=False,reason='native_frontend_refused')))
'''


class NativeAdapter:
    def __init__(self, manifest, journal, *, ssh_host=None, known_hosts=None):
        validate_live_inputs(manifest)
        if (manifest.get('deployment_mode') != 'frontend-split'
                or manifest.get('profile') != 'basic'
                or manifest.get('functional_recipe') not in {'owned-simulation-local-v1','owned-keycloak-local-v1'}):
            raise ValueError('Explicit owned frontend recipe required')
        oidc=manifest['functional_recipe']=='owned-keycloak-local-v1'
        if oidc:
            from test_scripts.replica_frontend_oidc_native import validate_oidc_inputs
            validate_oidc_inputs(manifest)
        fields=(*SAFE_FIELDS,*OIDC_FIELDS) if oidc else SAFE_FIELDS
        self.value = json.loads(json.dumps({k: manifest[k] for k in fields}))
        self.manifest_sha256 = digest(manifest)
        self.private = Path(journal).absolute().parent
        Kubernetes(self.value, ssh_host=ssh_host, known_hosts=known_hosts)
        if ssh_host is None and os.name != 'posix': raise ValueError('Native Linux or pinned SSH required')
        self.ssh_host, self.known_hosts = ssh_host, Path(known_hosts).absolute() if known_hosts else None
        self.sources = {name: (Path(__file__).parent/(name+'.py')).read_text(encoding='utf-8') for name in (OIDC_MODULES if oidc else MODULES)}
        compile(BOOTSTRAP, '<fixed-native-bootstrap>', 'exec')

    def bind(self):
        return {'manifest_sha256':self.manifest_sha256, 'value_sha256': digest(self.value), 'capsule_sha256': digest(self.sources),
                'ssh_host': self.ssh_host, 'host_pin_sha256': sha256(self.known_hosts.read_bytes()).hexdigest() if self.known_hosts else None}

    def before(self, stage, state):
        if stage not in STAGES: raise ValueError('Unknown frontend stage')
        self.bind()  # Refresh host-pin readability before publishing an intent.

    def rpc(self, stage, state, mode):
        request = {k: state[k] for k in ('execution_id','code_sha256','binding_sha256')}
        if mode=='verify':
            if stage!='final' or state['active'] is not None:raise ValueError('Only completed native execution can be verified')
            intent=state['completed']['final']['intent_id']
        else:
            if state['active']['stage'] != stage: raise ValueError('Native active stage differs')
            intent=state['active']['intent_id']
        request.update(stage=stage, intent_id=intent);validate_request(request)
        payload = {'sources': self.sources, 'value': self.value, 'request': request, 'mode': mode}
        isolated=['-I'] if self.value['functional_recipe']=='owned-keycloak-local-v1' else []
        argv = ['python3',*isolated,'-c',BOOTSTRAP]
        if self.ssh_host:
            argv = ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes',
                    '-o','UserKnownHostsFile='+str(self.known_hosts),self.ssh_host,shlex.join(argv)]
        try:
            result = subprocess.run(argv,input=json.dumps(payload),text=True,capture_output=True,timeout=135)
            if result.returncode or len(result.stdout.encode()) > 1024*1024:
                raise ValueError('Native transport failed')
            value = json.loads(result.stdout)
            if set(value) != {'ok','receipt'} or value['ok'] is not True: raise ValueError('Native refused')
            return value['receipt']
        except Exception:
            raise ValueError('Native frontend response uncertain; observe the same intent') from None

    def execute(self, stage, state): return self.rpc(stage,state,'execute')
    def observe(self, stage, state): return self.rpc(stage,state,'observe')

    def verify_completed(self,state):
        if (self.value['functional_recipe']!='owned-keycloak-local-v1' or state['active'] is not None
                or set(state['completed'])!=set(STAGES)):
            raise ValueError('Complete owned OIDC history required')
        actual=self.rpc('final',state,'verify')
        expected=dict(recipe='owned-keycloak-local-v1',**{k:state[k] for k in ('execution_id','code_sha256','binding_sha256')},
            capsule_sha256=digest({n:sha256(s.encode()).hexdigest() for n,s in self.sources.items()}),
            receipts_sha256={s:state['completed'][s]['receipt_sha256'] for s in STAGES})
        if actual!=expected:raise ValueError('Native OIDC completion proof differs')
        return actual


def execute_frontend(manifest,journal,stage,*,apply=False,observe=False,ssh_host=None,known_hosts=None):
    from test_scripts.replica_frontend_run import FrontendRun
    adapter=NativeAdapter(manifest,journal,ssh_host=ssh_host,known_hosts=known_hosts)
    run=FrontendRun(journal,adapter)
    result=run.execute(stage,apply=apply,observe=observe)
    if result['selected_case_passed'] and manifest['functional_recipe']=='owned-keycloak-local-v1':
        result['provider_evidence']=adapter.verify_completed(run.state)
    return result
