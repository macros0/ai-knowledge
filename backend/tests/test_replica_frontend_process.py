"""Only the retained native PID identity may be signalled or used as a route."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def module():
    path = Path(__file__).resolve().parents[1] / 'test_scripts/replica_frontend_process.py'
    assert path.is_file(), 'Native owned process manager is missing'
    return importlib.import_module('test_scripts.replica_frontend_process')


def manager(tmp_path):
    tool = module()
    current = tool.OwnedProcesses(tmp_path, {'context': 'kind-owned', 'namespace': 'replica-test-abcdefgh'})
    handle = {'pid': 54321, 'start_ticks': '12345', 'argv': ['kubectl', 'owned'], 'uid': 1000}
    current.files.once('pf-0.pid.json', handle)
    current.files.once('pf-0.intent.json', {'pod': {'name': 'frontend-0', 'uid': 'bound'}, 'port': 3000, 'argv': handle['argv']})
    (tmp_path / 'pf-0.log').write_text('Forwarding from 127.0.0.1:25401 -> 3000\n')
    (tmp_path / 'pf-0.log').chmod(0o600)
    current.describe = lambda pid: handle.copy()
    return current, handle


def test_route_is_from_native_forward_log_and_exact_process_identity(tmp_path):
    current, _ = manager(tmp_path)
    assert current.route('pf-0')['url'] == 'http://127.0.0.1:25401'


@pytest.mark.parametrize('field', ['start_ticks', 'argv', 'uid'])
def test_foreign_or_reused_pid_is_never_a_route_or_signal_target(field, tmp_path):
    current, handle = manager(tmp_path)
    current.describe = lambda pid: {**handle, field: 'foreign'}
    with pytest.raises(ValueError): current.route('pf-0')
    signalled = []
    current.signal_owned = lambda *args: signalled.append(args)
    current.stop('pf-0')
    assert signalled == []


def test_uncertain_native_forward_is_not_started_again(tmp_path):
    current, _ = manager(tmp_path)
    with pytest.raises(ValueError, match='intent'):
        current.forward('pf-0', {'name': 'frontend-0', 'uid': 'bound'}, 3000)


def test_port_line_cannot_select_nonloopback_route(tmp_path):
    current, _ = manager(tmp_path)
    (tmp_path / 'pf-0.log').write_text('Forwarding from 0.0.0.0:25401 -> 3000\n')
    with pytest.raises(ValueError): current.route('pf-0')


def test_completed_native_handle_cannot_be_substituted(tmp_path):
    current, _ = manager(tmp_path)
    path = tmp_path / 'pf-0.pid.json'
    value = json.loads(path.read_bytes()); value['argv'] = ['another-command']; path.write_text(json.dumps(value))
    with pytest.raises(ValueError): current.route('pf-0')


@pytest.mark.skipif(os.name!='posix' or not hasattr(os,'pidfd_open'),reason='Native coordinator requires Linux pidfd')
def test_real_linux_pidfd_refuses_wrong_identity_and_closes_only_its_own_child(tmp_path):
    current=module().OwnedProcesses(tmp_path,{'context':'kind-owned','namespace':'replica-test-abcdefgh'})
    argv=[sys.executable,'-c','import time;time.sleep(60)']
    child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    try:
        real=current.describe(child.pid);assert real is not None
        wrong={**real,'start_ticks':str(int(real['start_ticks'])+1)}
        current.files.once('test.pid.json',wrong);current.files.once('test.intent.json',{'argv':argv})
        current.stop('test');assert child.poll() is None
        current.signal_owned(real);child.wait(timeout=10);assert child.returncode!=0
        assert current.describe(child.pid) is None
    finally:
        if child.poll() is None:
            current.signal_owned(current.describe(child.pid));child.wait(timeout=10)
