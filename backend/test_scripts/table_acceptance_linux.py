"""Strict Linux /proc telemetry for isolated table acceptance, without model text.

A sampled tree cannot prove short-lived unsampled children. Missing, reused or
unreadable selected processes invalidate observation rather than becoming zero.
"""
from pathlib import Path
import os
from table_acceptance_cpu import process_selection, CpuOwnershipUnknown


class LinuxObservationUnknown(ValueError):
    pass


def parse_process_stat(body, *, page_size):
    try:
        identity, separator, remainder=body.rpartition(") ")
        if not separator or " (" not in identity or type(page_size) is not int or page_size<=0:
            raise ValueError()
        pid=int(identity.split(" (",1)[0]);fields=remainder.split()
        parent=int(fields[1]);user=int(fields[11]);system=int(fields[12]);birth=int(fields[19]);rss=int(fields[21])
        if pid<=0 or parent<0 or user<0 or system<0 or birth<=0 or rss<0:
            raise ValueError()
        return {"pid":pid,"parent":parent,"birth_id":birth,"cpu_ticks":user+system,"rss_bytes":rss*page_size,"state":fields[0]}
    except (ValueError,IndexError,TypeError,AttributeError) as error:
        raise LinuxObservationUnknown("Process stat is incomplete or invalid") from error


def parse_system_cpu(body):
    try:
        fields=body.splitlines()[0].split()
        if fields[0]!="cpu" or len(fields)<9:raise ValueError()
        ticks=[int(v) for v in fields[1:9]]
        if any(v<0 for v in ticks):raise ValueError()
        # guest/guest_nice are already included in user/nice. iowait is idle.
        return {"total_ticks":sum(ticks),"idle_ticks":ticks[3]+ticks[4]}
    except (ValueError,IndexError,TypeError,AttributeError) as error:
        raise LinuxObservationUnknown("System CPU stat is incomplete or invalid") from error


def process_tree_sample(rows,root_pid,*,expected_root_birth=None):
    try:selected=process_selection({p:r["parent"] for p,r in rows.items()},{root_pid},include_descendants=True)
    except (CpuOwnershipUnknown,KeyError,TypeError) as error:
        raise LinuxObservationUnknown("Process tree is incomplete") from error
    if expected_root_birth is not None and rows[root_pid]["birth_id"]!=expected_root_birth:
        raise LinuxObservationUnknown("Root PID birth identity changed")
    # A verified unreaped child has kernel state Z and zero resident pages.
    # Keep its identity in both inventories; never turn unreadable/live RSS into zero.
    if any((rows[p].get('state') == 'Z' and (p == root_pid or rows[p]['rss_bytes'] != 0))
           or (rows[p].get('state') != 'Z' and rows[p]['rss_bytes'] <= 0) for p in selected):
        raise LinuxObservationUnknown("Selected process RSS is unavailable")
    return {"rss_bytes":sum(rows[p]["rss_bytes"] for p in selected),"pids":sorted(selected),
            "birth_ids":{p:rows[p]["birth_id"] for p in sorted(selected)},
            "cpu_ticks":sum(rows[p]["cpu_ticks"] for p in selected)}


def _inventory_pause():
    # Let an exiting thread publish its catalog removal before re-reading /proc.
    # At most two 1ms pauses per inventory; the RSS gap gate remains independent.
    import time
    time.sleep(.001)


class _TreeTransient(RuntimeError):
    pass


def native_process_tree_rss(root_pid=None, *, proc_root=Path('/proc'), expected_root_birth=None, page_size=None):
    root_pid = os.getpid() if root_pid is None else root_pid
    proc_root = Path(proc_root)
    pages = os.sysconf('SC_PAGE_SIZE') if page_size is None else page_size
    try:
        initial = parse_process_stat((proc_root/str(root_pid)/'stat').read_text(), page_size=pages)
    except OSError as error:
        raise LinuxObservationUnknown('Selected root identity could not be read') from error
    if expected_root_birth is None:
        expected_root_birth = initial['birth_id']
    observed_births = {}
    for attempt in range(3):
        try:
            value = _native_process_tree_rss_once(root_pid, proc_root=proc_root,
                expected_root_birth=expected_root_birth, page_size=pages, observed_births=observed_births)
            value['tree_retries'] = attempt
            return value
        except _TreeTransient as error:
            if attempt == 2:
                raise LinuxObservationUnknown('No consistent live tree after three inventories') from error
            _inventory_pause()


def _native_process_tree_rss_once(root_pid=None,*,proc_root=Path("/proc"),expected_root_birth=None,page_size=None,observed_births=None):
    root_pid=os.getpid() if root_pid is None else root_pid
    proc_root=Path(proc_root)
    pages=os.sysconf("SC_PAGE_SIZE") if page_size is None else page_size
    rows={};children_before={};threads_before={}

    inventory_retries = 0

    def children(pid):
        nonlocal inventory_retries
        task=proc_root/str(pid)/"task"
        # A thread can exit between directory enumeration and children read.
        # Re-enumerate only that inventory, at most three times. Missing process
        # stat/parent/birth or a changing process-child tree is never retried.
        for attempt in range(3):
            threads=tuple(sorted(p.name for p in task.iterdir() if p.name.isdecimal()))
            if not threads:raise LinuxObservationUnknown("Selected process threads unavailable")
            result=set()
            try:
                for tid in threads:
                    body=(task/tid/"children").read_text().split()
                    if any(not p.isdecimal() or int(p)<=0 for p in body):
                        raise LinuxObservationUnknown("Selected child inventory invalid")
                    result.update(int(p) for p in body)
            except FileNotFoundError:
                inventory_retries += 1
                if attempt == 2:raise
                _inventory_pause()
                continue
            return threads,tuple(sorted(result))

    def visit(pid,parent=None):
        if pid in rows:raise LinuxObservationUnknown("Selected child inventory has duplicate or cyclic identity")
        try:
            row=parse_process_stat((proc_root/str(pid)/"stat").read_text(),page_size=pages)
        except FileNotFoundError as error:
            if pid != root_pid:raise _TreeTransient('Selected child exited during observation') from error
            raise
        if observed_births is not None:
            if pid in observed_births and observed_births[pid] != row['birth_id']:
                raise LinuxObservationUnknown('Selected PID birth identity changed across inventory attempts')
            observed_births[pid] = row['birth_id']
        if row["pid"]!=pid or (parent is not None and row["parent"]!=parent):
            raise LinuxObservationUnknown("Selected process parent or PID identity mismatch")
        rows[pid]=row
        try:
            threads_before[pid],children_before[pid]=children(pid)
        except FileNotFoundError as error:
            if pid != root_pid and not (proc_root/str(pid)/'stat').exists():
                raise _TreeTransient('Selected child exited during inventory') from error
            raise
        for child in children_before[pid]:visit(child,pid)

    # Enumerate the selected tree through every thread's children, not all /proc:
    # an unrelated disappearing process cannot invalidate a selected-tree sample.
    try:
        visit(root_pid)
        value=process_tree_sample(rows,root_pid,expected_root_birth=expected_root_birth)
        for pid,birth in value["birth_ids"].items():
            try:
                current=parse_process_stat((proc_root/str(pid)/"stat").read_text(),page_size=pages)
            except FileNotFoundError as error:
                if pid != root_pid:raise _TreeTransient('Selected child exited during identity check') from error
                raise
            if current["birth_id"]!=birth:raise LinuxObservationUnknown("Selected PID birth identity changed")
            if (current.get('state') == 'Z') != (rows[pid].get('state') == 'Z'):
                raise _TreeTransient('Selected child lifecycle changed during observation')
            try:
                threads_after,children_after=children(pid)
            except FileNotFoundError as error:
                if pid != root_pid and not (proc_root/str(pid)/'stat').exists():
                    raise _TreeTransient('Selected child exited during final inventory') from error
                raise
            if children_after!=children_before[pid]:
                raise _TreeTransient("Selected process-child tree changed during observation")
        value['inventory_retries'] = inventory_retries
        return value
    except OSError as error:
        raise LinuxObservationUnknown("Selected tree or identity could not be read") from error



def external_cgroup_cpu_percent(before,after):
    """Conservative host external-CPU upper bound from stable owned cgroups.

    All eight system counters are integer clock ticks. Their before/after
    rounding plus the observer's two process counters costs at most 20 ticks;
    add the full-host CPU capacity across the measured sequential read window.
    Missing/reset/reused counters are unknown. Foreign service work must be
    independently excluded before treating the selected cgroups as owned.
    """
    import math
    def counter(value):return type(value) is int and value>=0
    try:
        for field in ('hz','cpu_count','ct_init_birth'):
            if type(before[field]) is not int or before[field]<=0 or before[field]!=after[field]:
                raise ValueError()
        for row in (before,after):
            for field in ('system_total_ticks','system_idle_ticks','observer_cpu_ticks'):
                if not counter(row[field]):raise ValueError()
            duration=row['snapshot_read_seconds']
            if type(duration) not in (int,float) or not math.isfinite(duration) or duration<0:raise ValueError()
        groups=before['owned_groups']
        if not groups or set(groups)!=set(after['owned_groups']):raise ValueError()
        owned_us=0
        for role,old in groups.items():
            new=after['owned_groups'][role]
            if (type(old['cgroup_inode']) is not int or old['cgroup_inode']<=0
                    or new['cgroup_inode']!=old['cgroup_inode']
                    or not counter(old['usage_usec']) or not counter(new['usage_usec'])
                    or new['usage_usec']<old['usage_usec']):raise ValueError()
            owned_us+=new['usage_usec']-old['usage_usec']
        total=after['system_total_ticks']-before['system_total_ticks']
        idle=after['system_idle_ticks']-before['system_idle_ticks']
        observer=after['observer_cpu_ticks']-before['observer_cpu_ticks']
        if total<=0 or idle<0 or idle>total or observer<0:raise ValueError()
        owned=owned_us*before['hz']/1000000+observer
        busy=total-idle
        duration=max(before['snapshot_read_seconds'],after['snapshot_read_seconds'])
        bound=20+math.ceil(duration*before['cpu_count']*before['hz']+2*len(groups)*before['hz']/1000000)
        if owned>busy+bound:raise ValueError()
        return {'cpu_percent':max(0,busy-owned+bound)/total*100,
                'total_cpu_percent':busy/total*100,'owned_cpu_percent':owned/total*100,
                'cpu_uncertainty_percent':bound/total*100,
                'cpu_scope':'Conservative external CPU upper bound; stable owned cgroups plus observer excluded'}
    except (KeyError,ValueError,TypeError,AttributeError) as error:
        raise LinuxObservationUnknown('Cgroup/system CPU counters are missing, reset or inconsistent') from error
