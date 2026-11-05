"""Run the actual isolated Linux child over TLS, including its private source loader."""
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_replica_frontend_oidc_http import APP, IDP, SECRET, client
from tests.test_replica_frontend_oidc_http import gateway as gateway


@pytest.mark.skipif(sys.platform!='linux',reason='Native OIDC child enforces Linux owner/mode/O_NOFOLLOW')
@pytest.mark.parametrize('fault',['interrupted','malformed-stream','tampered-source'])
def test_real_child_uses_pinned_tls_and_verified_modules_without_credential_replay(tmp_path,gateway,fault):
    from test_scripts.replica_frontend_native import digest
    from test_scripts.replica_frontend_oidc_native import OIDC_LOSS_CLIENT,CHILD_MODULES
    probe=client(tmp_path,gateway);probe.login(SECRET)
    directory=probe.files.directory;directory.chmod(0o700)
    sources={n:(Path(__file__).resolve().parents[1]/'test_scripts'/(n+'.py')).read_text() for n in CHILD_MODULES}
    capsule={n:sha256(s.encode()).hexdigest() for n,s in sources.items()}
    probe.files.once('capsule.json',capsule)
    for name,source in sources.items():
        if fault=='tampered-source' and name=='replica_frontend_oidc_business':source='raise RuntimeError("unverified source executed")'
        probe.files.once('module-'+name.replace('_','-')+'.json',{'source':source})
    configuration=dict(directory=str(directory),origin=APP,identity=IDP,port=gateway['port'],username='lab-admin',
        execution_id='e'*32,capsule_sha256=digest(capsule))
    probe.files.once('loss-client-input.json',configuration)
    gateway['state']['mode']='normal' if fault=='tampered-source' else fault
    # Neither ambient PYTHONPATH nor a same-named package in cwd may select code.
    foreign=tmp_path/'test_scripts';foreign.mkdir();(foreign/'__init__.py').write_text('raise RuntimeError("foreign cwd selected")')
    result=subprocess.run([sys.executable,'-I','-c',OIDC_LOSS_CLIENT,str(probe.files.path('loss-client-input.json'))],
        cwd=tmp_path,env={**os.environ,'PYTHONPATH':str(tmp_path)},capture_output=True,timeout=20)
    assert gateway['state']['credential_posts']==1
    if fault=='tampered-source':
        assert result.returncode!=0 and gateway['state']['stream_posts']==0
        assert b'unverified source executed' not in result.stderr
        assert not probe.files.path('loss.progress.json').exists()
    else:
        assert result.returncode==0,result.stderr.decode()
        progress=probe.files.read('loss.progress.json')
        assert progress['terminal'] and progress['sources_seen'] and not progress['result_seen']
        assert progress['protocol_error']==(fault=='malformed-stream') and not progress['error_seen']
        assert gateway['state']['stream_posts']==1
