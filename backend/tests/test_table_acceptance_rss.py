"""Fail closed when a process-tree RSS observation cannot cover the pipeline."""
import importlib.util
from pathlib import Path
import pytest


def api():
    path = Path(__file__).parents[1] / 'test_scripts/table_acceptance_rss.py'
    assert path.exists(), 'strict whole-process RSS accounting is missing'
    spec = importlib.util.spec_from_file_location('r2_rss', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_tree_counts_parent_and_nested_children_excluding_shared_services():
    m = api()
    parents = {10: 1, 11: 10, 12: 11, 99: 1}
    reads = []
    def query(pid):
        reads.append(pid)
        return {10: 100, 11: 80, 12: 30, 99: 900}[pid]
    assert m.read_process_tree(parents, 10, query) == {'rss_bytes': 210, 'pids': [10, 11, 12]}
    assert set(reads) == {10, 11, 12}


@pytest.mark.parametrize('fault', ['root_missing', 'child_missing', 'child_failed', 'negative', 'zero', 'boolean'])
def test_unavailable_child_or_root_never_silently_lowers_measured_peak(fault):
    m = api(); parents = {10: 1, 11: 10}
    if fault == 'root_missing': parents.pop(10)
    def query(pid):
        if pid == 11:
            if fault == 'child_missing': return None
            if fault == 'child_failed': raise PermissionError('unavailable')
            if fault == 'negative': return -1
            if fault == 'zero': return 0
            if fault == 'boolean': return True
        return 100
    with pytest.raises(m.RssUnknown): m.read_process_tree(parents, 10, query)


def test_complete_trace_uses_concurrent_tree_total_and_requires_stopped_sampler():
    m = api(); trace = m.RssTrace()
    for index, phase in enumerate(m.PHASES):
        trace.record(phase, {'rss_bytes': 110, 'pids': [10, 11]}, at_monotonic=index / 5)
    trace.record('generation', {'rss_bytes': 210, 'pids': [10, 12]}, at_monotonic=1.4)
    running = trace.result(sampler_stopped=False, window_start=0, window_end=1.4, publication_finished_at=1.4, max_gap_seconds=1)
    result = trace.result(sampler_stopped=True, window_start=0, window_end=1.4, publication_finished_at=1.4, max_gap_seconds=1)
    assert not running['rss_sampling_complete']
    assert result['rss_sampling_complete']
    assert result['peak_parent_children_rss_bytes'] == 210
    assert result['rss_samples'] == 8
    assert result['phase_sample_counts']['generation'] == 2


@pytest.mark.parametrize('fault', ['phase', 'error', 'invalid_total', 'empty_tree'])
def test_partial_trace_or_native_error_is_not_complete(fault):
    m = api(); trace = m.RssTrace()
    for index, phase in enumerate(m.PHASES):
        if phase != 'publication' or fault != 'phase':
            trace.record(phase, {'rss_bytes': 100, 'pids': [10]}, at_monotonic=index / 5)
    if fault == 'error': trace.error(m.RssUnknown('lost process'))
    if fault in ('invalid_total', 'empty_tree'):
        sample = {'rss_bytes': None if fault == 'invalid_total' else 100, 'pids': [] if fault == 'empty_tree' else [10]}
        with pytest.raises(m.RssUnknown): trace.record('indexing', sample, at_monotonic=1.4)
    assert not trace.result(sampler_stopped=True, window_start=0, window_end=1.4, publication_finished_at=1.4, max_gap_seconds=1)['rss_sampling_complete']


def test_unknown_phase_cannot_be_used_to_fill_pipeline_coverage():
    m = api(); trace = m.RssTrace()
    with pytest.raises(m.RssUnknown): trace.record('fake', {'rss_bytes': 100, 'pids': [10]}, at_monotonic=0)


def timed_trace(times):
    m = api(); trace = m.RssTrace()
    for index, at in enumerate(times):
        trace.record(m.PHASES[index % len(m.PHASES)],
                     {'rss_bytes': 100, 'pids': [10]}, at_monotonic=at)
    return trace


def test_rss_rejects_internal_gap():
    trace = timed_trace([0, .2, .4, 3, 3.2, 3.4, 3.6])
    assert not trace.result(sampler_stopped=True, window_start=0, window_end=3.6,
                            publication_finished_at=3.6, max_gap_seconds=1)['rss_sampling_complete']


@pytest.mark.parametrize('start,end', [(-2, 1.2), (0, 4)])
def test_rss_rejects_missing_window_edges(start, end):
    trace = timed_trace([0, .2, .4, .6, .8, 1, 1.2])
    assert not trace.result(sampler_stopped=True, window_start=start, window_end=end,
                            publication_finished_at=1.2, max_gap_seconds=1)['rss_sampling_complete']


def test_rss_requires_publication_completion():
    trace = timed_trace([0, .2, .4, .6, .8, 1, 1.2])
    assert not trace.result(sampler_stopped=True, window_start=0, window_end=1.4,
                            publication_finished_at=1.3, max_gap_seconds=1)['rss_sampling_complete']


def test_native_fault_records_errno_scope_without_exporting_proc_path():
    import errno,json
    m=api();trace=m.RssTrace()
    try:
        try:raise ProcessLookupError(errno.ESRCH,'exited','/proc/123/task/456/children')
        except OSError as cause:raise m.RssUnknown('Selected tree unreadable') from cause
    except m.RssUnknown as error:trace.error(error)
    fault=trace.errors[0]
    assert fault['errno']==errno.ESRCH and fault['proc_scope']=='thread-children'
    assert '/proc/' not in json.dumps(fault) and trace.error_count==1
