"""Exercise the producer's actual patch arguments using real, offline kubectl."""
import json
import os
import shutil
import subprocess

import pytest

from tests.test_replica_frontend_native import envelope
from tests.test_replica_frontend_oidc_lifecycle import before_loss,replaced


@pytest.mark.skipif(os.name!='posix',reason='Native Linux kubectl command regression')
def test_native_replacement_arguments_apply_locally_without_api_or_registry(tmp_path):
    kubectl=os.environ.get('KUBECTL_BIN') or shutil.which('kubectl')
    assert kubectl,'Linux producer CI must provide the actual kubectl binary'
    current,kube,_=before_loss(tmp_path);current.dispatch(envelope('loss'));replaced(current,kube)
    current.dispatch(envelope('loss-observe'));current.dispatch(envelope('replacement'))
    calls=[call for call in kube.calls if call[0]=='patch'];assert len(calls)==1
    args,operations=calls[0][:-1],calls[0][-1]
    value=dict(apiVersion='v1',kind='Pod',metadata=dict(name=args[2],uid=operations[0]['value'],
        resourceVersion=operations[1]['value'],labels={}),spec={'containers':[dict(name='never-run',image='example.invalid/never-pulled')]})
    path=tmp_path/'local-only-pod.json';path.write_text(json.dumps(value))
    command=[kubectl,args[0],'--local=true','-f',str(path),*args[3:]]
    response=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=15,
        env={**os.environ,'KUBECONFIG':str(tmp_path/'no-cluster-config')})
    assert response.returncode==0,response.stderr
    result=json.loads(response.stdout)
    assert result['metadata']['uid']==value['metadata']['uid'] and result['metadata']['labels']=={'okf-f2-probe-slot':'first'}
