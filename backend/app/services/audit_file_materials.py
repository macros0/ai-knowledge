"""Owned, bounded file/config IPC; snapshots live until their connection closes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4

HELPER = Path(__file__).with_name('audit_file_probe.py')
MAX_IPC = 32768
FILE_SECONDS = 1.0
CLEANUP_REAP_SECONDS = .1
NAMES = ('passfile', 'sslrootcert', 'sslcrl', 'sslcert', 'sslkey')
_processes = set()
_quarantined_materials = set()
_lock = threading.Lock()


class FileTimeout(TimeoutError):
    pass


class FileProfileError(ValueError):
    pass


class FileCleanupError(RuntimeError):
    audit_cleanup_incomplete = True


def process_count():
    with _lock:
        return len(_processes)


def helper_environment():
    return {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'LANG', 'LC_ALL', 'LC_CTYPE') if key in os.environ}


def run_operation(request, deadline, *, cleanup=False, reap_deadline=None):
    if deadline <= time.monotonic():
        raise FileTimeout('Audit file deadline expired')
    payload = json.dumps(request, ensure_ascii=True).encode()
    if len(payload) > MAX_IPC:
        raise FileProfileError('Audit file request exceeds limit')
    args = [sys.executable, '-I']
    if request['operation'] != 'defaults':
        args.append('-S')
    args += ['-X', 'utf8', str(HELPER)]
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=helper_environment(), close_fds=True,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    with _lock:
        _processes.add(process)
    output = []
    overflow = threading.Event()
    io_failed = threading.Event()

    def write():
        try:
            process.stdin.write(payload)
            process.stdin.close()
        except (OSError, ValueError):
            io_failed.set()

    def read():
        try:
            data = process.stdout.read(MAX_IPC + 1)
            output.append(data)
            if len(data) > MAX_IPC:
                overflow.set()
        except (OSError, ValueError):
            io_failed.set()

    threads = [threading.Thread(target=write, daemon=True), threading.Thread(target=read, daemon=True)]
    started_threads = []
    failure = None
    try:
        for thread in threads:
            thread.start()
            started_threads.append(thread)
        while process.poll() is None:
            if overflow.is_set():
                failure = FileProfileError('Audit file response exceeds limit')
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = FileTimeout('Audit file deadline expired')
                break
            try:
                process.wait(timeout=min(.02, remaining))
            except subprocess.TimeoutExpired:
                pass
    finally:
        cleanup_deadline = min(deadline + .25, time.monotonic() + .25) if reap_deadline is None else reap_deadline
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=max(.001, cleanup_deadline - time.monotonic()))
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise FileCleanupError('Audit file helper cleanup incomplete') from exc
        for thread in started_threads:
            thread.join(max(0, cleanup_deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            raise FileCleanupError('Audit file IPC cleanup incomplete')
        try:
            for pipe in (process.stdin, process.stdout):
                if pipe:
                    pipe.close()
        except (OSError, ValueError) as exc:
            raise FileCleanupError('Audit file IPC cleanup incomplete') from exc
        with _lock:
            _processes.discard(process)
    if failure is not None:
        raise failure
    if process.returncode or io_failed.is_set() or overflow.is_set() or len(output) != 1:
        raise FileProfileError('Audit file helper failed')
    try:
        result = json.loads(output[0])
    except (ValueError, UnicodeError) as exc:
        raise FileProfileError('Invalid audit file response') from exc
    if not isinstance(result, dict) or result.get('status') != 'ok':
        if cleanup or isinstance(result, dict) and result.get('status') == 'cleanup_incomplete':
            raise FileCleanupError('Audit file materials cleanup incomplete')
        raise FileProfileError('Unsupported audit file profile')
    return result


def connection_defaults(deadline):
    environment = {key: value for key, value in os.environ.items() if key.startswith('PG') or key == 'HOME'}
    if 'PGSERVICE' in environment:
        raise FileProfileError('Unsupported audit service profile')
    result = run_operation({'operation': 'defaults', 'environment': environment}, min(deadline, time.monotonic() + FILE_SECONDS))
    if set(result) != {'status', 'parameters'} or not isinstance(result['parameters'], dict):
        raise FileProfileError('Invalid audit defaults response')
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in result['parameters'].items()):
        raise FileProfileError('Invalid audit defaults response')
    return result['parameters']


class FileMaterials:
    def __init__(self, parameters, deadline=None):
        if deadline is not None and deadline <= time.monotonic():
            raise FileTimeout('Audit file deadline expired')
        self.parameters = dict(parameters)
        self.cleanup_deadline = deadline + .5 if deadline is not None else None
        self.owner = uuid4().hex
        # Selecting a path must not call tempfile.gettempdir(): its first call
        # probes/writes the filesystem in the parent. All creation is isolated.
        root = next((os.environ[key] for key in (('TEMP', 'TMP') if os.name == 'nt' else ('TMPDIR', 'TEMP', 'TMP')) if os.environ.get(key)), None)
        if root is None:
            # Shared base only: worker exclusively creates a UUID child (0700/Windows ACL)
            # and validates ownership/links. Parent must not probe the filesystem.
            root = str(Path(os.environ['SYSTEMROOT']) / 'Temp') if os.name == 'nt' and os.environ.get('SYSTEMROOT') else ('/tmp' if os.name != 'nt' else '')  # nosec B108
        self.base = Path(root)
        if not root or len(root) > 4096 or '\0' in root or not self.base.is_absolute():
            raise FileProfileError('Unsupported audit temporary directory')
        self.directory = self.base / ('okf-audit-files-' + self.owner + '-' + uuid4().hex)
        self.closed = False
        self.quarantined = False
        work_deadline = deadline if deadline is not None else time.monotonic() + 4
        try:
            result = run_operation({'operation': 'create', 'directory': str(self.directory), 'owner': self.owner},
                                   min(work_deadline, time.monotonic() + FILE_SECONDS))
            if result != {'status': 'ok'}:
                raise FileProfileError('Invalid audit creation response')
        except BaseException as exc:
            if getattr(exc, 'audit_cleanup_incomplete', False):
                self.quarantine()
            else:
                self.close()
            raise

    def quarantine(self):
        self.quarantined = True
        with _lock:
            _quarantined_materials.add(self)

    def close(self, *, deadline=None):
        if self.quarantined:
            raise FileCleanupError("Audit file materials remain quarantined")
        if self.closed:
            return
        if self.directory.parent != self.base or not self.directory.is_absolute():
            raise FileCleanupError('Audit file owner invalid')
        bound = deadline if deadline is not None else self.cleanup_deadline
        cleanup_deadline = min(time.monotonic() + .5, bound if bound is not None else float('inf'))
        try:
            run_operation({'operation': 'cleanup', 'directory': str(self.directory), 'owner': self.owner},
                          cleanup_deadline - CLEANUP_REAP_SECONDS, cleanup=True, reap_deadline=cleanup_deadline)
        except BaseException as exc:
            self.quarantine()
            raise FileCleanupError('Audit file materials cleanup incomplete') from exc
        self.closed = True
        with _lock:
            _quarantined_materials.discard(self)


def prepare_files(parameters, deadline):
    if deadline <= time.monotonic():
        raise FileTimeout('Audit file deadline expired')
    tls = parameters.get('sslmode', 'prefer') != 'disable'
    client_disabled = parameters.get('sslcertmode') == 'disable'
    if parameters.get('sslkeylogfile') or (tls and (
            parameters.get('sslrootcert') == 'system' or parameters.get('sslcrldir') or
            (not client_disabled and any(parameters.get(key) for key in ('sslcert', 'sslkey', 'sslpassword'))))):
        raise FileProfileError('Unsupported audit file profile')
    material = FileMaterials(parameters, deadline)
    try:
        fields = {name: parameters[name] for name in NAMES if name in parameters}
        result = run_operation({'operation': 'prepare', 'directory': str(material.directory), 'owner': material.owner,
                                'files': fields, 'uses_passfile': not bool(parameters.get('password')),
                                'tls': tls, 'client_disabled': client_disabled, 'home': os.environ.get('HOME')},
                               min(deadline, time.monotonic() + FILE_SECONDS))
        if (set(result) != {'status', 'files'} or not isinstance(result['files'], dict) or
                set(result['files']) != set(NAMES) or any(state not in ('ready', 'missing', 'ignored') for state in result['files'].values())):
            raise FileProfileError('Invalid audit file response')
        material.parameters.update({name: str(material.directory / name) for name in NAMES})
        return material
    except BaseException as exc:
        if getattr(exc, "audit_cleanup_incomplete", False):
            material.quarantine()
        else:
            material.close()
        raise
