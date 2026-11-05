"""The selected NAS backup has client provenance, never server filesystem proof."""
import copy
from hashlib import sha256
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.deployment.bootstrap import canonical
from app.deployment.replica_backup import BackupPlan
from tests.test_replica_backup import fixture_plan

NAMESPACE='replica-test-nast20261105'
CLAIM='5d4fec89-4edd-4e1a-8e33-40d96a609065'
RECIPE='synology-ds211-dsm6.2.4-25556-u8-nfs4.0-csi4.13.6-training-v1'


def binding():
    root=Path(__file__).resolve().parents[2]
    source=copy.deepcopy(json.loads((root/'docs/superpowers/reports/2026-11-04-replica-synology-independent-backup.json').read_bytes())['source_binding'])
    plan,_=fixture_plan();value=plan.model_dump(mode='json')
    value['source']['namespace']=source['namespace']
    value['storage'][2].update(provider='external',recipe=RECIPE,scope_sha256=sha256(canonical(source)).hexdigest())
    return BackupPlan.model_validate(value),source


def observed():
    export='/volume1/Vitaliy/okf-syn-'+NAMESPACE+'-artifacts-pvc-'+CLAIM
    return dict(root='/artifacts',mountinfo=f'20 10 0:50 / /artifacts rw,relatime - nfs4 10.10.1.5:{export} rw,vers=4.0,hard,proto=tcp,sec=sys,addr=10.10.1.5\n',
        major=0,minor=50,inode=17000,uid=0,gid=100,mode=0o42770)


def modules(monkeypatch):
    nas=importlib.import_module('app.deployment.nas_restore')
    rwx=importlib.import_module('app.deployment.rwx_restore')
    monkeypatch.setattr(nas,'_wire_identity',lambda:(1031,100))
    return nas,rwx


def settings():
    return SimpleNamespace(artifact_backend='rwx',artifact_provider='external',artifact_root='/artifacts',deployment_namespace=NAMESPACE)


def test_new_recipe_accepts_actual_client_binding_and_disjoint_native_nfs40_target(monkeypatch):
    nas,rwx=modules(monkeypatch);plan,source=binding();actual=observed()
    monkeypatch.setattr(rwx,'_observe_nfs_root',lambda root:copy.deepcopy(actual))
    proof=rwx.verify_rwx_relocation(settings(),plan,source)
    assert proof['source_scope_sha256']==plan.storage[2].scope_sha256
    assert proof['namespace']==NAMESPACE and proof['client_directory_inode']==17000
    assert proof['nfs_source'].endswith(CLAIM) and proof['nfs_version']=='4.0'
    assert proof['wire_uid']==1031 and proof['wire_gid']==100
    assert proof['server_filesystem_identity_claimed'] is False
    assert 'server_directory_inode' not in proof and 'server_filesystem_id' not in proof
    actual.update(major=1,minor=77);actual['mountinfo']=actual['mountinfo'].replace('20 10 0:50','200 100 1:77')
    assert rwx.verify_rwx_relocation(settings(),plan,source)==proof


@pytest.mark.parametrize('fault',['tamper','missing','extra','recipe','provider','namespace','claim','handle','reader-image',
    'reader-code','server-proof','wire-uid','readonly','owner','mode','bool-inode','server','share'])
def test_untrusted_nas_binding_refuses_before_native_observation(monkeypatch,fault):
    nas,rwx=modules(monkeypatch);plan,source=binding();value=plan.model_dump(mode='json')
    if fault=='tamper':source['client_directory_inode']+=1
    elif fault=='missing':source.pop('reader_pod_uid')
    elif fault=='extra':source['approved']=True
    elif fault=='recipe':value['storage'][2]['recipe']='unqualified-nas'
    elif fault=='provider':value['storage'][2]['provider']='managed'
    elif fault=='namespace':source['namespace']='replica-test-other'
    elif fault=='claim':source['claim_uid']='00000000-0000-0000-0000-000000000000'
    elif fault=='handle':source['volume_handle']+='-alias'
    elif fault=='reader-image':source['reader_image_ref']='image:latest'
    elif fault=='reader-code':source['reader_source_sha256']='unknown'
    elif fault=='server-proof':source['server_filesystem_identity_claimed']=True
    elif fault=='wire-uid':source['wire_uid']=1000
    elif fault=='readonly':source['readonly']=False
    elif fault=='owner':source['client_directory_uid']=1031
    elif fault=='mode':source['client_directory_mode']=0o2777
    elif fault=='bool-inode':source['client_directory_inode']=True
    elif fault=='server':source['server']='10.10.1.6'
    else:source['share']='/volume1/other'
    if fault!='tamper':value['storage'][2]['scope_sha256']=sha256(canonical(source)).hexdigest()
    plan=BackupPlan.model_validate(value)
    monkeypatch.setattr(rwx,'_observe_nfs_root',lambda *args:pytest.fail('Untrusted source reached target observation'))
    with pytest.raises(ValueError):rwx.verify_rwx_relocation(settings(),plan,source)


@pytest.mark.parametrize('fault',['source','source-child','source-parent','inode','target-namespace','stacked','bind-root',
    'old-version','udp','security','address','soft','owner','mode','wire-uid','wire-gid'])
def test_nas_target_alias_or_kernel_identity_drift_refuses(monkeypatch,fault):
    nas,rwx=modules(monkeypatch);plan,source=binding();actual=observed();old=source['nfs_source']
    if fault in {'source','source-child','source-parent'}:
        target=old if fault=='source' else old+'/child' if fault=='source-child' else '10.10.1.5:/volume1/Vitaliy'
        actual['mountinfo']=actual['mountinfo'].replace('10.10.1.5:/volume1/Vitaliy/okf-syn-'+NAMESPACE+'-artifacts-pvc-'+CLAIM,target)
    elif fault=='inode':actual['inode']=source['client_directory_inode']
    elif fault=='target-namespace':actual['mountinfo']=actual['mountinfo'].replace(NAMESPACE,'replica-test-other')
    elif fault=='stacked':actual['mountinfo']*=2
    elif fault=='bind-root':actual['mountinfo']=actual['mountinfo'].replace('0:50 / /artifacts','0:50 /child /artifacts')
    elif fault=='old-version':actual['mountinfo']=actual['mountinfo'].replace('vers=4.0','vers=4.1')
    elif fault=='udp':actual['mountinfo']=actual['mountinfo'].replace('proto=tcp','proto=udp')
    elif fault=='security':actual['mountinfo']=actual['mountinfo'].replace('sec=sys','sec=krb5')
    elif fault=='address':actual['mountinfo']=actual['mountinfo'].replace('addr=10.10.1.5','addr=10.10.1.6')
    elif fault=='soft':actual['mountinfo']=actual['mountinfo'].replace(',hard,',',soft,')
    elif fault=='owner':actual['uid']=1031
    elif fault=='mode':actual['mode']=0o42777
    elif fault=='wire-uid':monkeypatch.setattr(nas,'_wire_identity',lambda:(1000,100))
    else:monkeypatch.setattr(nas,'_wire_identity',lambda:(1031,1000))
    monkeypatch.setattr(rwx,'_observe_nfs_root',lambda root:actual)
    with pytest.raises(ValueError):rwx.verify_rwx_relocation(settings(),plan,source)


def test_existing_selected_nfs41_recipe_still_rejects_nfs40(monkeypatch):
    from tests.test_replica_rwx_restore import fixture_binding,observation
    from app.deployment import rwx_restore
    plan,source=fixture_binding();actual=observation();actual['mountinfo']=actual['mountinfo'].replace('vers=4.1','vers=4.0')
    monkeypatch.setattr(rwx_restore,'_observe_nfs_root',lambda root:actual)
    old=SimpleNamespace(artifact_backend='rwx',artifact_provider='external',artifact_root='/artifacts',deployment_namespace='replica-test-target01')
    with pytest.raises(ValueError):rwx_restore.verify_rwx_relocation(old,plan,source)
