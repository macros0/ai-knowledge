"""Partition only one bound worker; never weaken the queue or model quarantine."""
import copy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_postgres_partition.py"
    assert path.is_file(), "Actual PostgreSQL partition coordinator is missing"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("postgres_partition_test", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_deny_policy_selects_only_the_owned_fault_label_and_postgres_port():
    tool = module()
    v = {"namespace": "replica-test-fixture01", "run_id": "fixture01", "application_release": "application", "storage_release": "storage"}
    result = tool.partition_policy(v, "a" * 32)
    assert result["metadata"]["namespace"] == v["namespace"]
    assert result["spec"]["endpointSelector"]["matchLabels"] == {
        "okf-role": "worker", "app.kubernetes.io/instance": "application", "okf-pg-partition": "a" * 32}
    assert result["spec"]["egressDeny"] == [{"toEndpoints": [{"matchLabels": {
        "app.kubernetes.io/instance": "storage", "okf-storage-component": "postgres"}}],
        "toPorts": [{"ports": [{"port": "5432", "protocol": "TCP"}]}]}]
    with pytest.raises(ValueError):
        tool.partition_policy({**v, "namespace": "production"}, "a" * 32)


def test_traceable_startup_preserves_image_configuration_and_worker_entrypoint():
    tool = module()
    value = {"spec": {"replicas": 2, "template": {"spec": {"containers": [{"name": "worker",
        "command": ["python", "-m", "app.worker"], "image": "owned-image", "envFrom": [{"secretRef": {"name": "private"}}]}]}}}}
    before = copy.deepcopy(value)
    result = tool.identified_worker(value)
    assert value == before
    old = before["spec"]["template"]["spec"]["containers"][0]
    new = result["spec"]["template"]["spec"]["containers"][0]
    assert new["image"] == old["image"] and new["envFrom"] == old["envFrom"]
    assert "main()" in new["command"][-1] and "runtime.uuid4" in new["command"][-1]
    assert new["env"][-1]["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.uid"


@pytest.mark.parametrize("change", ["fence", "owner", "publication", "generation"])
def test_stale_return_requires_reclaim_without_late_publication(change):
    tool = module()
    original = {"id": "work", "owner_id": "old", "fence": 1}
    reclaimed = {"id": "work", "owner_id": "new", "fence": 2, "status": "running"}
    before = {"active": None, "candidate": "new-generation", "active_count": 0}
    after = dict(before)
    if change == "fence":
        reclaimed["fence"] = 1
    elif change == "owner":
        reclaimed["owner_id"] = "old"
    elif change == "publication":
        after["active_count"] = 1
    else:
        after["candidate"] = "old-generation"
    with pytest.raises(ValueError):
        tool.assert_stale_return(original, reclaimed, before, after)


@pytest.mark.parametrize("change", [{"postgres": True}, {"qdrant": False}, {"model": False}, {"live": 503}])
def test_delayed_observation_requires_the_same_postgres_only_fault(change):
    tool = module()
    net = {"postgres": False, "qdrant": True, "model": True, "live": 200}
    with pytest.raises(ValueError):
        tool.qualify_partition({**net, **change}, [{"status": "running"}], 0)


def test_provider_finish_does_not_make_unfinished_partitioned_work_an_idle_test():
    tool = module()
    net = {"postgres": False, "qdrant": True, "model": True, "live": 200}
    tool.qualify_partition(net, [{"status": "running"}], 0)
    with pytest.raises(ValueError):
        tool.qualify_partition(net, [{"status": "succeeded"}], 0)


@pytest.mark.parametrize("fault", ["old_work", "old_active", "duplicate", "running"])
def test_confirmed_reply_recovery_requires_only_the_new_generation_to_publish(fault):
    tool = module()
    work = {"id": "work", "fence": 2, "status": "succeeded"}
    doc = {"active": "new", "active_count": 1, "generations": [
        {"id": "old", "phase": "abandoned", "work_fence": 1}, {"id": "new", "phase": "active", "work_fence": 2}]}
    if fault == "old_work":
        work["fence"] = 1
    elif fault == "old_active":
        doc["generations"][0]["phase"] = "active"
    elif fault == "duplicate":
        doc["active_count"] = 2
    else:
        work["status"] = "running"
    with pytest.raises(ValueError):
        tool.assert_confirmed_return({"id": "work", "fence": 1}, {"fence": 2}, work, doc)


def returned_fixture(monkeypatch, *, proof=None, loss=0, useful=True):
    tool=module();op=object.__new__(tool.PostgresPartition);calls=[]
    old={'metadata':{'uid':'old-pod','name':'old-worker'}}
    before={'okf_replica_process_claims_total':10,'okf_replica_process_completed_total':9,
        'okf_replica_process_lease_losses_total':0}
    metrics={**before,'okf_replica_process_claims_total':12 if useful else 10,
        'okf_replica_process_completed_total':11 if useful else 9,'okf_replica_process_lease_losses_total':loss}
    lease={'id':'slot','resource':'llm','owner_id':'provider-owner','fence':3,'release_required':True}
    doc={'active':None,'candidate':'new-generation','candidate_work_fence':2,'active_count':0}
    op.config={'storage_release':'storage'};op.value={'namespace':'replica-test-fixture01'}
    op.journal={'phase':'healing','selected_worker':old,'healthy_worker':{'metadata':{'name':'healthy-worker'}},'metrics_before':{'old-pod':before},
        'original_lease':lease,'provider_before':{'started':1},
        'original_work':{'id':'work','owner_id':'old','fence':1},
        'reclaimed_work':{'id':'work','owner_id':'new','fence':2,'status':'running'},'before_return':doc}
    def save(**values):op.journal.update(values)
    op.save=save;op.preflight=lambda phase:calls.append('preflight:'+phase)
    op.pod=lambda expected:old;op.delete_policy=lambda:calls.append('observe-owned-policy-absence')
    op.state=lambda:{'leases':[lease],'provider':{'started':1,'active':0},'work':[{'id':'work','status':'running'}]}
    op.doc_state=lambda:copy.deepcopy(doc)
    def python(pod,source):
        if source==tool.connectivity_source(op.config):return {'postgres':True,'qdrant':True,'model':True,'live':200,'ready':200}
        if source==tool.METRICS_SOURCE:return metrics
        if 'prepare_document_attempt' in source:
            assert "'owner_id': 'old'" in source and "'fence': 1" in source
            assert 'raise ProbeRollback()' in source
            calls.append('original-token-rollback-probe')
            return proof if proof is not None else {'old_token_rejected':True,'probe_transaction_rolled_back':True}
        if 'acquire_model_budget' in source:
            calls.append('busy-api:'+pod);return {'busy':True}
        raise AssertionError('Unexpected action')
    op.kube=SimpleNamespace(python=python,inventory=lambda:[])
    monkeypatch.setattr(tool,'role_pods',lambda objects,role:[{'metadata':{'name':'api1'}},{'metadata':{'name':'api2'}}])
    monkeypatch.setattr(tool,'validate_inventory',lambda objects,value:None)
    times=iter([0,181,182,183]);monkeypatch.setattr(tool.time,'monotonic',lambda:next(times))
    monkeypatch.setattr(tool.time,'sleep',lambda _:None)
    return op,calls


@pytest.mark.parametrize('loss',[0,1])
def test_returned_process_can_prove_quarantine_with_exact_sql_token_not_a_counter(monkeypatch,loss):
    op,calls=returned_fixture(monkeypatch,loss=loss)
    op.heal()
    assert op.journal['phase']=='quarantine-proven'
    assert op.journal['lease_losses_delta']==loss
    assert op.journal['stale_token_probe']=={'old_token_rejected':True,'probe_transaction_rolled_back':True}
    assert calls.count('original-token-rollback-probe')==1
    assert calls[-2:]==['busy-api:api1','busy-api:api2']
    assert op.journal['original_lease']['owner_id']=='provider-owner'


@pytest.mark.parametrize('proof',[{'old_token_rejected':False,'probe_transaction_rolled_back':True},
    {'old_token_rejected':True,'probe_transaction_rolled_back':False},
    {'old_token_rejected':1,'probe_transaction_rolled_back':True},{}])
def test_quarantine_rejects_an_accepted_or_unproven_stale_token_without_slot_repair(monkeypatch,proof):
    op,calls=returned_fixture(monkeypatch,proof=proof)
    with pytest.raises(ValueError):op.heal()
    assert op.journal['phase']=='healing'
    assert calls.count('original-token-rollback-probe')==1
    assert not any(v.startswith('busy-api:') for v in calls)
    assert op.journal['original_lease']['owner_id']=='provider-owner'


def test_a_counterless_process_with_no_useful_return_is_not_a_quarantine_proof(monkeypatch):
    op,calls=returned_fixture(monkeypatch,useful=False)
    with pytest.raises(ValueError):op.heal()
    assert op.journal['phase']=='healing' and 'original-token-rollback-probe' not in calls


def report_fixture(monkeypatch,change=None):
    tool=module();op=object.__new__(tool.PostgresPartition)
    worker={'metadata':{'uid':'worker-uid'},'spec':{'replicas':2,'template':{'spec':{
        'containers':[{'command':['python','-m','app.worker']} ]}}}}
    current=copy.deepcopy(worker);policies=[];owners=0;fingerprint='a'*64
    if change=='command':current['spec']['template']['spec']['containers'][0]['command']=['python','instrumented.py']
    if change=='uid':current['metadata']['uid']='foreign-worker'
    if change=='policy':policies=[{'metadata':{'name':'leftover-policy'}}]
    if change=='slot':owners=1
    if change=='identity':fingerprint='b'*64
    op.fingerprint='a'*64;op.value={'namespace':'replica-test-fixture01'}
    op.journal={'operation_id':'c'*32,'epoch':5,'old_deployments':{'worker':worker},
        'policy':{'metadata':{'uid':'policy-uid'},'spec':{}},'network_sample':{'active':0},
        'selected_worker':{'metadata':{'uid':'old-pod'}},'reclaimed_work':{'fence':2},'lease_losses_delta':0,
        'proof_digest':'d'*64,'stale_token_probe':{'old_token_rejected':True,'probe_transaction_rolled_back':True}}
    def run(*args,**kwargs):
        if args==('get','deploy','worker','-o','json'):return __import__('json').dumps(current)
        if args==('get','cnp','-o','json'):return __import__('json').dumps({'items':policies})
        raise AssertionError(args)
    op.kube=SimpleNamespace(run=run)
    probe=SimpleNamespace(identity={'fingerprint':fingerprint},state=lambda:{'model_owners':owners,
        'running_documents':0,'running_chats':0,'provider':{'active':0}})
    monkeypatch.setattr(tool,'FaultProbe',lambda kube,value:probe)
    return op,{'crashed_worker_containers':0,'provider_active_after_crash':1}


def test_uncertain_pg_report_exposes_verified_canonical_consumer_fields(monkeypatch):
    op,public=report_fixture(monkeypatch)
    report=op.report_result(public)
    assert report['fault_operation_id']=='c'*32 and report['maintenance_epoch']==5
    assert report['policy_removed'] is True and report['normal_entrypoint_restored'] is True
    assert report['capacity']['owned_slots_at_finish']==0
    assert report['capacity']['completion']=='audited-stopped-provider-reconciliation'
    assert report['capacity']['offline_reconciliation_required'] is True


@pytest.mark.parametrize('change',['command','uid','policy','slot','identity'])
def test_uncertain_pg_report_never_claims_unobserved_final_recovery_facts(monkeypatch,change):
    op,public=report_fixture(monkeypatch,change)
    with pytest.raises(ValueError):op.report_result(public)
