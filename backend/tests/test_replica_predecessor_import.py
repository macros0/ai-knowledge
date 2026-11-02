"""Old provider imports retain the authenticated S4 fence in a new stopped target."""
import copy
import importlib.util
import json
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from app.deployment.replica_backup import BackupPlan, canonical
from tests.test_replica_backup_coordinator import config
from tests.test_replica_predecessor_backup import previous_plan
from tests.test_replica_predecessor_stop import sql_snapshot
from tests.test_replica_provider_stop import fixture


def tool():
    path=Path(__file__).resolve().parents[2]/'scripts/kubernetes/replica_predecessor_restore.py'
    assert path.is_file(),'Explicit predecessor provider import is missing'
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('predecessor_import_test',path)
    value=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def imported():
    source,snapshot=sql_snapshot()
    value,_=previous_plan()
    value['source'].update(namespace=source['namespace'],installation_id=source['installation_id'])
    value['model_vector_sha256']=sha256(canonical('2'*64)).hexdigest()
    proof=value['source']['stop_proof']
    proof['runtime_fingerprint_sha256']=value['model_vector_sha256']
    scope={k:value['source'][k] for k in ('installation_id','namespace','operation_id')}
    scope.update(namespace_uid=proof['namespace_uid'],schema_revision=value['schema_revision'],
        runtime_fingerprint_sha256=value['model_vector_sha256'])
    proof['source_scope_sha256']=sha256(canonical(scope)).hexdigest()
    plan=BackupPlan.model_validate(value)
    target=config()
    run='restore01'
    target.update(run_id=run,namespace='replica-test-'+run,installation_id='8'*32,
        database='replica_test_'+run,collection='replica_test_'+run,bucket='replica-test-'+run,
        prefix='replica_test/'+run+'/application/')
    snapshot['database']=target['database']
    snapshot['controls'][0]['maintenance_token']=plan.backup_id
    snapshot['fence_audit_events']=1
    snapshot['counts']={'documents':2,'concepts':2,'chunks':2,'blobs':2,'work_items':3,'audit_events':1}
    snapshot['artifacts']=[{'object_id':e.id,'store_id':source['store_id'],'key':'objects/'+e.id,
        'sha256':e.expected_sha256,'size':e.expected_size} for e in plan.inventory if e.component=='artifacts']
    return source,target,plan,snapshot


def test_imported_old_metadata_is_verified_without_rewriting_source_identity():
    source,target,plan,snapshot=imported()
    before=copy.deepcopy(snapshot)
    result=tool().validate_imported_predecessor(snapshot,source,target,plan)
    assert result['counts']=={'documents':2,'concepts':2,'chunks':2,'blobs':2,'work_items':3,'audit_events':1}
    assert {a['object_id'] for a in result['artifacts']}=={'0'*31+'3','0'*31+'4'}
    assert snapshot==before
    assert snapshot['controls'][0]['active_descriptor']['bucket']==source['namespace']!=target['namespace']


def test_import_accepts_the_complete_verified_tls_descriptor_without_rewriting_it():
    source,target,plan,snapshot=imported()
    descriptor=snapshot['controls'][0]['active_descriptor']
    descriptor.update(region='us-east-1',addressing_style='path',
        ca_bundle='/etc/okf/trust/ca-bundle.crt',allow_http=False)
    before=copy.deepcopy(descriptor)
    tool().validate_imported_predecessor(snapshot,source,target,plan)
    assert descriptor==before


@pytest.mark.parametrize('fault',['database','schema','maintenance_table','epoch_column','token','fingerprint',
    'installation','bucket','prefix','missing_audit','duplicate_audit','counts','running_work','object_sha',
    'transport_http','transport_ca','descriptor_extra','current_schema_plan'])
def test_uncertain_or_modern_import_cannot_proceed_to_provider_writes(fault):
    source,target,plan,snapshot=imported()
    if fault=='database':snapshot['database']=source['database']
    elif fault=='schema':snapshot['schema_revisions']=['rpl05_maintenance_epoch']
    elif fault=='maintenance_table':snapshot['maintenance_table']=True
    elif fault=='epoch_column':snapshot['work_maintenance_epoch']=True
    elif fault=='token':snapshot['controls'][0]['maintenance_token']='9'*32
    elif fault=='fingerprint':snapshot['identities'][0]['fingerprint']='9'*64
    elif fault=='installation':snapshot['controls'][0]['installation_id']=target['installation_id']
    elif fault=='bucket':snapshot['controls'][0]['active_descriptor']['bucket']=target['bucket']
    elif fault=='prefix':snapshot['controls'][0]['active_descriptor']['prefix']=target['prefix']
    elif fault=='missing_audit':snapshot['fence_audit_events']=0
    elif fault=='duplicate_audit':snapshot['fence_audit_events']=2
    elif fault=='counts':snapshot['counts']['documents']=1
    elif fault=='running_work':snapshot['active_work']=1
    elif fault=='object_sha':snapshot['artifacts'][0]['sha256']='9'*64
    elif fault=='transport_http':snapshot['controls'][0]['active_descriptor']['allow_http']=True
    elif fault=='transport_ca':snapshot['controls'][0]['active_descriptor']['ca_bundle']='/unexpected/roots.crt'
    elif fault=='descriptor_extra':snapshot['controls'][0]['active_descriptor']['root']='/unexpected/local-store'
    else:
        from tests.test_replica_backup import fixture_plan
        plan,_=fixture_plan()
    with pytest.raises(ValueError):
        tool().validate_imported_predecessor(snapshot,source,target,plan)


def stopped_source():
    value,binding,items,nodes=copy.deepcopy(fixture())
    items=[o for o in items if o['kind']!='Pod']
    for role in ('postgres','qdrant','artifacts'):
        items.append({'kind':'StatefulSet','metadata':{'name':'storage-'+role,'uid':'uid-'+role,
            'generation':2,'labels':{'app.kubernetes.io/instance':'storage'}},
            'spec':{'replicas':0},'status':{'observedGeneration':2}})
    return value,binding,items,nodes,[]


def test_source_off_proof_uses_fresh_nodes_and_cri_without_starting_storage():
    value,binding,items,nodes,containers=stopped_source()
    result=tool().predecessor_offline_observation(items,nodes,containers,value,binding)
    nodes[0]['status']['conditions'][0]['lastHeartbeatTime']='different'
    assert tool().predecessor_offline_observation(items,nodes,containers,value,binding)==result


@pytest.mark.parametrize('fault',['writer','storage','pod','orphan','node_uid','node_down','hpa','job','schedule','endpoint','namespace'])
def test_source_must_be_physically_unavailable_throughout_import(fault):
    value,binding,items,nodes,containers=stopped_source()
    if fault in {'writer','storage'}:
        next(o for o in items if o['kind']==('Deployment' if fault=='writer' else 'StatefulSet'))['spec']['replicas']=1
    elif fault=='pod':items.append({'kind':'Pod','metadata':{'name':'unknown'},'status':{'phase':'Running'}})
    elif fault=='orphan':containers.append({'state':'CONTAINER_RUNNING','labels':{'io.kubernetes.pod.namespace':value['namespace']}})
    elif fault=='node_uid':nodes[0]['metadata']['uid']='different'
    elif fault=='node_down':nodes[0]['status']['conditions'][0]['status']='False'
    elif fault=='hpa':items.append({'kind':'HorizontalPodAutoscaler'})
    elif fault=='job':items.append({'kind':'Job','status':{'active':1}})
    elif fault=='schedule':items.append({'kind':'CronJob','spec':{'suspend':False}})
    elif fault=='endpoint':items.append({'kind':'EndpointSlice','endpoints':[{'addresses':['10.0.0.1']}]})
    else:items[0]['metadata']['uid']='different'
    with pytest.raises(ValueError):
        tool().predecessor_offline_observation(items,nodes,containers,value,binding)


def test_import_cli_refuses_foreign_scope_before_namespace_changes_and_redacts_secrets(tmp_path,capsys):
    source,target,_plan,_snapshot=imported()
    target['namespace']='default'
    canary='PRIVATE_PREDECESSOR_IMPORT_CANARY_8416'
    target['credentials']={'POSTGRES_PASSWORD':canary}
    config_file=tmp_path/'target.json';config_file.write_text(json.dumps(target))
    source_file=tmp_path/'source.json';source_file.write_text(json.dumps(source))
    args=['--config',str(config_file),'--source-config',str(source_file),
        '--namespace-uid','77777777-7777-4777-8777-777777777777',
        '--source-namespace-uid','88888888-8888-4888-8888-888888888888']
    for name in ('source-operation-journal','archive','key-file','report','operation-journal'):
        args.extend(['--'+name,str(tmp_path/name)])
    assert tool().main(args)==2
    output=capsys.readouterr()
    assert canary not in output.out+output.err
    assert not (tmp_path/'operation-journal').exists() and not (tmp_path/'report').exists()
