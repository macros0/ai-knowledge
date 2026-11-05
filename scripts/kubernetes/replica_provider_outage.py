"""Durable provider stages and exact owned-policy actions.

A live caller must supply a separately admitted native adapter, never a manifest
import path. A journal or policy receipt alone is not native provider acceptance.
"""
from __future__ import annotations

import copy
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
import traceback
from uuid import uuid4

from filelock import FileLock

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_scripts.replica_frontend_http import PrivateFiles
from test_scripts.replica_provider_faults import fault_policy, require, uid
from replica_runtime_run import digest, now, read_json, write_json

STAGES=('preflight','baseline','qdrant.gate','qdrant.submit','qdrant.inject','qdrant.observe','qdrant.heal','qdrant.verify',
    'model.inject','model.submit','model.observe','model.heal','model.verify','final')
READ_ONLY={'preflight','qdrant.observe','qdrant.verify','model.observe','model.verify'}


def code_digest():
    paths=[*(ROOT/'scripts/kubernetes').glob('replica_*.py'),
        *(ROOT/'backend/test_scripts').glob('replica_*.py'),
        ROOT/'backend/app/deployment/replica_backup.py',ROOT/'backend/app/services/diagnostics/paths.py']
    return digest({p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in paths})


def hash_value(value):
    return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None


class ProviderRun:
    """One fixed ordered history; an active stage can only be observed."""
    def __init__(self,path,adapter,*,code_sha256=None):
        from app.services.diagnostics.paths import assert_no_links
        self.path,self.adapter=Path(path).absolute(),adapter
        require(self.path.parent==Path(adapter.private).resolve() and self.path.parent.is_dir())
        assert_no_links(self.path,self.path.parent)
        self.code=code_sha256 or code_digest();require(hash_value(self.code))
        exists=self.path.exists()
        self.state=read_json(self.path) if exists else dict(version=1,execution_id=uuid4().hex,
            code_sha256=self.code,binding_sha256=None,admission=None,created_at=now(),completed={},active=None,observations=[])
        self.validate()
        try:binding=adapter.bind(self.state)
        except Exception:raise ValueError('Owned provider source admission refused') from None
        selected=digest(binding)
        require(not exists or self.state['binding_sha256']==selected)
        self.state.update(binding_sha256=selected,admission=binding.get('admission'))

    def validate(self):
        s=self.state
        require(type(s.get('version')) is int and s['version']==1 and s.get('code_sha256')==self.code
            and isinstance(s.get('execution_id'),str) and re.fullmatch('[0-9a-f]{32}',s['execution_id']) is not None
            and isinstance(s.get('completed'),dict) and isinstance(s.get('observations'),list) and len(s['observations'])<=4096)
        completed=s['completed'];require(set(completed)==set(STAGES[:len(completed)]))
        require(s.get('binding_sha256') is None and not completed and s.get('active') is None or hash_value(s.get('binding_sha256')))
        previous=None;intents=set()
        for stage in STAGES[:len(completed)]:
            row=completed[stage]
            require(isinstance(row,dict) and row.get('stage')==stage and row.get('previous_sha256')==previous
                and isinstance(row.get('intent_id'),str) and re.fullmatch('[0-9a-f]{32}',row['intent_id']) is not None
                and row['intent_id'] not in intents and isinstance(row.get('outcome'),dict) and row['outcome'].get('passed') is True
                and all(row.get(k)==s[k] for k in ('execution_id','code_sha256','binding_sha256')))
            require(row.get('receipt_sha256')==digest({k:v for k,v in row.items() if k!='receipt_sha256'}))
            previous=row['receipt_sha256'];intents.add(row['intent_id'])
        active=s.get('active')
        if active is not None:
            require(isinstance(active,dict) and len(completed)<len(STAGES) and active.get('stage')==STAGES[len(completed)]
                and isinstance(active.get('intent_id'),str) and re.fullmatch('[0-9a-f]{32}',active['intent_id']) is not None
                and active['intent_id'] not in intents)

    def save(self):write_json(self.path,self.state)

    def finish(self,stage,outcome):
        require(isinstance(outcome,dict) and outcome.get('passed') is True)
        active=self.state['active'];index=STAGES.index(stage);finished=now()
        row=dict(stage=stage,intent_id=active['intent_id'],started_at=active['started_at'],finished_at=finished,
            seconds=max(0,(datetime.fromisoformat(finished)-datetime.fromisoformat(active['started_at'])).total_seconds()),
            previous_sha256=self.state['completed'][STAGES[index-1]]['receipt_sha256'] if index else None,
            outcome=copy.deepcopy(outcome),**{k:self.state[k] for k in ('execution_id','code_sha256','binding_sha256')})
        row['receipt_sha256']=digest(row)
        candidate=copy.deepcopy(self.state);candidate['completed'][stage]=row;candidate['active']=None
        write_json(self.path,candidate);self.state=candidate

    def pending(self,stage,outcome):
        require(len(self.state['observations'])<4096)
        self.state['observations'].append(dict(stage=stage,intent_id=self.state['active']['intent_id'],
            status='pending' if outcome is None else 'failed',observed_at=now(),result_sha256=digest(outcome)))
        self.save()

    def execute(self,stage,*,apply=False,observe=False):
        from app.services.diagnostics.paths import assert_no_links
        require(stage in STAGES and type(apply) is bool and type(observe) is bool and not(apply and observe))
        if stage not in READ_ONLY and not(apply or observe):raise ValueError('Explicit provider apply required')
        lock=self.path.with_name(self.path.name+'.lock');assert_no_links(lock,self.path.parent)
        with FileLock(lock,timeout=5):
            require(not self.path.exists() or read_json(self.path)==self.state)
            self.validate()
            if observe and self.state['active'] is None and stage not in self.state['completed']:
                raise ValueError('No started provider intent to observe')
            if stage not in self.state['completed']:
                require(stage==STAGES[len(self.state['completed'])])
            try:self.adapter.before(stage,self.state)
            except Exception:raise ValueError('Owned provider live precondition refused') from None
            if stage in self.state['completed']:return self.report()
            active=self.state['active'] is not None
            if not active:
                self.state['active']=dict(stage=stage,intent_id=uuid4().hex,started_at=now());self.save()
            try:
                # Even apply=True cannot replay an already started action.
                outcome=self.adapter.observe(stage,self.state) if active else self.adapter.execute(stage,self.state)
                if isinstance(outcome,dict) and outcome.get('passed') is True:self.finish(stage,outcome)
                else:self.pending(stage,outcome)
            except Exception as exc:
                self.state['active']['error_type']=type(exc).__name__
                self.state['active']['locations']=[Path(f.filename).name+':'+str(f.lineno) for f in traceback.extract_tb(exc.__traceback__)[-3:]]
                try:self.pending(stage,{'error_type':type(exc).__name__})
                except OSError:pass  # A durable writing marker blocks a false ACK.
                raise ValueError('Owned provider outcome uncertain; observe the same intent') from None
        return self.report()

    def report(self):
        self.validate();s=self.state;active=s['active'];failed=int(active is not None and 'error_type' in active)
        checks=[dict(name='provider.'+stage,status='passed' if stage in s['completed'] else 'pending',
            **({'evidence_sha256':s['completed'][stage]['receipt_sha256']} if stage in s['completed'] else {})) for stage in STAGES]
        if failed:checks[len(s['completed'])]['status']='failed'
        return dict(scope='owned-basic-managed-s3-dependencies',execution_id=s['execution_id'],code_sha256=self.code,
            binding_sha256=s['binding_sha256'],checks=checks,failed=failed,skipped_required=len(STAGES)-len(s['completed']),
            selected_provider_case_passed=len(s['completed'])==len(STAGES) and active is None,
            qualification_complete=False,certification=None,production_schedule='disabled')


class PolicyActions:
    """Guarded create/UID DELETE primitives, with read-only lost-ACK adoption."""
    def __init__(self,kube,value,directory,operation,guard):
        fault_policy(value,'qdrant',operation);require(callable(guard))
        self.kube,self.value,self.operation,self.guard=kube,copy.deepcopy(value),operation,guard
        self.files=PrivateFiles(directory)

    def check(self):
        require(self.guard() is not False)
        binding=dict(operation=self.operation,**{k:self.value[k] for k in ('recipe','profile','deployment_mode',
            'artifact_backend','run_id','namespace','namespace_uid','application_release','storage_release')})
        if self.files.path('policy-binding.json').exists():require(self.files.read('policy-binding.json')==binding)
        else:self.files.once('policy-binding.json',binding)

    def expected(self,provider):return fault_policy(self.value,provider,self.operation)

    def fetch(self,provider):
        expected=self.expected(provider)
        raw=self.kube.run('get','ciliumnetworkpolicy',expected['metadata']['name'],'--ignore-not-found','-o','json')
        return json.loads(raw) if raw.strip() else None

    def admit(self,provider,obj):
        expected=self.expected(provider);meta=obj.get('metadata',{})
        require(obj.get('kind')=='CiliumNetworkPolicy' and obj.get('apiVersion')=='cilium.io/v2'
            and obj.get('spec')==expected['spec'] and uid(meta.get('uid'))
            and meta.get('name')==expected['metadata']['name'] and meta.get('namespace')==self.value['namespace']
            and all(meta.get('labels',{}).get(k)==v for k,v in expected['metadata']['labels'].items())
            and not meta.get('deletionTimestamp') and not meta.get('ownerReferences'))
        return dict(passed=True,evidence=dict(provider=provider,policy_uid=meta['uid'],policy_sha256=digest(obj['spec'])))

    def create(self,provider):
        self.check();expected=self.expected(provider);name=provider+'.policy.intent.json'
        if self.files.path(name).exists():return self.observe_create(provider)
        require(self.fetch(provider) is None)
        self.files.once(name,expected)
        result=self.admit(provider,json.loads(self.kube.run('create','-f','-','-o','json',source=json.dumps(expected))))
        self.files.once(provider+'.policy.created.json',result)
        return result

    def observe_create(self,provider):
        self.check();require(self.files.read(provider+'.policy.intent.json')==self.expected(provider))
        obj=self.fetch(provider)
        if obj is None:return None
        result=self.admit(provider,obj);path=provider+'.policy.created.json'
        if self.files.path(path).exists():require(self.files.read(path)==result)
        else:self.files.once(path,result)
        return result

    def heal(self,provider):
        self.check();name=provider+'.heal.intent.json'
        if self.files.path(name).exists():return self.observe_heal(provider)
        created=self.files.read(provider+'.policy.created.json');obj=self.fetch(provider)
        require(obj is not None and self.admit(provider,obj)==created)
        options=dict(apiVersion='v1',kind='DeleteOptions',preconditions={'uid':created['evidence']['policy_uid']})
        self.files.once(name,dict(policy=created,delete_options=options))
        uri='/apis/cilium.io/v2/namespaces/'+self.value['namespace']+'/ciliumnetworkpolicies/'+obj['metadata']['name']
        response=json.loads(self.kube.run('delete','--raw',uri,'-f','-',source=json.dumps(options)))
        require(response.get('kind')=='Status' and response.get('status')=='Success'
            or response.get('kind')=='CiliumNetworkPolicy' and response.get('metadata',{}).get('uid')==created['evidence']['policy_uid'])
        return self.observe_heal(provider)

    def observe_heal(self,provider):
        self.check();intent=self.files.read(provider+'.heal.intent.json');created=self.files.read(provider+'.policy.created.json')
        require(intent==dict(policy=created,delete_options=dict(apiVersion='v1',kind='DeleteOptions',
            preconditions={'uid':created['evidence']['policy_uid']})))
        obj=self.fetch(provider)
        if obj is not None:
            require(self.admit(provider,obj)==created)
            return None
        result=dict(passed=True,evidence={**created['evidence'],'policy_absent':True})
        path=provider+'.policy.healed.json'
        if self.files.path(path).exists():require(self.files.read(path)==result)
        else:self.files.once(path,result)
        return result
