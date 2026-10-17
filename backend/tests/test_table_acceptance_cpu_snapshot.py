"""Default native CPU observation preserves policy with one ancestry snapshot."""
import importlib.util
from pathlib import Path
import pytest


def capture():
    path = Path(__file__).resolve().parents[1] / 'test_scripts/table_acceptance_cpu.py'
    spec = importlib.util.spec_from_file_location('snapshot_capture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_native_path_uses_one_policy_snapshot(monkeypatch):
    m = capture()
    policies = {10: True, 50: False, 90: False}
    calls = []
    wanted = {(10, 100): 20, (11, 110): 30, (50, 500): 40, (90, 900): 10}
    def snapshot(observed):
        calls.append(observed.copy())
        return wanted
    def repeated(*args, **kwargs):
        raise AssertionError('Repeated per-root native read retains unnecessary enumeration cost')
    monkeypatch.setattr(m, 'native_policy_cpu_times', snapshot, raising=False)
    monkeypatch.setattr(m, 'native_process_cpu_times', repeated)
    assert m.cpu_times_by_root(policies) == wanted
    assert calls == [policies]


@pytest.mark.parametrize('policies', [{10: True, 50: False, 90: False}, {10: False, 50: True}, {10: True, 11: False}, {10: False, 11: True}, {10: True, 11: True}])
def test_single_snapshot_selects_exact_policy_union(policies):
    m = capture()
    parents = {10: 1, 11: 10, 12: 11, 50: 1, 51: 50, 90: 1, 91: 90}
    expected = set().union(*(m.process_selection(parents, {pid}, include_descendants=children) for pid, children in policies.items()))
    assert m.process_selection_by_root(parents, policies) == expected
    if policies.get(50) is False:
        assert 51 not in expected
    if policies.get(90) is False:
        assert 91 not in expected


@pytest.mark.parametrize('policies', [{}, {10: 1}, {True: True}, {0: False}, {10: True, 99: False}])
def test_missing_root_or_unknown_policy_never_certifies_partial_snapshot(policies):
    m = capture()
    with pytest.raises(m.CpuOwnershipUnknown):
        m.process_selection_by_root({10: 1, 11: 10}, policies)


def test_native_snapshot_failure_preserves_unknown_ownership(monkeypatch):
    m = capture()
    def broken(policies):
        raise PermissionError('counter unavailable')
    monkeypatch.setattr(m, 'native_policy_cpu_times', broken, raising=False)
    with pytest.raises(m.CpuOwnershipUnknown):
        m.cpu_times_by_root({10: True})
