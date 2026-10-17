"""Native RSS acquisition in an owned child, independent of the parent's GIL.

The observer is included in the measured tree. Missing samples, changed process
identities, broken IPC and unexpected termination fail closed.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading
import time
from table_acceptance_rss import RssUnknown

class NativeRssProcess:
    def __init__(self, record, trace, timeout):
        self.record, self.trace, self.timeout = record, trace, timeout
        self.process = self.thread = None
        self.nonce = secrets.token_hex(24)
        self.pending = {}
        self.lock = threading.Lock()
        self.sequence = 0
        self.closing = False
        self.acknowledged = 0
        self.birth = None

    def _read(self):
        try:
            while True:
                raw = self.process.stdout.readline(1024 * 1024 + 1)
                if not raw:
                    if not self.closing:
                        raise RssUnknown('RSS observer exited before completion')
                    break
                if len(raw) > 1024 * 1024 or not raw.endswith(b'\n'):
                    raise RssUnknown('RSS observer IPC frame exceeds limit')
                message = json.loads(raw)
                if (message.get('nonce') != self.nonce
                        or message.get('observer_pid') != self.process.pid
                        or message.get('observer_birth') != self.birth):
                    raise RssUnknown('RSS observer identity changed')
                if message.get('error'):
                    raise RssUnknown('Independent native RSS read failed')
                sample = message['sample']
                sample['birth_ids'] = {int(pid): birth for pid, birth in sample['birth_ids'].items()}
                self.record(message['phase'], sample, message['at_monotonic'])
                sequence = message.get('sequence')
                if sequence is not None:
                    with self.lock:
                        done = self.pending.pop(sequence, None)
                    if done is None:
                        raise RssUnknown('Unknown RSS boundary acknowledgement')
                    self.acknowledged = sequence
                    done.set()
        except Exception as error:
            self.trace.error(error)
        finally:
            with self.lock:
                for done in self.pending.values():
                    done.set()
                self.pending.clear()

    def start(self):
        from run_table_acceptance import _birth
        parent = os.getpid()
        command = [getattr(sys, '_base_executable', sys.executable), '-u', str(Path(__file__).resolve()),
                   str(parent), str(_birth(parent)), self.nonce]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        try:
            self.birth = _birth(self.process.pid)
        except Exception:
            self.close()
            raise
        self.thread = threading.Thread(target=self._read, name='r2-rss-ipc', daemon=True)
        self.thread.start()
        self.request('construction')
        if not self.trace.count or self.trace.error_count:
            self.close()
            raise RssUnknown('Independent RSS observer is unready')

    def request(self, phase, *, finish=False):
        if self.process.poll() is not None or not self.thread.is_alive():
            self.trace.error(RssUnknown('RSS observer unavailable at boundary'))
            return
        if finish:
            self.closing = True
        done = threading.Event()
        with self.lock:
            self.sequence += 1
            sequence = self.sequence
            self.pending[sequence] = done
        try:
            frame = json.dumps({'nonce': self.nonce, 'sequence': sequence,
                                'phase': phase, 'finish': finish}).encode() + b'\n'
            self.process.stdin.write(frame)
            self.process.stdin.flush()
            if not done.wait(self.timeout) or self.acknowledged != sequence:
                raise RssUnknown('Independent RSS boundary timed out or was not acknowledged')
        except Exception as error:
            self.trace.error(error)

    def close(self):
        self.closing = True
        try:
            self.process.stdin.close()
        except OSError as error:
            self.trace.error(error)
        try:
            self.process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self.trace.error(RssUnknown('RSS observer did not stop'))
            from run_table_acceptance import _birth
            if _birth(self.process.pid) == self.birth:
                self.process.kill()
                self.process.wait(timeout=self.timeout)
        if self.thread is not None:
            self.thread.join(timeout=self.timeout)
        alive = self.thread is not None and self.thread.is_alive()
        if alive:
            self.trace.error(RssUnknown('RSS IPC reader did not stop'))
        else:
            self.process.stdout.close()
        return self.process.poll() is not None and not alive


def worker(parent, parent_birth, nonce):
    from run_table_acceptance import _birth
    from table_acceptance_rss import PHASES
    if sys.platform == 'win32':
        from table_acceptance_rss import native_process_tree_rss
    else:
        from table_acceptance_linux import native_process_tree_rss
    commands = queue.Queue(maxsize=8)
    def read_commands():
        try:
            pending = b''
            while True:
                chunk = os.read(0, 4096)
                if not chunk:
                    break
                pending += chunk
                if len(pending) > 4096:
                    raise ValueError('Command too large')
                while b'\n' in pending:
                    raw, pending = pending.split(b'\n', 1)
                    command = json.loads(raw)
                    if command.get('nonce') != nonce:
                        raise ValueError('Command identity changed')
                    commands.put(command)
        finally:
            commands.put(None)
    threading.Thread(target=read_commands, daemon=True).start()
    birth = _birth(os.getpid())
    phase = 'construction'
    command = commands.get()
    while command is not None:
        if command:
            phase = command['phase']
            if phase not in PHASES or type(command['sequence']) is not int:
                raise ValueError('Invalid phase command')
        message = {'nonce': nonce, 'observer_pid': os.getpid(), 'observer_birth': birth,
                   'phase': phase, 'sequence': command.get('sequence')}
        try:
            message['sample'] = native_process_tree_rss(parent, expected_root_birth=parent_birth)
            message['at_monotonic'] = time.monotonic()
        except Exception:
            message['error'] = True
        try:
            sys.stdout.buffer.write(json.dumps(message, separators=(',', ':')).encode() + b'\n')
            sys.stdout.buffer.flush()
        except BrokenPipeError:
            return
        if message.get('error') or command.get('finish'):
            return
        try:
            command = commands.get(timeout=.2)
        except queue.Empty:
            command = {}


if __name__ == '__main__':
    worker(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3])
