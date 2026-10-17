"""Run real disposable children; no product performance claim from these tests."""
import importlib.util
import json
from pathlib import Path
import sys
import time

import pytest


def api():
    path = Path(__file__).parents[1] / 'test_scripts/run_table_acceptance.py'
    assert path.exists(), 'bounded acceptance runner is missing'
    spec = importlib.util.spec_from_file_location('r2_runner', path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_runner_rejects_unready_observer_before_provider_call(tmp_path):
    called = tmp_path / 'provider-called'
    result = api().run_owned_command([sys.executable, '-c',
        'from pathlib import Path; Path(' + repr(str(called)) + ').touch()'],
        output_dir=tmp_path / 'run', timeout_seconds=2, readiness=lambda: False)
    assert result['exit_code'] == 2 and result['reason'] == 'readiness_failed'
    assert not called.exists() and result['owned_process'] is None


def test_hung_command_is_bounded_and_partial_result_is_retained(tmp_path):
    started = time.monotonic()
    result = api().run_owned_command([sys.executable, '-c', 'import time; time.sleep(60)'],
        output_dir=tmp_path / 'run', timeout_seconds=.5, readiness=lambda: True)
    assert time.monotonic() - started < 5
    assert result['exit_code'] == 2 and result['reason'] == 'deadline_exceeded'
    assert result['cleanup_complete'] is True
    assert result['owned_process']['pid'] > 0 and result['owned_process']['birth_id']
    assert json.loads((tmp_path / 'run/command-manifest.json').read_text()) == result


def test_runner_stops_descendant_with_owned_process_group(tmp_path):
    witness = tmp_path / 'late-write'
    child_code = 'import time; from pathlib import Path; time.sleep(2); Path(' + repr(str(witness)) + ').touch()'
    parent_code = 'import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", ' + repr(child_code) + ']); time.sleep(60)'
    result = api().run_owned_command([sys.executable, '-c', parent_code],
        output_dir=tmp_path / 'run', timeout_seconds=.8, readiness=lambda: True)
    assert result['cleanup_complete'] is True
    time.sleep(2.2)
    assert not witness.exists()


def test_reused_pid_is_never_signalled():
    calls = []
    m = api()
    assert m.stop_if_owned({'pid': 12, 'birth_id': 44},
        read_birth=lambda pid: 45, stop=lambda: calls.append('kill')) is False
    assert not calls


def test_lost_observer_stops_batch_without_starting_next_variant(tmp_path):
    checks = iter([True, False])
    called = tmp_path / 'next'
    result = api().run_batch([
        [sys.executable, '-c', 'import time; time.sleep(60)'],
        [sys.executable, '-c', 'from pathlib import Path; Path(' + repr(str(called)) + ').touch()']],
        output_dir=tmp_path / 'batch', timeout_seconds=3,
        readiness=lambda: next(checks, False))
    assert result['exit_code'] == 2
    assert len(result['commands']) == 1 and result['commands'][0]['reason'] == 'observer_lost'
    assert not called.exists()


def test_successful_command_is_collected_but_not_product_acceptance(tmp_path):
    result = api().run_owned_command([sys.executable, '-c', 'pass'],
        output_dir=tmp_path / 'run', timeout_seconds=5, readiness=lambda: True)
    assert result['command_returncode'] == 0 and result['exit_code'] == 0
    assert result['status'] == 'collected' and result['cleanup_complete'] is True


def test_output_directory_cannot_overwrite_historical_evidence(tmp_path):
    m = api(); folder = tmp_path / 'existing'; folder.mkdir()
    sentinel = folder / 'proof'; sentinel.write_text('keep')
    with pytest.raises(FileExistsError):
        m.run_owned_command([sys.executable, '-c', 'pass'], output_dir=folder,
                            timeout_seconds=1, readiness=lambda: True)
    assert sentinel.read_text() == 'keep'


@pytest.mark.parametrize('limit', [0, -1, float('nan'), float('inf'), 86401, True])
def test_invalid_total_batch_budget_is_rejected_before_launch(tmp_path, limit):
    with pytest.raises(ValueError):
        api().run_batch([[sys.executable, '-c', 'pass']], output_dir=tmp_path / 'batch',
                        timeout_seconds=1, total_timeout_seconds=limit, readiness=lambda: True)


def test_lost_adapter_transport_is_invalid_and_prevents_next_variant(tmp_path):
    result = api().run_batch([[sys.executable, '-c', 'raise SystemExit(255)'],
                             [sys.executable, '-c', 'pass']], output_dir=tmp_path / 'batch',
                             timeout_seconds=5, readiness=lambda: True)
    assert result['exit_code'] == 2 and len(result['commands']) == 1
    assert result['commands'][0]['reason'] == 'command_failed'


def test_cli_reports_invalid_when_required_adapter_contract_is_missing(tmp_path):
    import subprocess
    protocol = tmp_path / 'protocol.json'; protocol.write_text('{}')
    out = tmp_path / 'result'
    result = subprocess.run([sys.executable, str(Path(__file__).parents[1] / 'test_scripts/run_table_acceptance.py'),
        '--kind', 'resources', '--protocol', str(protocol), '--output-dir', str(out)],
        capture_output=True, timeout=15)
    assert result.returncode == 2
    assert json.loads((out / 'acceptance.json').read_text())['status'] == 'invalid'
    assert not result.stderr



def test_invalid_cli_input_cannot_append_to_historical_batch_directory(tmp_path):
    import subprocess
    out = tmp_path / 'old'; out.mkdir()
    (out / 'batch-manifest.json').write_text('{}')
    protocol = tmp_path / 'protocol.json'; protocol.write_text('{}')
    result = subprocess.run([sys.executable, str(Path(__file__).parents[1] / 'test_scripts/run_table_acceptance.py'),
        '--kind', 'resources', '--protocol', str(protocol), '--output-dir', str(out)],
        capture_output=True, timeout=15)
    assert result.returncode == 2 and sorted(p.name for p in out.iterdir()) == ['batch-manifest.json']



def test_runner_rejects_measurement_file_outside_new_batch_before_provider_call(tmp_path):
    import subprocess
    out = tmp_path / 'output'
    marker = tmp_path / 'provider-called'
    readiness = tmp_path / 'ready.json'
    readiness.write_text(json.dumps({'ready': True, 'observed_monotonic': time.monotonic()}))
    stale = tmp_path / 'historical-measurements.json'; stale.write_text('{}')
    protocol = tmp_path / 'protocol.json'
    protocol.write_text(json.dumps({'runner': {'readiness_file': str(readiness),
        'measurements_file': str(stale), 'commands': [[sys.executable, '-c',
        'from pathlib import Path; Path(' + repr(str(marker)) + ').touch()']]}}))
    result = subprocess.run([sys.executable, str(Path(__file__).parents[1] / 'test_scripts/run_table_acceptance.py'),
        '--kind', 'resources', '--protocol', str(protocol), '--output-dir', str(out)],
        capture_output=True, timeout=15)
    assert result.returncode == 2 and not marker.exists()
    assert not (out / 'commands').exists()
