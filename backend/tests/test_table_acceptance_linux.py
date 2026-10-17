"""Linux benchmark telemetry must preserve identity and reject incomplete samples."""
import pytest
from table_acceptance_linux import parse_process_stat, parse_system_cpu, process_tree_sample, LinuxObservationUnknown, native_process_tree_rss, external_cgroup_cpu_percent


def stat(pid=7, parent=1, start=123, rss=4, name="runner ) child"):
    fields=["S",str(parent)]+["0"]*50
    fields[11]="20";fields[12]="30";fields[19]=str(start);fields[21]=str(rss)
    return f"{pid} ({name}) "+" ".join(fields)


def test_process_stat_name_parent_birth_rss_and_cpu():
    assert parse_process_stat(stat(), page_size=4096)=={"pid":7,"parent":1,"birth_id":123,"cpu_ticks":50,"rss_bytes":16384,"state":"S"}


@pytest.mark.parametrize("body",["", "7 (unfinished", stat(start=0), stat(rss=-1),stat(pid=-7)])
def test_bad_stat_unknown(body):
    with pytest.raises(LinuxObservationUnknown):parse_process_stat(body,page_size=4096)


def test_cpu_idle_counts_iowait_once_and_excludes_guest_double_count():
    assert parse_system_cpu("cpu 10 20 30 40 50 60 70 80 9 8\ncpu0 1 2")=={"total_ticks":360,"idle_ticks":90}


@pytest.mark.parametrize("body",["cpu 1 2", "cpu 1 2 3 -1 5 6 7 8", "cpu bad 2 3 4 5 6 7 8"])
def test_bad_system_cpu_unknown(body):
    with pytest.raises(LinuxObservationUnknown):parse_system_cpu(body)


def test_tree_concurrent_sum_excludes_unrelated_and_preserves_births():
    rows={1:parse_process_stat(stat(pid=1,parent=0,rss=10),page_size=1),2:parse_process_stat(stat(pid=2,parent=1,rss=20),page_size=1),3:parse_process_stat(stat(pid=3,parent=0,rss=40),page_size=1)}
    assert process_tree_sample(rows,1)=={"rss_bytes":30,"pids":[1,2],"birth_ids":{1:123,2:123},"cpu_ticks":100}


def test_missing_root_unknown():
    with pytest.raises(LinuxObservationUnknown):process_tree_sample({},1)


def test_selected_zero_rss_unknown():
    rows={1:parse_process_stat(stat(pid=1,parent=0,rss=0),page_size=1)}
    with pytest.raises(LinuxObservationUnknown):process_tree_sample(rows,1)


def test_reused_pid_expected_birth_unknown():
    rows={1:parse_process_stat(stat(pid=1,parent=0),page_size=1)}
    with pytest.raises(LinuxObservationUnknown):process_tree_sample(rows,1,expected_root_birth=999)



def fixture_proc(root,pid,parent,children="",rss=4):
    folder=root/str(pid);folder.mkdir()
    (folder/"stat").write_text(stat(pid=pid,parent=parent,rss=rss))
    task=folder/"task"/str(pid);task.mkdir(parents=True)
    (task/"children").write_text(children)


def test_native_ignores_unrelated_unreadable_process(tmp_path):
    fixture_proc(tmp_path,1,0,"2")
    fixture_proc(tmp_path,2,1)
    (tmp_path/"999").mkdir()  # unrelated process disappearing during enumeration
    sample=native_process_tree_rss(1,proc_root=tmp_path,page_size=1)
    assert sample["pids"]==[1,2] and sample["rss_bytes"]==8


def test_native_missing_selected_child_is_unknown(tmp_path):
    fixture_proc(tmp_path,1,0,"2")
    with pytest.raises(LinuxObservationUnknown):native_process_tree_rss(1,proc_root=tmp_path,page_size=1)


def test_native_wrong_selected_child_parent_is_unknown(tmp_path):
    fixture_proc(tmp_path,1,0,"2")
    fixture_proc(tmp_path,2,99)
    with pytest.raises(LinuxObservationUnknown):native_process_tree_rss(1,proc_root=tmp_path,page_size=1)


def test_native_reused_root_is_unknown(tmp_path):
    fixture_proc(tmp_path,1,0)
    with pytest.raises(LinuxObservationUnknown):native_process_tree_rss(1,proc_root=tmp_path,page_size=1,expected_root_birth=999)



def test_native_includes_children_spawned_from_other_threads(tmp_path):
    fixture_proc(tmp_path,1,0,"2")
    fixture_proc(tmp_path,2,1)
    fixture_proc(tmp_path,3,1)
    other=tmp_path/"1"/"task"/"11";other.mkdir()
    (other/"children").write_text("3")
    assert native_process_tree_rss(1,proc_root=tmp_path,page_size=1)["pids"]==[1,2,3]


def test_native_cyclic_children_unknown(tmp_path):
    fixture_proc(tmp_path,1,0,"1")
    with pytest.raises(LinuxObservationUnknown):native_process_tree_rss(1,proc_root=tmp_path,page_size=1)



def cpu_record(total,idle,usage,observer=3):
    return {"hz":100,"cpu_count":16,"snapshot_read_seconds":0.001,
            "ct_init_birth":55,"system_total_ticks":total,"system_idle_ticks":idle,
            "observer_cpu_ticks":observer,"owned_groups":{"worker":{"usage_usec":usage,"cgroup_inode":20}}}


def test_cgroup_cpu_conservative_upper_bound_and_unit_conversion():
    # 25% total busy, 12.625% measured own; add conservative read/clock bound.
    result=external_cgroup_cpu_percent(cpu_record(1000,900,1000000),cpu_record(1800,1500,2000000,4))
    assert result["total_cpu_percent"]==25
    assert result["owned_cpu_percent"]==12.625
    assert result["cpu_percent"]==15.125


@pytest.mark.parametrize("changed",[{"hz":0},{"ct_init_birth":56},{"observer_cpu_ticks":0},{"system_total_ticks":999},{"owned_groups":{}}])
def test_cgroup_missing_reset_or_changed_identity_unknown(changed):
    after=cpu_record(1800,1500,2000000,4);after.update(changed)
    with pytest.raises(LinuxObservationUnknown):external_cgroup_cpu_percent(cpu_record(1000,900,1000000),after)


def test_cgroup_inode_reuse_unknown():
    after=cpu_record(1800,1500,2000000,4);after["owned_groups"]["worker"]["cgroup_inode"]=21
    with pytest.raises(LinuxObservationUnknown):external_cgroup_cpu_percent(cpu_record(1000,900,1000000),after)


def test_cgroup_owned_more_than_busy_beyond_bound_unknown():
    with pytest.raises(LinuxObservationUnknown):external_cgroup_cpu_percent(cpu_record(1000,900,1000000),cpu_record(1800,1500,4000000,4))


def test_native_reenumerates_threads_that_exit_before_children_read(tmp_path,monkeypatch):
    from pathlib import Path
    import errno
    fixture_proc(tmp_path,1,0,'2');fixture_proc(tmp_path,2,1)
    thread=tmp_path/'1'/'task'/'11';thread.mkdir();(thread/'children').write_text('')
    original=Path.read_text;calls=[]
    def read(path,*args,**kwargs):
        if path==thread/'children':
            calls.append(1);path.unlink();thread.rmdir()
            raise FileNotFoundError(errno.ENOENT,'exited thread',str(path))
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    result=native_process_tree_rss(1,proc_root=tmp_path,page_size=1)
    assert result['pids']==[1,2] and result['inventory_retries']==1 and len(calls)==1


def test_native_changing_thread_set_with_same_verified_process_children_is_known(tmp_path,monkeypatch):
    from pathlib import Path
    fixture_proc(tmp_path,1,0,'2');fixture_proc(tmp_path,2,1)
    original=Path.read_text;calls=0
    def read(path,*args,**kwargs):
        nonlocal calls
        if path==tmp_path/'1'/'stat':
            calls+=1
            if calls==2:
                thread=tmp_path/'1'/'task'/'11';thread.mkdir();(thread/'children').write_text('')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    assert native_process_tree_rss(1,proc_root=tmp_path,page_size=1)['pids']==[1,2]


def test_native_reenumeration_is_bounded_and_does_not_hide_persistent_unknown(tmp_path,monkeypatch):
    from pathlib import Path
    import errno
    fixture_proc(tmp_path,1,0)
    original=Path.read_text;calls=[]
    def read(path,*args,**kwargs):
        if path.name=='children':
            calls.append(1);raise FileNotFoundError(errno.ENOENT,'unknown thread',str(path))
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    with pytest.raises(LinuxObservationUnknown):native_process_tree_rss(1,proc_root=tmp_path,page_size=1)
    assert len(calls)==3


def test_native_rescans_live_tree_when_child_exits_during_stat_read(tmp_path,monkeypatch):
    from pathlib import Path
    fixture_proc(tmp_path,1,0,'2');fixture_proc(tmp_path,2,1)
    original=Path.read_text;reads=0
    def read(path,*args,**kwargs):
        nonlocal reads
        if path==tmp_path/'2'/'stat':
            reads+=1
            if reads==2:
                path.unlink();(tmp_path/'1'/'task'/'1'/'children').write_text('')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    result=native_process_tree_rss(1,proc_root=tmp_path,page_size=1)
    assert result['pids']==[1] and result['tree_retries']==1


def test_native_known_child_pid_reuse_is_not_rescanned_as_exit(tmp_path,monkeypatch):
    from pathlib import Path
    fixture_proc(tmp_path,1,0,'2');fixture_proc(tmp_path,2,1)
    original=Path.read_text;reads=0
    def read(path,*args,**kwargs):
        nonlocal reads
        if path==tmp_path/'2'/'stat':
            reads+=1
            if reads==2:return stat(pid=2,parent=1,start=999)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    with pytest.raises(LinuxObservationUnknown):native_process_tree_rss(1,proc_root=tmp_path,page_size=1)
    assert reads==2


def test_exiting_task_catalog_gets_bounded_scheduler_yield_before_rescan(tmp_path,monkeypatch):
    import table_acceptance_linux as module
    fixture_proc(tmp_path,1,0)
    ghost=tmp_path/'1'/'task'/'11';ghost.mkdir()  # catalog entry outlives readable children file
    pauses=[]
    def release():
        pauses.append(1);ghost.rmdir()
    monkeypatch.setattr(module,'_inventory_pause',release,raising=False)
    result=module.native_process_tree_rss(1,proc_root=tmp_path,page_size=1)
    assert result['pids']==[1] and len(pauses)==1 and result['inventory_retries']==1



def zombie_stat(**values):
    return stat(**values).replace(') S ', ') Z ')


def test_verified_zombie_child_keeps_identity_and_contributes_actual_zero_rss():
    rows={1:parse_process_stat(stat(pid=1,parent=0,rss=4),page_size=1),
          2:parse_process_stat(zombie_stat(pid=2,parent=1,rss=0),page_size=1)}
    sample=process_tree_sample(rows,1)
    assert sample['rss_bytes']==4 and sample['pids']==[1,2]
    assert sample['birth_ids']=={1:123,2:123}


@pytest.mark.parametrize('state,rss',[('S',0),('Z',4)])
def test_zombie_exception_does_not_accept_live_zero_or_invalid_zombie_rss(state,rss):
    rows={1:parse_process_stat(stat(pid=1,parent=0,rss=4),page_size=1),
          2:parse_process_stat(stat(pid=2,parent=1,rss=rss).replace(') S ', ') '+state+' '),page_size=1)}
    with pytest.raises(LinuxObservationUnknown):process_tree_sample(rows,1)


def test_zombie_root_is_not_a_live_measurement_root():
    row=parse_process_stat(zombie_stat(pid=1,parent=0,rss=0),page_size=1)
    with pytest.raises(LinuxObservationUnknown):process_tree_sample({1:row},1)


def test_native_verified_zombie_child_checks_both_identity_inventories(tmp_path):
    fixture_proc(tmp_path,1,0,'2');fixture_proc(tmp_path,2,1,rss=0)
    (tmp_path/'2/stat').write_text(zombie_stat(pid=2,parent=1,rss=0))
    assert native_process_tree_rss(1,proc_root=tmp_path,page_size=1)['rss_bytes']==4


def test_native_child_becoming_zombie_rebuilds_entire_current_tree(tmp_path,monkeypatch):
    from pathlib import Path
    fixture_proc(tmp_path,1,0,'2');fixture_proc(tmp_path,2,1)
    original=Path.read_text;reads=[0]
    def read(path,*args,**kwargs):
        if path==tmp_path/'2/stat':
            reads[0]+=1
            if reads[0]>=2:return zombie_stat(pid=2,parent=1,rss=0)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    sample=native_process_tree_rss(1,proc_root=tmp_path,page_size=1)
    assert sample['rss_bytes']==4 and sample['tree_retries']==1
