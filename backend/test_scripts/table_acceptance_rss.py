"""Strict Windows RSS samples for a private pipeline and its descendants.

Peak is the largest observed concurrent tree total, not the sum of individual
process lifetime peaks. Sampling cannot certify unsampled short-lived children.
Shared services and unrelated processes are excluded by ancestry.
"""
import math
import os
import sys
import threading
from table_acceptance_cpu import process_selection, CpuOwnershipUnknown

PHASES = ('construction', 'parse', 'splitting', 'generation', 'finalization', 'publication', 'indexing')


class RssUnknown(ValueError):
    pass


def _positive_integer(value):
    return type(value) is int and value > 0


def _time(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validate_rss_coverage(coverage, sample_count, max_gap_seconds):
    """Recompute temporal coverage from observations, never trust a completeness flag."""
    if not isinstance(coverage, dict) or not _positive_integer(sample_count):
        raise RssUnknown('RSS temporal observations are missing')
    start, end, published = (coverage.get(k) for k in
                              ('window_start', 'window_end', 'publication_finished_at'))
    times = coverage.get('sample_times')
    if (not all(_time(v) for v in (start, end, published, max_gap_seconds))
            or max_gap_seconds <= 0 or end <= start or not start <= published <= end
            or not isinstance(times, list) or len(times) != sample_count
            or not times or any(not _time(t) for t in times)):
        raise RssUnknown('RSS window, publication or timestamps are unknown')
    if (times[0] < start or times[-1] > end or times[-1] < published
            or any(b < a for a, b in zip(times, times[1:]))):
        raise RssUnknown('RSS observations do not cover publication or are unordered')
    gaps = [times[0] - start, end - times[-1]]
    gaps.extend(b - a for a, b in zip(times, times[1:]))
    largest = max(gaps)
    if largest > max_gap_seconds:
        raise RssUnknown('RSS observation gap exceeds the frozen maximum')
    return {'max_observed_gap_seconds': largest, 'window_seconds': end - start}


def read_process_tree(parents, root_pid, read_rss):
    if not _positive_integer(root_pid):
        raise RssUnknown('Pipeline PID is unknown')
    try:
        selected = process_selection(parents, {root_pid}, include_descendants=True)
    except CpuOwnershipUnknown as error:
        raise RssUnknown(str(error)) from error
    total = 0
    for pid in sorted(selected):
        try:
            rss = read_rss(pid)
        except Exception as error:
            raise RssUnknown('Selected process RSS could not be read') from error
        if not _positive_integer(rss):
            raise RssUnknown('Selected process RSS is unavailable')
        total += rss
    return {'rss_bytes': total, 'pids': sorted(selected)}


class RssTrace:
    """Bounded summary, safe for concurrent phase-boundary and periodic samples."""
    def __init__(self):
        self.lock = threading.RLock()
        self.peak = 0
        self.count = 0
        self.phase_counts = dict.fromkeys(PHASES, 0)
        self.error_count = 0
        self.errors = []
        self.times = []

    def error(self, error):
        with self.lock:
            self.error_count += 1
            if len(self.errors) < 32:
                fault = {'type': type(error).__name__, 'reason': str(error)}
                cause = error.__cause__
                if isinstance(cause, OSError):
                    from pathlib import Path
                    path = Path(cause.filename or '')
                    fault.update(cause_type=type(cause).__name__, errno=cause.errno,
                        proc_scope='thread-children' if path.name == 'children' and 'task' in path.parts
                            else 'process-stat' if path.name == 'stat' else 'process-other')
                self.errors.append(fault)

    def record(self, phase, sample, *, at_monotonic):
        with self.lock:
            if (not _time(at_monotonic) or (self.times and at_monotonic < self.times[-1])
                    or phase not in PHASES or not isinstance(sample, dict)
                    or not _positive_integer(sample.get('rss_bytes'))
                    or not isinstance(sample.get('pids'), list) or not sample['pids']
                    or any(not _positive_integer(pid) for pid in sample['pids'])
                    or len(set(sample['pids'])) != len(sample['pids'])):
                error = RssUnknown('RSS sample or phase is unknown')
                self.error(error)
                raise error
            self.peak = max(self.peak, sample['rss_bytes'])
            self.count += 1
            self.phase_counts[phase] += 1
            self.times.append(at_monotonic)

    def result(self, *, sampler_stopped, window_start, window_end,
               publication_finished_at, max_gap_seconds):
        with self.lock:
            coverage = {'window_start': window_start, 'window_end': window_end,
                        'publication_finished_at': publication_finished_at,
                        'sample_times': list(self.times)}
            coverage_error = None
            try:
                validate_rss_coverage(coverage, self.count, max_gap_seconds)
            except RssUnknown as error:
                coverage_error = str(error)
            return {'peak_parent_children_rss_bytes': self.peak,
                    'rss_samples': self.count,
                    'rss_sampler_error_count': self.error_count,
                    'rss_errors': list(self.errors),
                    'sampled_phases': [p for p in PHASES if self.phase_counts[p]],
                    'phase_sample_counts': dict(self.phase_counts),
                    'rss_coverage': coverage,
                    'rss_coverage_error': coverage_error,
                    'rss_sampling_complete': sampler_stopped is True and not self.error_count
                        and coverage_error is None
                        and all(self.phase_counts.values()),
                    'rss_scope': 'entire-pipeline-parent-and-children',
                    'rss_limitations': 'Observed concurrent totals; unsampled transient children or allocation peaks remain unproven'}


def native_process_tree_rss(root_pid=None, *, expected_root_birth=None):
    if sys.platform != 'win32':
        raise RssUnknown('Native RSS observation currently supports Windows only')
    import ctypes
    from ctypes import wintypes as W
    root_pid = os.getpid() if root_pid is None else root_pid

    class Entry(ctypes.Structure):
        _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD),
                    ('heap', ctypes.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                    ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD), ('exe', W.WCHAR * 260)]

    class Memory(ctypes.Structure):
        _fields_ = [('cb', W.DWORD), ('faults', W.DWORD)] + [(name, ctypes.c_size_t)
                    for name in ('peak', 'rss', 'qpp', 'pp', 'qnp', 'np', 'page', 'peakpage')]

    # Local DLL wrappers keep pointer argtypes independent of CPU telemetry threads.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes = [W.DWORD, W.DWORD]; snapshot.restype = W.HANDLE
    first = kernel.Process32FirstW; first.argtypes = [W.HANDLE, ctypes.POINTER(Entry)]; first.restype = W.BOOL
    following = kernel.Process32NextW; following.argtypes = first.argtypes; following.restype = W.BOOL
    close = kernel.CloseHandle; close.argtypes = [W.HANDLE]; close.restype = W.BOOL
    opener = kernel.OpenProcess; opener.argtypes = [W.DWORD, W.BOOL, W.DWORD]; opener.restype = W.HANDLE
    query = psapi.GetProcessMemoryInfo; query.argtypes = [W.HANDLE, ctypes.POINTER(Memory), W.DWORD]; query.restype = W.BOOL
    handle = snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise RssUnknown('Process-tree snapshot failed')
    entry = Entry(); entry.size = ctypes.sizeof(entry); parents = {}
    try:
        more = first(handle, ctypes.byref(entry))
        while more:
            parents[entry.pid] = entry.parent
            more = following(handle, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise RssUnknown('Process-tree snapshot is incomplete')
    finally:
        close(handle)

    births = {}
    birth_query = kernel.GetProcessTimes
    birth_query.argtypes = [W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4
    birth_query.restype = W.BOOL

    def read(pid):
        process = opener(0x400 | 0x10, False, pid)
        if not process:
            raise RssUnknown('Selected process RSS handle is unavailable')
        try:
            memory = Memory(); memory.cb = ctypes.sizeof(memory)
            if not query(process, ctypes.byref(memory), memory.cb):
                raise RssUnknown('Selected process RSS query failed')
            values = [W.FILETIME() for _ in range(4)]
            if not birth_query(process, *(ctypes.byref(v) for v in values)):
                raise RssUnknown('Selected process birth query failed')
            births[pid] = values[0].dwLowDateTime | (values[0].dwHighDateTime << 32)
            if pid == root_pid and expected_root_birth is not None and births[pid] != expected_root_birth:
                raise RssUnknown('Selected root PID birth changed')
            return memory.rss
        finally:
            close(process)
    result = read_process_tree(parents, root_pid, read)
    result['birth_ids'] = births
    return result
