from table_acceptance_cpu import external_cpu_upper_bound, CpuOwnershipUnknown
"""External CPU accounting must retain unknown ownership and system load."""
import importlib.util
from pathlib import Path
import pytest


def module():
    path = Path(__file__).resolve().parents[1] / 'test_scripts/table_acceptance_cpu.py'
    assert path.exists(), 'explicit owned process CPU accounting missing'
    spec = importlib.util.spec_from_file_location('cpu_capture', path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def test_owned_service_cpu_is_separate_from_external_background():
    m = module()
    before = {(10, 100): 20, (11, 110): 10}
    after = {(10, 100): 50, (11, 110): 20}
    result = m.external_cpu_percent((100, 1000), (150, 1100), before, after, foreign_service_requests=0)
    assert result == {'total_cpu_percent': 50, 'owned_cpu_percent': 40, 'external_cpu_percent': 10}


@pytest.mark.parametrize('fault', ['foreign', 'unknown_foreign', 'empty', 'new_pid', 'reused_pid', 'exited_pid', 'reset_process', 'more_than_busy', 'reset_system'])
def test_unknown_process_or_service_ownership_cannot_be_subtracted(fault):
    m = module(); before = {(10, 100): 20}; after = {(10, 100): 50}; requests = 0
    global_before, global_after = (100, 1000), (150, 1100)
    if fault == 'foreign': requests = 1
    elif fault == 'unknown_foreign': requests = None
    elif fault == 'empty': before = {}; after = {}
    elif fault == 'new_pid': after[(11, 110)] = 5
    elif fault == 'reused_pid': after = {(10, 101): 50}
    elif fault == 'exited_pid': after = {}
    elif fault == 'reset_process': after[(10, 100)] = 10
    elif fault == 'more_than_busy': after[(10, 100)] = 90
    elif fault == 'reset_system': global_after = (90, 999)
    with pytest.raises(ValueError): m.external_cpu_percent(global_before, global_after, before, after, foreign_service_requests=requests)


def test_server_roots_require_unique_observed_listener_for_every_port():
    m = module()
    rows = [{'state': 2, 'local_port': 16400, 'owner_pid': 10}, {'state': 2, 'local_port': 16333, 'owner_pid': 20}, {'state': 5, 'local_port': 50000, 'owner_pid': 30}]
    assert m.listener_processes(rows, (16400, 16333)) == {10, 20}
    with pytest.raises(ValueError): m.listener_processes(rows, (16400, 15432))
    with pytest.raises(ValueError): m.listener_processes(rows + [{'state': 2, 'local_port': 16400, 'owner_pid': 40}], (16400, 16333))


def test_unselected_transient_children_remain_in_external_cpu():
    m = module()
    parents = {10: 1, 11: 10, 12: 1}
    assert m.process_selection(parents, {10, 12}, include_descendants=False) == {10, 12}
    assert m.process_selection(parents, {10, 12}, include_descendants=True) == {10, 11, 12}
    with pytest.raises(ValueError): m.process_selection(parents, {99}, include_descendants=False)
    result = m.external_cpu_percent((100, 1000), (150, 1100), {(10, 100): 10}, {(10, 100): 40}, foreign_service_requests=0)
    assert result['external_cpu_percent'] == 20


def test_embedding_descendants_are_owned_without_subtracting_private_pg_workers():
    m = module()
    reads = []
    def reader(roots, *, include_descendants):
        root = next(iter(roots)); reads.append((root, include_descendants))
        result = {(root, root*10): 100}
        if include_descendants: result[(root+1, root*10+1)] = 300
        return result
    result = m.cpu_times_by_root({10: True, 50: False, 90: False}, reader=reader)
    assert result == {(10,100):100,(11,101):300,(50,500):100,(90,900):100}
    assert reads == [(10,True),(50,False),(90,False)]
    assert (51,501) not in result


@pytest.mark.parametrize('policies', [{}, {10: 1}, {True: True}, {0: False}])
def test_root_cpu_policy_must_be_explicit_and_known(policies):
    m = module()
    with pytest.raises(m.CpuOwnershipUnknown): m.cpu_times_by_root(policies, reader=lambda *a, **kw: {})


def test_missing_selected_root_cannot_certify_a_runner_only_cpu_snapshot():
    m = module()
    with pytest.raises(m.CpuOwnershipUnknown):
        m.cpu_times_by_root({10:True}, reader=lambda *a, **kw: {(11,101):300})


def test_root_policies_reject_reused_pid_across_overlapping_trees():
    m = module()
    def reader(roots, **kwargs):
        root = next(iter(roots))
        return {(root,root*10):100, (12,1000 if root==10 else 2000):20}
    with pytest.raises(m.CpuOwnershipUnknown): m.cpu_times_by_root({10:True,50:True},reader=reader)


def test_root_only_policy_rejects_extra_child_in_native_snapshot():
    m = module()
    with pytest.raises(m.CpuOwnershipUnknown):
        m.cpu_times_by_root({10:False},reader=lambda *a, **kw: {(10,100):20,(11,101):30})


def test_native_root_reader_failure_remains_unknown_ownership():
    m = module()
    def reader(*args, **kwargs): raise PermissionError('unavailable')
    with pytest.raises(m.CpuOwnershipUnknown): m.cpu_times_by_root({10:True},reader=reader)



def test_upper_bound_subtracts_only_stable_births_and_read_lower_bound():
    a={(1,10):100,(2,20):100};b={(1,10):140,(3,30):500}
    result=external_cpu_upper_bound((0,100),(20,200),a,b,read_uncertainty_ticks=10,foreign_service_requests=0,required_identity=(1,10))
    assert result['owned_cpu_percent_lower_bound']==30
    assert result['external_cpu_percent_upper_bound']==50


def test_upper_bound_refuses_foreign_requests_or_missing_root():
    for foreign,root in [(1,(1,10)),(0,(1,99))]:
        with pytest.raises(CpuOwnershipUnknown):external_cpu_upper_bound((0,100),(20,200),{(1,10):100},{(1,10):140},read_uncertainty_ticks=0,foreign_service_requests=foreign,required_identity=root)


def test_upper_bound_keeps_uncertain_owned_time_external():
    result=external_cpu_upper_bound((0,100),(20,200),{(1,10):100},{(1,10):140},read_uncertainty_ticks=50,foreign_service_requests=0,required_identity=(1,10))
    assert result['external_cpu_percent_upper_bound']==80
