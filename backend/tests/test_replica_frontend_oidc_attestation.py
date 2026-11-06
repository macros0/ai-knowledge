"""Native completion proof must recheck every receipt under the current scope."""
import json

import pytest

from tests.test_replica_frontend_native import envelope
from tests.test_replica_frontend_oidc_lifecycle import before_loss,replaced
from tests.test_replica_frontend_oidc_native import module


def finished(tmp_path):
    assert hasattr(module().OIDCCoordinator,'attest'),'Native all-stage completion proof missing'
    current,kube,process=before_loss(tmp_path)
    current.dispatch(envelope('loss'));replaced(current,kube)
    for stage in ['loss-observe','replacement','logout']:current.dispatch(envelope(stage))
    request=envelope('final');current.dispatch(request)
    return current,kube,process,request


def test_native_attestation_rechecks_scope_and_returns_all_twelve_receipt_hashes(tmp_path):
    from test_scripts.replica_frontend_native import STAGES,digest
    current,kube,process,request=finished(tmp_path)
    calls=process.calls.copy();proof=current.attest(request)
    assert proof==dict(recipe='owned-keycloak-local-v1',**{k:request[k] for k in ['execution_id','code_sha256','binding_sha256']},
        capsule_sha256=digest(current.files.read('capsule.json')),
        receipts_sha256={stage:digest(current.files.read('native.'+stage+'.result.json')) for stage in STAGES})
    assert process.calls==calls and 'PRIVATE' not in json.dumps(proof)
    kube.parts[2][0]['metadata']['uid']='f'*36
    with pytest.raises(ValueError):current.attest(request)


def test_native_attestation_rejects_corrupt_previous_receipt(tmp_path):
    current,_,_,request=finished(tmp_path)
    path=current.files.path('native.session.result.json');row=json.loads(path.read_bytes());row['binding_sha256']='f'*64
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError):current.attest(request)
