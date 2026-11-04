"""Native Linux handles for exact-Pod loopback forwards and one stream client."""
from __future__ import annotations

import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from test_scripts.replica_frontend_http import PrivateFiles


class OwnedProcesses:
    def __init__(self, directory, value):
        self.files, self.value = PrivateFiles(directory), value

    @staticmethod
    def describe(pid):
        path = Path('/proc') / str(pid)
        try:
            metadata = path.stat()
            fields = (path / 'stat').read_text().rsplit(') ', 1)[1].split()
            if fields[0] == 'Z': return None
            argv = (path / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
            return {'pid': pid, 'uid': metadata.st_uid, 'start_ticks': fields[19],
                    'argv': [arg.decode() for arg in argv]}
        except FileNotFoundError: return None

    def forward(self, slot, pod, port):
        if port not in {3000, 8000} or not re.fullmatch('[a-z0-9][-a-z0-9.]{0,252}', pod['name']):
            raise ValueError('Invalid native Pod forward')
        argv = ['kubectl', '--context', self.value['context'], '-n', self.value['namespace'],
                'port-forward', 'pod/' + pod['name'], '--address=127.0.0.1', '--pod-running-timeout=20s', '0:' + str(port)]
        self.files.once(slot + '.intent.json', {'pod': pod, 'port': port, 'argv': argv})
        path = self.files.path(slot + '.log')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as output:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
        info = self.describe(child.pid)
        if info is None or info['argv'] != argv or info['uid'] != os.getuid():
            raise ValueError('Native forward did not start as the owned process')
        self.files.once(slot + '.pid.json', info)
        for _ in range(100):
            if self.describe(child.pid) != info: raise ValueError('Native forward exited before readiness')
            try: return self.route(slot)
            except ValueError: time.sleep(.1)
        raise ValueError('Observe the same native forward; do not start another')

    def route(self, slot):
        expected = self.files.read(slot + '.pid.json')
        intent = self.files.read(slot + '.intent.json')
        if expected['argv'] != intent['argv'] or self.describe(expected['pid']) != expected:
            raise ValueError('Native forward PID identity differs')
        path = self.files.path(slot + '.log')
        with path.open('rb') as stream: log = stream.read(65537)
        if len(log) > 65536: raise ValueError('Native forward log exceeds its bound')
        ports = re.findall(r'^Forwarding from 127[.]0[.]0[.]1:([0-9]+) -> ' + str(intent['port']) + r'\s*$',
                           log.decode(), re.MULTILINE)
        if len(set(ports)) != 1 or not 1 <= int(ports[0]) <= 65535:
            raise ValueError('Native loopback forward is not ready')
        return {'url': 'http://127.0.0.1:' + ports[0], 'pod': intent['pod'], 'slot': slot}

    def start_client(self, program, configuration):
        self.files.once('loss-client-input.json', configuration)
        argv = [sys.executable, '-c', program, str(self.files.path('loss-client-input.json'))]
        self.files.once('client.intent.json', {'argv': argv})
        fd = os.open(self.files.path('client.log'), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as output:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
        info = self.describe(child.pid)
        if info is None or info['argv'] != argv or info['uid'] != os.getuid():
            raise ValueError('Native stream client identity unavailable; inspect same intent')
        self.files.once('client.pid.json', info)

    def signal_owned(self, expected):
        # A pidfd keeps signalling bound to that process across a PID reuse race.
        if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
            raise ValueError('Native pidfd support required')
        if self.describe(expected['pid']) != expected: return
        try: fd = os.pidfd_open(expected['pid'])
        except ProcessLookupError: return
        try:
            if self.describe(expected['pid']) != expected: return
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            for _ in range(60):
                if self.describe(expected['pid']) != expected: return
                time.sleep(.1)
            signal.pidfd_send_signal(fd, signal.SIGKILL)
            for _ in range(30):
                if self.describe(expected['pid']) != expected: return
                time.sleep(.1)
            raise ValueError('Owned native process has not exited')
        finally: os.close(fd)

    def stop(self, slot):
        if self.files.path(slot + '.closed.json').exists():
            expected = self.files.read(slot + '.pid.json')
            if self.describe(expected['pid']) == expected: raise ValueError('Closed native handle is still live')
            return
        expected = self.files.read(slot + '.pid.json')
        intent = self.files.read(slot + '.intent.json')
        if expected['argv'] != intent['argv']: raise ValueError('Native handle command differs')
        if self.describe(expected['pid']) == expected: self.signal_owned(expected)
        if self.describe(expected['pid']) == expected: raise ValueError('Native owned handle remains live')
        self.files.once(slot + '.closed.json', {'original_process_gone': True})
