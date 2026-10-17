"""Bounded, owned-process coordinator for one immutable acceptance batch.

Command completion is only collection. Product acceptance needs the independent
latency/resource evaluator. No shell, command output or document text is logged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


_BOOTSTRAP = r"""
import json, subprocess, sys
from pathlib import Path
command = json.loads(sys.argv[1])
output = Path(sys.argv[2])
if sys.stdin.readline().strip() != 'go':
    raise SystemExit(2)
try:
    code = subprocess.call(command)
except Exception:
    code = 2
pending = output.with_suffix('.pending')
pending.write_text(json.dumps({'returncode': code}), encoding='utf-8')
pending.replace(output)
# Keep the owned session leader alive until the coordinator closes its group.
sys.stdin.readline()
"""


def _birth(pid):
    if sys.platform != 'win32':
        body = Path('/proc', str(pid), 'stat').read_text()
        return int(body.rpartition(') ')[2].split()[19])
    import ctypes
    from ctypes import wintypes as W
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    opener = kernel.OpenProcess; opener.argtypes = [W.DWORD, W.BOOL, W.DWORD]; opener.restype = W.HANDLE
    close = kernel.CloseHandle; close.argtypes = [W.HANDLE]; close.restype = W.BOOL
    query = kernel.GetProcessTimes; query.argtypes = [W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4; query.restype = W.BOOL
    handle = opener(0x1000, False, pid)
    if not handle:
        raise OSError('Owned process identity unavailable')
    try:
        values = [W.FILETIME() for _ in range(4)]
        if not query(handle, *(ctypes.byref(v) for v in values)):
            raise OSError('Owned process birth unavailable')
        return values[0].dwLowDateTime | (values[0].dwHighDateTime << 32)
    finally:
        close(handle)


def stop_if_owned(identity, *, read_birth, stop):
    try:
        if read_birth(identity['pid']) != identity['birth_id']:
            return False
    except (OSError, ValueError, KeyError):
        return False
    stop()
    return True


class _WindowsJob:
    """Kernel-owned job handle kills this bootstrap and its descendants only."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes as W
        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', W.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                        ('active_limit', W.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', W.DWORD), ('scheduling', W.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        create = self.kernel.CreateJobObjectW; create.argtypes = [ctypes.c_void_p, W.LPCWSTR]; create.restype = W.HANDLE
        self.close = self.kernel.CloseHandle; self.close.argtypes = [W.HANDLE]; self.close.restype = W.BOOL
        self.assign = self.kernel.AssignProcessToJobObject; self.assign.argtypes = [W.HANDLE, W.HANDLE]; self.assign.restype = W.BOOL
        set_info = self.kernel.SetInformationJobObject; set_info.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]; set_info.restype = W.BOOL
        self.handle = create(None, None)
        if not self.handle:
            raise OSError('Owned job creation failed')
        limits = Extended(); limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE
        if not set_info(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.stop(); raise OSError('Owned job limits unavailable')

    def attach(self, process):
        if not self.assign(self.handle, int(process._handle)):
            raise OSError('Owned process job assignment failed')

    def stop(self):
        if self.handle:
            self.close(self.handle); self.handle = None


def _write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def _ready(check):
    try:
        return check() is True
    except Exception:
        return False


def run_owned_command(command, *, output_dir, timeout_seconds, readiness):
    if (not isinstance(command, list) or not command or any(not isinstance(v, str) or not v for v in command)
            or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 21600):
        raise ValueError('Invalid bounded command')
    area = Path(output_dir); area.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    manifest = {'schema_version': 1, 'status': 'invalid', 'exit_code': 2,
                'reason': 'readiness_failed', 'owned_process': None, 'cleanup_complete': True,
                'command_returncode': None, 'timeout_seconds': timeout_seconds,
                'command_sha256': hashlib.sha256(json.dumps(command).encode()).hexdigest()}
    process = None; job = None
    try:
        if _ready(readiness):
            job = _WindowsJob() if sys.platform == 'win32' else None
            process = subprocess.Popen([sys.executable, '-c', _BOOTSTRAP, json.dumps(command), str(area.resolve() / 'completion.json')],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=sys.platform != 'win32',
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            # Bootstrap is blocked on stdin: no provider/descendant before job assignment.
            if job: job.attach(process)
            manifest['owned_process'] = {'pid': process.pid, 'birth_id': _birth(process.pid)}
            process.stdin.write(b'go\n'); process.stdin.flush()
            while True:
                if time.monotonic() - started >= timeout_seconds:
                    manifest['reason'] = 'deadline_exceeded'; break
                if not _ready(readiness):
                    manifest['reason'] = 'observer_lost'; break
                completion = area / 'completion.json'
                if completion.exists():
                    code = json.loads(completion.read_text(encoding='utf-8'))['returncode']
                    manifest['command_returncode'] = code
                    if type(code) is int and code == 0:
                        manifest.update(status='collected', exit_code=0, reason='command_completed')
                    else:
                        manifest['reason'] = 'command_failed'
                    break
                if process.poll() is not None:
                    manifest['reason'] = 'bootstrap_lost'; break
                time.sleep(min(.1, max(0, timeout_seconds - (time.monotonic() - started))))
    except Exception as error:
        manifest['reason'] = 'coordinator_error'
        manifest['error_type'] = type(error).__name__
    finally:
        if process is not None:
            identity = manifest['owned_process']
            if identity is not None:
                stop = job.stop if job else lambda: os.killpg(process.pid, signal.SIGKILL)
                try:
                    manifest['cleanup_complete'] = stop_if_owned(identity, read_birth=_birth, stop=stop)
                except OSError:
                    manifest['cleanup_complete'] = False
            else:
                # No command was released. Popen retains the exact process handle.
                process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                manifest['cleanup_complete'] = False
            process.stdin.close()
        if job: job.stop()
        if not manifest['cleanup_complete']:
            manifest.update(status='invalid', exit_code=2, reason='cleanup_unknown')
        manifest['elapsed_seconds'] = time.monotonic() - started
        _write(area / 'command-manifest.json', manifest)
    return manifest


def run_batch(commands, *, output_dir, timeout_seconds, readiness, total_timeout_seconds=86400):
    if (type(total_timeout_seconds) not in (int, float)
            or not math.isfinite(total_timeout_seconds) or not 0 < total_timeout_seconds <= 86400
            or not isinstance(commands, list) or not commands):
        raise ValueError('Invalid total batch budget or command sequence')
    area = Path(output_dir); area.mkdir(parents=True, exist_ok=False)
    start = time.monotonic(); results = []
    for index, command in enumerate(commands):
        remaining = total_timeout_seconds - (time.monotonic() - start)
        if remaining <= 0: break
        result = run_owned_command(command, output_dir=area / str(index),
            timeout_seconds=min(timeout_seconds, remaining), readiness=readiness)
        results.append(result)
        if result['exit_code'] != 0: break
    complete = len(results) == len(commands) and bool(results) and all(r['exit_code'] == 0 for r in results)
    manifest = {'status': 'collected' if complete else 'invalid', 'exit_code': 0 if complete else 2,
                'commands': results, 'scope': 'collection only; independent evaluator required'}
    _write(area / 'batch-manifest.json', manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=('latency', 'resources'), required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        args.output_dir.mkdir(parents=True, exist_ok=False)
    except OSError:
        return 2
    try:
        protocol = json.loads(args.protocol.read_text(encoding='utf-8'))
        runner = protocol['runner']
        measurement_path = Path(runner['measurements_file']).resolve()
        if not measurement_path.is_relative_to(args.output_dir.resolve()) or measurement_path.exists():
            raise ValueError('Measurements must be newly created inside this batch')
        readiness_file = Path(runner['readiness_file'])
        def readiness():
            state = json.loads(readiness_file.read_text(encoding='utf-8'))
            return state.get('ready') is True and 0 <= time.monotonic() - state['observed_monotonic'] <= 1
        collected = run_batch(runner['commands'], output_dir=args.output_dir / 'commands',
            timeout_seconds=1800 if args.kind == 'latency' else 21600,
            total_timeout_seconds=1800 if args.kind == 'latency' else 86400, readiness=readiness)
        if collected['exit_code'] != 0:
            report = {'status': 'invalid', 'exit_code': 2, 'errors': ['collection incomplete']}
        else:
            from benchmark_table_acceptance import evaluate, evaluate_resources
            measured = json.loads(measurement_path.read_text(encoding='utf-8'))
            report = (evaluate if args.kind == 'latency' else evaluate_resources)(protocol, measured)
    except (OSError, ValueError, TypeError, KeyError):
        report = {'status': 'invalid', 'exit_code': 2, 'errors': ['input or adapter contract invalid']}
    _write(args.output_dir / 'acceptance.json', report)
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
