"""Own direct-service observations acquired outside the pipeline Python GIL.

Probe code is frozen by SHA; samples retain their acquisition clock. Model-call
ownership changes require an acknowledgement before the application continues.
This collector establishes no universal client exclusivity or CPU acceptance.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading
import time
import types
from table_acceptance_load import LoadUnknown


def observation_fault(error):
    record = {'status': 'direct_observer_failed', 'type': type(error).__name__,
        'reason': str(error) if isinstance(error, LoadUnknown) else 'Independent direct observation failed',
        'at_unix': time.time(), 'at_monotonic': time.monotonic()}
    details = getattr(error, 'observation_details', None)
    if isinstance(details, dict):
        try:
            json.dumps(details, allow_nan=False)
            record['observation_details'] = details
        except (ValueError, TypeError):
            pass
    return record


def publish_fault(config, record):
    name = config.get('fault_file')
    if name is None:
        return
    path = Path(name)
    if not path.is_absolute():
        raise LoadUnknown('Direct observer fault path must be absolute')
    if path.exists():
        return
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.' + str(threading.get_ident()) + '.tmp')
    temporary.write_text(json.dumps(record, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temporary, path)


class NativeObservationProcess:
    def __init__(self, *, probe, config):
        self.probe, self.config = probe, config
        self.samples, self.errors = [], []
        self.process = self.thread = None
        self.birth = None
        self.nonce = secrets.token_hex(24)
        self.pending = {}
        self.lock = threading.Lock()
        self.command_lock = threading.Lock()
        self.sequence = self.acknowledged = self.model_inflight = 0
        self.model_started_calls = self.model_completed_calls = 0
        self.final_model_reconciliation = None
        self.closing = False
        self.started = False
        self.start_timeout_seconds = 12
        self.shutdown_timeout_seconds = 12
        self.control_timeout_seconds = .5

    def error(self, error):
        record = observation_fault(error)
        self.errors.append(record)
        publish_fault(self.config, record)

    def _read(self):
        try:
            while True:
                raw = self.process.stdout.readline(1024 * 1024 + 1)
                if not raw:
                    if not self.closing:
                        raise LoadUnknown('Direct observer exited unexpectedly')
                    return
                if len(raw) > 1024 * 1024 or not raw.endswith(b'\n'):
                    raise LoadUnknown('Direct observer frame exceeds limit')
                message = json.loads(raw)
                if (message.get('nonce') != self.nonce or message.get('pid') != self.process.pid
                        or message.get('birth') != self.birth):
                    raise LoadUnknown('Direct observer identity changed')
                if 'sample' in message:
                    sample = message['sample']
                    at = message['at_monotonic']
                    if (not isinstance(sample, dict) or type(at) not in (int, float)
                            or not math.isfinite(at) or at < 0
                            or self.samples and at < self.samples[-1]['observer_at_monotonic']):
                        raise LoadUnknown('Direct observation clock unknown')
                    self.samples.append(dict(sample, observer_at_monotonic=at))
                if 'final_model_reconciliation' in message:
                    proof = message['final_model_reconciliation']
                    names = ('owned_started','owned_completed','native_reserved','native_started','native_completed')
                    if ('model_events_identity' not in self.config or not isinstance(proof, dict)
                            or proof.get('known') is not True or proof.get('terminal_api_idle') is not True
                            or proof.get('source_identity') != self.config['model_events_identity']
                            or any(type(proof.get(k)) is not int or proof[k] < 0 for k in names)
                            or proof['owned_started'] != self.model_started_calls
                            or proof['owned_completed'] != self.model_completed_calls
                            or len({proof[k] for k in names}) != 1):
                        raise LoadUnknown('Final native model reconciliation invalid')
                    self.final_model_reconciliation = proof
                if 'error' in message:
                    failure = LoadUnknown(message['error'])
                    failure.observation_details = message.get('observation_details')
                    self.error(failure)
                sequence = message.get('sequence')
                if sequence is not None:
                    with self.lock:
                        done = self.pending.pop(sequence, None)
                    if done is None:
                        raise LoadUnknown('Direct observer acknowledgement unknown')
                    if message.get('ok') is True:
                        self.acknowledged = sequence
                    done.set()
        except Exception as error:
            self.error(error)
        finally:
            with self.lock:
                for done in self.pending.values():
                    done.set()
                self.pending.clear()

    def _request(self, command, payload=None, *, timeout):
        with self.command_lock:
            if self.process.poll() is not None or not self.thread.is_alive():
                raise LoadUnknown('Direct observer is unavailable')
            done = threading.Event()
            with self.lock:
                self.sequence += 1
                sequence = self.sequence
                self.pending[sequence] = done
            frame = json.dumps({'nonce': self.nonce, 'sequence': sequence,
                                'command': command, 'payload': payload}, allow_nan=False).encode() + b'\n'
            if len(frame) > 256 * 1024:
                raise LoadUnknown('Direct observer command exceeds limit')
            try:
                self.process.stdin.write(frame)
                self.process.stdin.flush()
                if not done.wait(timeout) or self.acknowledged != sequence:
                    raise LoadUnknown('Direct observer acknowledgement timed out or missing')
            except Exception as error:
                self.error(error)
                raise

    def start(self):
        if self.process is not None:
            raise LoadUnknown('Direct observer already started')
        from run_table_acceptance import _birth
        parent = os.getpid()
        env = os.environ.copy()
        # Base Python avoids the Windows venv launcher PID. Inherit only this
        # child's import search path, including the current venv dependencies.
        env['PYTHONPATH'] = os.pathsep.join(str(Path(x or os.getcwd()).resolve()) for x in sys.path)
        command = [getattr(sys, '_base_executable', sys.executable), '-u', str(Path(__file__).resolve()),
                   str(parent), str(_birth(parent)), self.nonce]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        try:
            self.birth = _birth(self.process.pid)
            self.thread = threading.Thread(target=self._read, name='r2-direct-ipc', daemon=True)
            self.thread.start()
            self._request('init', {'probe': self.probe, 'config': self.config}, timeout=self.start_timeout_seconds)
            if not self.samples or self.errors:
                raise LoadUnknown('Initial independent direct sample is unavailable')
            self.started = True
        except Exception:
            self._stop()
            raise
        return self

    def update_model(self, count):
        if type(count) is not int or count < 0 or abs(count - self.model_inflight) != 1:
            raise LoadUnknown('Invalid owned model transition')
        self._request('model', count, timeout=self.control_timeout_seconds)
        if count > self.model_inflight:
            self.model_started_calls += 1
        else:
            self.model_completed_calls += 1
        self.model_inflight = count

    def sample(self):
        self._request('sample', timeout=self.shutdown_timeout_seconds)

    def _stop(self):
        self.closing = True
        try:
            self.process.stdin.close()
        except OSError as error:
            self.error(error)
        try:
            self.process.wait(timeout=self.shutdown_timeout_seconds)
        except subprocess.TimeoutExpired:
            self.error(LoadUnknown('Direct observer did not stop'))
            from run_table_acceptance import _birth
            if _birth(self.process.pid) == self.birth:
                self.process.kill()
                self.process.wait(timeout=self.shutdown_timeout_seconds)
        if self.process.returncode != 0:
            self.error(LoadUnknown('Independent direct observer exited unsuccessfully'))
        if self.thread is not None:
            self.thread.join(timeout=self.shutdown_timeout_seconds)
        if self.thread is None or not self.thread.is_alive():
            self.process.stdout.close()
        else:
            self.error(LoadUnknown('Direct IPC reader did not stop'))

        if 'model_events_identity' in self.config and self.final_model_reconciliation is None:
            self.error(LoadUnknown('Final native model reconciliation missing'))

    def close(self):
        if self.process is None or self.closing:
            return
        self.closing = True
        try:
            self._request('close', timeout=self.shutdown_timeout_seconds)
        except Exception as error:
            self.error(error)
        finally:
            self._stop()


def _commands():
    pending = b''
    while True:
        chunk = os.read(0, 4096)
        if not chunk:
            return
        pending += chunk
        if len(pending) > 256 * 1024:
            raise LoadUnknown('Direct command too large')
        while b'\n' in pending:
            raw, pending = pending.split(b'\n', 1)
            yield json.loads(raw)


def worker(parent, parent_birth, nonce):
    from run_table_acceptance import _birth
    birth = _birth(os.getpid())
    output_lock, state_lock = threading.Lock(), threading.Lock()
    stop = threading.Event()
    tasks = queue.Queue(maxsize=8)
    inflight = 0
    def owned():
        with state_lock:
            return inflight
    def send(**data):
        with output_lock:
            frame = dict(data, nonce=nonce, pid=os.getpid(), birth=birth)
            sys.stdout.buffer.write(json.dumps(frame, allow_nan=False, separators=(',', ':')).encode() + b'\n')
            sys.stdout.buffer.flush()
    commands = _commands()
    initial = next(commands)
    if initial.get('nonce') != nonce or initial.get('command') != 'init':
        return
    probe = None
    def observe(sequence=None):
        try:
            if _birth(parent) != parent_birth:
                raise LoadUnknown('Measured parent identity changed')
            sample = probe.sample(owned)
            send(sample=sample, at_monotonic=time.monotonic())
            ok = True
        except Exception as error:
            record = observation_fault(error)
            publish_fault(initial['payload']['config'], record)
            send(error=record['reason'], observation_details=record.get('observation_details'))
            ok = False
        if sequence is not None:
            send(sequence=sequence, ok=ok)
        return ok
    try:
        description = initial['payload']['probe']
        path = Path(description['path']);raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != description['sha256']:
            raise LoadUnknown('Frozen direct probe changed')
        module = types.ModuleType('r2_frozen_direct_probe');module.__file__ = str(path)
        exec(compile(raw, str(path), 'exec'), module.__dict__)
        probe = module.create_probe(initial['payload']['config'])
        if not observe(initial['sequence']):
            return
        def controls():
            nonlocal inflight
            try:
                for command in commands:
                    if command.get('nonce') != nonce or type(command.get('sequence')) is not int:
                        raise LoadUnknown('Direct control identity unknown')
                    if command['command'] == 'model':
                        count = command['payload']
                        with state_lock:
                            if type(count) is not int or count < 0 or abs(count - inflight) != 1:
                                raise LoadUnknown('Invalid model bracket control')
                            transition = getattr(probe, 'model_changed', None)
                            if transition is not None:
                                transition(inflight, count)
                            inflight = count
                        send(sequence=command['sequence'], ok=True)
                    elif command['command'] in ('sample', 'close'):
                        tasks.put(command)
                    else:
                        raise LoadUnknown('Unknown direct observer control')
            except Exception:
                send(error='Direct observer control failed')
            finally:
                stop.set()
        threading.Thread(target=controls, daemon=True).start()
        next_at = time.monotonic() + .5
        while not stop.is_set():
            try:
                command = tasks.get(timeout=max(0, next_at - time.monotonic()))
            except queue.Empty:
                if not observe():
                    return
                next_at += .5;continue
            valid = observe(command['sequence'])
            if not valid or command['command'] == 'close':
                return
    except Exception:
        send(error='Direct probe construction failed', sequence=initial['sequence'], ok=False)
    finally:
        if probe is not None:
            probe.close()
            reconciliation = getattr(probe, 'model_reconciliation', None)
            if reconciliation is not None:
                send(final_model_reconciliation=reconciliation)


if __name__ == '__main__':
    worker(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3])
