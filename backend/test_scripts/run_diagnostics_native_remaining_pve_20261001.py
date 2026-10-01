"""Numeric PVE observer for the authorized remaining acceptance queue."""
import subprocess
if __name__ == '__main__':
    import json
    import threading
    import time
    from pathlib import Path
    stage='native-v16-remaining-live-20261001'
    out=Path('/root/okf-memory-budget-20261001T004114Z')
    path=out/(stage+'-pve-metrics.jsonl');assert not path.exists()
    finished=threading.Event();status={'stage':stage,'started_epoch':time.time(),'observer_errors':[]}
    def numeric_sample():
        mem={}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key,_,value=line.partition(':')
            if key in ('MemTotal','MemAvailable'):mem[key]=int(value.split()[0])*1024
        vm=dict(line.split() for line in Path('/proc/vmstat').read_text().splitlines())
        io=next(line for line in Path('/proc/pressure/io').read_text().splitlines() if line.startswith('full '))
        row={'at_unix':time.time(),'at_monotonic':time.monotonic(),'mem':mem,'swap_in_pages':int(vm['pswpin']),'swap_out_pages':int(vm['pswpout']),'io_full_total_us':int(io.split('total=')[1]),'guests':{}}
        for guest in ('101','102'):
            cg=Path('/sys/fs/cgroup/lxc')/guest
            row['guests'][guest]={'memory_current_bytes':int((cg/'memory.current').read_text()),'swap_current_bytes':int((cg/'memory.swap.current').read_text()),'memory_events':{k:int(v) for k,v in (line.split() for line in (cg/'memory.events').read_text().splitlines())}}
        return row
    def observe():
        try:
            with path.open('x') as sink:
                while not finished.is_set():
                    sink.write(json.dumps(numeric_sample())+'\n');sink.flush()
                    if finished.wait(2):break
        except Exception as exc:status['observer_errors'].append(type(exc).__name__)
    worker=threading.Thread(target=observe);worker.start()
    try:
        result=subprocess.run(['pct','exec','102','--','python3','/opt/okf-diag-levels-20260929/backend/test_scripts/run_diagnostics_native_v16_remaining_20261001.py'],check=False)
        status['exit_code']=result.returncode
    finally:
        finished.set();worker.join(timeout=3);status['finished_epoch']=time.time()
        (out/(stage+'-pve-status.json')).write_text(json.dumps(status,indent=2)+'\n')
        print(json.dumps(status),flush=True)
    raise SystemExit(result.returncode)
