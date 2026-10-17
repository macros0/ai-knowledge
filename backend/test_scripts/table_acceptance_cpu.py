"""Account for external CPU only when all selected service work is exclusively owned.

Process birth identities and unchanged trees are required. Transient processes,
foreign completed requests or missing counters leave CPU ownership unknown.
"""
import sys


class CpuOwnershipUnknown(ValueError):
    pass


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def external_cpu_percent(before, after, owned_before, owned_after, *, foreign_service_requests):
    if foreign_service_requests != 0 or not _count(foreign_service_requests):
        raise CpuOwnershipUnknown('Shared service CPU has foreign or unknown request ownership')
    if not all(isinstance(v, (tuple, list)) and len(v) == 2 and all(_count(n) for n in v) for v in (before, after)):
        raise CpuOwnershipUnknown('Unknown system CPU counters')
    idle, total = after[0] - before[0], after[1] - before[1]
    if total <= 0 or idle < 0 or idle > total:
        raise CpuOwnershipUnknown('System CPU counters reset or interval is absent')
    if (not isinstance(owned_before, dict) or not isinstance(owned_after, dict)
            or not owned_before or owned_before.keys() != owned_after.keys()):
        raise CpuOwnershipUnknown('Selected process tree changed or is missing')
    owned = 0
    for identity, previous in owned_before.items():
        current = owned_after[identity]
        if (not isinstance(identity, tuple) or len(identity) != 2
                or not all(_count(v) and v > 0 for v in identity)
                or not _count(previous) or not _count(current) or current < previous):
            raise CpuOwnershipUnknown('Process identity or CPU counter is unknown/reset')
        owned += current - previous
    busy = total - idle
    if owned > busy:
        raise CpuOwnershipUnknown('Process and system CPU intervals cannot reconcile')
    return {'total_cpu_percent': 100 * busy / total,
            'owned_cpu_percent': 100 * owned / total,
            'external_cpu_percent': 100 * (busy - owned) / total}


def listener_processes(rows, ports):
    if (not isinstance(rows, list) or not ports
            or any(not _count(port) or not 1 <= port <= 65535 for port in ports)):
        raise CpuOwnershipUnknown('Listener ownership input is unknown')
    owners = {port: set() for port in ports}
    for row in rows:
        if not isinstance(row, dict) or any(not _count(row.get(k)) for k in ('state', 'local_port', 'owner_pid')):
            raise CpuOwnershipUnknown('Listener ownership table is incomplete')
        if row['state'] == 2 and row['local_port'] in owners:
            owners[row['local_port']].add(row['owner_pid'])
    if any(len(pids) != 1 or next(iter(pids)) <= 0 for pids in owners.values()):
        raise CpuOwnershipUnknown('Every measured service needs one observed listening process')
    return set().union(*owners.values())


def process_selection(parents, roots, *, include_descendants):
    if not isinstance(parents, dict) or not isinstance(roots, set) or not roots or not isinstance(include_descendants, bool):
        raise CpuOwnershipUnknown('Process selection is unknown')
    if not roots <= parents.keys():
        raise CpuOwnershipUnknown('A selected process root disappeared')
    selected = set(roots)
    if include_descendants:
        while True:
            expanded = selected | {pid for pid, parent in parents.items() if parent in selected}
            if selected == expanded: break
            selected = expanded
    return selected


def _validate_root_cpu_policies(policies):
    if (not isinstance(policies, dict) or not policies
            or any(type(pid) is not int or pid <= 0 or type(descendants) is not bool
                   for pid, descendants in policies.items())):
        raise CpuOwnershipUnknown('Per-root process CPU policy is unknown')


def process_selection_by_root(parents, policies):
    """One observed forest, with descendants admitted separately for each root."""
    _validate_root_cpu_policies(policies)
    selected = set()
    for pid, descendants in policies.items():
        selected.update(process_selection(parents, {pid}, include_descendants=descendants))
    return selected


def native_process_cpu_times(roots, *, include_descendants=True):
    """Read selected roots with a uniform descendant policy."""
    if (not isinstance(roots, set) or not roots
            or any(not _count(pid) or pid <= 0 for pid in roots)
            or type(include_descendants) is not bool):
        raise CpuOwnershipUnknown('Selected process roots are unknown')
    return native_policy_cpu_times({pid: include_descendants for pid in roots})


def native_policy_cpu_times(policies):
    """Read one ancestry snapshot for all explicit root policies; retain only counters."""
    if sys.platform != 'win32':
        raise CpuOwnershipUnknown('Native process CPU accounting currently supports Windows only')
    _validate_root_cpu_policies(policies)
    import ctypes
    from ctypes import wintypes as W
    class Entry(ctypes.Structure):
        _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD),
                    ('heap', ctypes.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                    ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD), ('exe', W.WCHAR * 260)]
    # A private DLL wrapper avoids global ctypes argtype races with the RSS sampler.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes = [W.DWORD, W.DWORD]; snapshot.restype = W.HANDLE
    first = kernel.Process32FirstW; first.argtypes = [W.HANDLE, ctypes.POINTER(Entry)]
    following = kernel.Process32NextW; following.argtypes = first.argtypes
    close = kernel.CloseHandle; close.argtypes = [W.HANDLE]
    opener = kernel.OpenProcess; opener.argtypes = [W.DWORD, W.BOOL, W.DWORD]; opener.restype = W.HANDLE
    times = kernel.GetProcessTimes; times.argtypes = [W.HANDLE, *([ctypes.POINTER(W.FILETIME)] * 4)]
    handle = snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise CpuOwnershipUnknown('Process-tree snapshot failed')
    entry = Entry(); entry.size = ctypes.sizeof(entry); parents = {}
    try:
        more = first(handle, ctypes.byref(entry))
        while more:
            parents[entry.pid] = entry.parent
            more = following(handle, ctypes.byref(entry))
    finally:
        close(handle)
    selected = process_selection_by_root(parents, policies)
    result = {}
    def number(value): return (value.dwHighDateTime << 32) | value.dwLowDateTime
    for pid in selected:
        handle = opener(0x1000, False, pid)
        if not handle: raise CpuOwnershipUnknown('Selected process CPU is unavailable')
        try:
            birth, exited, kernel_time, user = [W.FILETIME() for _ in range(4)]
            if not times(handle, ctypes.byref(birth), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user)):
                raise CpuOwnershipUnknown('Selected process CPU observation failed')
            result[(pid, number(birth))] = number(kernel_time) + number(user)
        finally:
            close(handle)
    return result


def cpu_times_by_root(policies, *, reader=None):
    """Select descendants per stable service root, never by executable name.

    Embedding runner descendants can be selected while transient private-PG
    worker descendants remain external. Birth IDs are retained for the next
    interval's strict ownership check. Native snapshots are sequential.
    """
    if (not isinstance(policies, dict) or not policies
            or any(type(pid) is not int or pid <= 0 or type(descendants) is not bool
                   for pid, descendants in policies.items())):
        raise CpuOwnershipUnknown('Per-root process CPU policy is unknown')
    if reader is None:
        try:
            return native_policy_cpu_times(policies)
        except Exception as error:
            raise CpuOwnershipUnknown('Selected CPU root observation failed') from error
    combined = {}
    identities = {}
    for pid, descendants in policies.items():
        try:
            observed = reader({pid}, include_descendants=descendants)
        except Exception as error:
            raise CpuOwnershipUnknown('Selected CPU root observation failed') from error
        if not isinstance(observed, dict) or not observed or not any(key[0] == pid for key in observed if isinstance(key, tuple) and len(key) == 2):
            raise CpuOwnershipUnknown('Selected CPU root is absent from its snapshot')
        for key, ticks in observed.items():
            if (not isinstance(key, tuple) or len(key) != 2
                    or any(type(value) is not int or value <= 0 for value in key)
                    or not _count(ticks)):
                raise CpuOwnershipUnknown('Per-root CPU observation is malformed')
            process, birth = key
            if not descendants and process != pid:
                raise CpuOwnershipUnknown('Root-only CPU snapshot unexpectedly includes a descendant')
            if process in identities and identities[process] != birth:
                raise CpuOwnershipUnknown('PID identity changed between CPU root snapshots')
            identities[process] = birth
            combined[key] = max(combined.get(key, ticks), ticks)
    return combined


def external_cpu_upper_bound(before, after, owned_before, owned_after, *, read_uncertainty_ticks, foreign_service_requests, required_identity):
    """Keep new/exited children and sequential-read uncertainty external.

    Only processes selected by verified ancestry at both endpoints contribute.
    The pinned service root is mandatory. Unsampled children remain external;
    zero completed foreign requests is required, not universal exclusivity.
    """
    if (not _count(read_uncertainty_ticks) or not isinstance(owned_before, dict)
            or not isinstance(owned_after, dict) or required_identity not in owned_before
            or required_identity not in owned_after):
        raise CpuOwnershipUnknown('Stable service root or read bound unavailable')
    for rows in (owned_before, owned_after):
        if any(not isinstance(key, tuple) or len(key) != 2
               or any(not _count(n) or n <= 0 for n in key)
               or not _count(ticks) for key, ticks in rows.items()):
            raise CpuOwnershipUnknown('Selected process counter malformed')
    stable = owned_before.keys() & owned_after.keys()
    if any(owned_after[key] < owned_before[key] for key in stable):
        raise CpuOwnershipUnknown('Selected process counter reset')
    observed = sum(owned_after[key] - owned_before[key] for key in stable)
    lower = max(0, observed - read_uncertainty_ticks)
    result = external_cpu_percent(before, after, {required_identity: 0},
                                  {required_identity: lower},
                                  foreign_service_requests=foreign_service_requests)
    return {'total_cpu_percent': result['total_cpu_percent'],
            'owned_cpu_percent_lower_bound': result['owned_cpu_percent'],
            'external_cpu_percent_upper_bound': result['external_cpu_percent'],
            'stable_process_count': len(stable)}
