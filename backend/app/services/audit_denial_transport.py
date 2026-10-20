"""Owned PostgreSQL denial I/O with one absolute deadline and bounded DNS.

This adapter is deliberately scoped to denial attempts, not the shared DB pool.
The driver wait API is pinned and checked before durable PG readiness.
"""
import inspect
import ipaddress
import json
import os
import random
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

import psycopg
from psycopg import pq, waiting
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.adapt import AdaptersMap
from .audit_file_materials import connection_defaults, prepare_files, FileTimeout, FileProfileError as FileProfileError, HELPER as FILE_HELPER

WORK_SECONDS = 4.0
DNS_SECONDS = 1.0
ENDPOINT_SECONDS = 1.0
CLEANUP_SECONDS = .5
MAX_HOSTS = 8
MAX_ADDRESSES = 8
MAX_IPC_BYTES = 32768
RESOLVER_PATH = Path(__file__).with_name('audit_dns_probe.py')
_processes = set()
_process_lock = threading.Lock()


class DenialTimeout(psycopg.OperationalError):
    """The owned attempt exhausted its deadline; never retry its INSERT."""


class ResolverCleanupError(RuntimeError):
    """An owned resolver could not be reaped; its admission slot stays closed."""

    audit_cleanup_incomplete = True


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise DenialTimeout('Audit denial deadline expired')
    return value


def validate_driver():
    if (psycopg.__version__ != '3.3.6' or (sys.platform, pq.version()) not in {('linux', 180006), ('win32', 180004)}
            or 'timeout' not in inspect.signature(waiting.wait).parameters):
        raise ValueError('Durable PostgreSQL denial requires the tested psycopg/libpq build')
    if not RESOLVER_PATH.is_file() or not FILE_HELPER.is_file():
        raise ValueError('Audit DNS helper is missing')


def reject_service_profile(engine):
    # Empty service values also activate libpq service-file I/O. Inspect the
    # SQLAlchemy URL before normalization and refuse inherited profiles too.
    _, keywords = engine.dialect.create_connect_args(engine.url)
    if 'service' in engine.url.query or 'service' in keywords or 'PGSERVICE' in os.environ:
        raise ValueError('Audit denial service-file profile requires separate compatibility proof')


def connection_parameters(engine, *, deadline=None):
    deadline = time.monotonic() + WORK_SECONDS if deadline is None else deadline
    remaining(deadline)
    reject_service_profile(engine)
    args, keywords = engine.dialect.create_connect_args(engine.url)
    params = conninfo_to_dict(*args, **keywords)
    # libpq defaults include PG environment overrides; explicit URL args win.
    try:
        defaults = connection_defaults(deadline)
    except FileTimeout as exc:
        raise DenialTimeout('Audit file configuration deadline expired') from exc
    params = {**defaults, **params}
    if params.get('service'):
        raise ValueError('Audit denial service-file profile requires separate compatibility proof')
    params['options'] = params.get('options', '') + ' -c statement_timeout=1000 -c lock_timeout=500'
    _host_pairs(params)
    if params.get('target_session_attrs', 'any') not in ('any', 'read-write', 'read-only', 'primary', 'standby', 'prefer-standby'):
        raise ValueError('Invalid audit target_session_attrs')
    make_conninfo('', **{key: value for key, value in params.items() if key != 'context'})
    # Preserve configured encryption. No fallback disabling SSL/GSS is allowed.
    return params


def _literal(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _host_pairs(params):
    raw_host = params.get('host', '')
    hosts = raw_host.split(',') if raw_host else []
    addresses = params.get('hostaddr', '').split(',') if params.get('hostaddr') else []
    count = max(len(hosts), len(addresses), 1)
    if count > MAX_HOSTS or (hosts and len(hosts) != count) or (addresses and len(addresses) != count):
        raise ValueError('Invalid or oversized audit host list')
    hosts = hosts or [''] * count
    addresses = addresses or [''] * count
    ports = str(params.get('port', '5432')).split(',')
    if len(ports) == 1:
        ports *= count
    if len(ports) != count:
        raise ValueError('Audit host/port list lengths differ')
    result = []
    for host, address, port in zip(hosts, addresses, ports):
        if len(host) > 254 and not host.startswith(('/', '@')):
            raise ValueError('Oversized audit hostname')
        if address and not _literal(address):
            raise ValueError('Audit hostaddr must be a numeric address')
        if not host and os.name == 'nt' and not address:
            host = 'localhost'
        result.append((host, address, port))
    return result


def names_to_resolve(params):
    return list(dict.fromkeys(host for host, address, _ in _host_pairs(params)
                             if host and not address and not _literal(host) and not host.startswith(('/', '@'))))


def endpoints(params, resolved):
    attempts = []
    for host, address, port in _host_pairs(params):
        if address or _literal(host):
            ready = [address or host]
        elif not host or host.startswith(('/', '@')):
            ready = ['']
        else:
            ready = resolved.get(host, [])
        for address in ready:
            attempt = {**params, 'host': host, 'port': port}
            if address:
                attempt['hostaddr'] = address
            else:
                attempt.pop('hostaddr', None)
            attempts.append(attempt)
    if params.get('load_balance_hosts') == 'random':
        random.SystemRandom().shuffle(attempts)
    if params.get('target_session_attrs') == 'prefer-standby':
        attempts = [{**p, 'target_session_attrs': 'standby'} for p in attempts] + [
            {**p, 'target_session_attrs': 'any'} for p in attempts]
    return attempts


def resolver_environment():
    # No PATH/PYTHONPATH/PG*/application variables or inherited credentials.
    return {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'LANG', 'LC_ALL', 'LC_CTYPE', 'RES_OPTIONS', 'LOCALDOMAIN') if key in os.environ}


def resolver_process_count():
    with _process_lock:
        return len(_processes)


def decode_dns(raw, names):
    if len(raw) > MAX_IPC_BYTES:
        raise ValueError('Oversized audit resolver response')
    result = {}
    for line in raw.splitlines():
        # A process killed during its final write can leave a partial last line.
        if not line.endswith(b']'):
            continue
        name, addresses = json.loads(line)
        if name not in names or name in result or not isinstance(addresses, list) or len(addresses) > MAX_ADDRESSES:
            raise ValueError('Invalid audit resolver response')
        result[name] = [str(ipaddress.ip_address(address)) for address in addresses]
    return result


def resolve_names(names, deadline):
    if not names:
        return {}
    remaining(deadline)
    if len(names) > MAX_HOSTS:
        raise ValueError('Too many audit resolver names')
    payload = json.dumps(names).encode('utf-8')
    if len(payload) > 4096:
        raise ValueError('Oversized audit resolver request')
    process = subprocess.Popen([sys.executable, '-I', '-S', '-X', 'utf8', str(RESOLVER_PATH)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=resolver_environment(), close_fds=True,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    with _process_lock:
        _processes.add(process)
    cleanup_deadline = None
    killed = False
    try:
        try:
            output, _ = process.communicate(payload, timeout=remaining(deadline))
        except subprocess.TimeoutExpired:
            killed = True
            cleanup_deadline = time.monotonic() + CLEANUP_SECONDS
            process.kill()
            try:
                output, _ = process.communicate(timeout=max(.001, cleanup_deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise ResolverCleanupError('Audit resolver cleanup incomplete') from exc
        if not killed and process.returncode != 0:
            raise ValueError('Audit resolver failed')
        return decode_dns(output, names)
    finally:
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=max(.001, (cleanup_deadline or (time.monotonic() + CLEANUP_SECONDS)) - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise ResolverCleanupError('Audit resolver cleanup incomplete') from exc
        if process.poll() is not None:
            for pipe in (process.stdin, process.stdout):
                if pipe:
                    pipe.close()
            with _process_lock:
                _processes.discard(process)


class DeadlineConnection(psycopg.Connection):
    def __init__(self, pgconn, deadline, context=None):
        super().__init__(pgconn)
        if context is not None:
            self._adapters = AdaptersMap(context.adapters)
        self.deadline = deadline
        self.prepare_threshold = None

    def close(self):
        try:
            super().close()
        finally:
            materials = getattr(self, '_audit_files', None)
            if materials is not None:
                materials.close()
                self._audit_files = None

    def wait(self, gen, interval=.1, timeout=None):
        # Connection.wait's default interruption handler performs another cancel
        # and wait. This owned connection is closed immediately instead.
        try:
            budget = remaining(self.deadline)
            if timeout is not None:
                budget = min(budget, timeout)
            return waiting.wait(gen, self.pgconn.socket, interval=min(interval, budget), timeout=budget)
        except BaseException:
            self.close()
            raise


def _check_target(connection, target):
    if target == 'any':
        return
    # Separate the target suitability probe from authentication. libpq can hide
    # several internal states in one connect_poll call, so a FAILED status does
    # not reliably distinguish bad credentials from a healthy primary rejected
    # by target_session_attrs=standby. These are the same server properties used
    # by libpq, queried without opening a transaction or changing server policy.
    connection.autocommit = True
    if target in ('read-write', 'read-only'):
        read_only = connection.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
        suitable = read_only == (target == 'read-only')
    else:
        standby = connection.execute('SELECT pg_is_in_recovery()').fetchone()[0]
        suitable = standby == (target == 'standby')
    connection.autocommit = False
    if not suitable:
        error = psycopg.OperationalError('Audit PostgreSQL target unavailable')
        error.audit_stop_failover = False
        raise error


def _connect_one(params, deadline):
    libpq_params = {key: value for key, value in params.items() if key != 'context'}
    target = libpq_params.get('target_session_attrs', 'any')
    libpq_params['target_session_attrs'] = 'any'
    pgconn = pq.PGconn.connect_start(make_conninfo('', **libpq_params).encode('utf-8'))
    owned = True
    seen_auth = False
    try:
        while True:
            remaining(deadline)
            status_before = pgconn.status
            state = pgconn.connect_poll()
            if state == pq.PollingStatus.OK:
                pgconn.nonblocking = 1
                connection = DeadlineConnection(pgconn, deadline, context=params.get('context'))
                _check_target(connection, target)
                owned = False
                return connection
            if state == pq.PollingStatus.FAILED:
                # Conservatively stop after reaching authentication. A TCP
                # refusal before startup permits failover; credentials aren't
                # retried on another endpoint after an authentication refusal.
                error = psycopg.OperationalError('Audit PostgreSQL connection refused')
                error.audit_stop_failover = seen_auth or status_before in (pq.ConnStatus.AWAITING_RESPONSE, pq.ConnStatus.AUTH_OK) or pgconn.used_password or pgconn.needs_password
                raise error
            seen_auth = seen_auth or status_before in (pq.ConnStatus.AWAITING_RESPONSE, pq.ConnStatus.AUTH_OK)
            if state not in (pq.PollingStatus.READING, pq.PollingStatus.WRITING):
                raise psycopg.OperationalError('Invalid audit PostgreSQL polling state')
            fd = pgconn.socket  # libpq can replace it during connect polling.
            if fd < 0:
                raise psycopg.OperationalError('Audit PostgreSQL socket unavailable')
            reading = [fd] if state == pq.PollingStatus.READING else []
            writing = [fd] if state == pq.PollingStatus.WRITING else []
            select.select(reading, writing, [], remaining(deadline))
    finally:
        if owned:
            pgconn.finish()


def _connect_prepared(params, deadline):
    remaining(deadline)
    validate_driver()
    names = names_to_resolve(params)
    resolved = resolve_names(names, min(deadline, time.monotonic() + DNS_SECONDS)) if names else {}
    attempts = endpoints(params, resolved)
    for index, attempt in enumerate(attempts):
        # Leave an opportunity for each remaining candidate instead of letting
        # the first four silent endpoints consume all of the global budget.
        quantum = min(ENDPOINT_SECONDS, remaining(deadline) / (len(attempts) - index))
        endpoint_deadline = min(deadline, time.monotonic() + quantum)
        try:
            connection = _connect_one(attempt, endpoint_deadline)
        except psycopg.OperationalError as exc:
            if getattr(exc, 'audit_stop_failover', False):
                raise
            continue
        # Endpoint connect quantum doesn't shorten the transaction's deadline.
        connection.deadline = deadline
        return connection
    raise psycopg.OperationalError('Audit PostgreSQL endpoints unavailable')

def connect(params, deadline):
    remaining(deadline)
    validate_driver()
    try:
        materials = prepare_files(params, deadline)
    except FileTimeout as exc:
        raise DenialTimeout('Audit file material deadline expired') from exc
    try:
        connection = _connect_prepared(materials.parameters, deadline)
        connection._audit_files = materials
        return connection
    except BaseException as exc:
        if getattr(exc, "audit_cleanup_incomplete", False):
            materials.quarantine()
        else:
            materials.close()
        raise
