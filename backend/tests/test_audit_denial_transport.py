"""Bounded audit transport contracts: faults must leave no detached DB writer."""
import asyncio
import os
import threading
import time
from types import SimpleNamespace

import anyio
import pytest
from sqlalchemy import create_engine


def transport():
    from app.services import audit_denial_transport
    return audit_denial_transport


def test_expired_budget_never_starts_resolver_or_connection(monkeypatch):
    t = transport()
    monkeypatch.setattr(t, 'resolve_names', lambda *a: pytest.fail('Expired work started DNS'))
    with pytest.raises(t.DenialTimeout):
        t.connect({'host': 'example.invalid'}, time.monotonic() - 1)


def test_pairs_preserve_hostname_port_and_multiple_addresses():
    t = transport()
    params = {'host': 'one.example,two.example', 'port': '5433,5434', 'sslmode': 'verify-full'}
    resolved = {'one.example': ['127.0.0.1', '::1'], 'two.example': ['127.0.0.2']}
    pairs = t.endpoints(params, resolved)
    assert [(p['host'], p['hostaddr'], p['port']) for p in pairs] == [
        ('one.example', '127.0.0.1', '5433'), ('one.example', '::1', '5433'), ('two.example', '127.0.0.2', '5434')]
    assert all(p['sslmode'] == 'verify-full' for p in pairs)
    assert params['host'] == 'one.example,two.example'


def test_ip_hostaddr_and_unix_socket_bypass_dns():
    t = transport()
    for params in ({'host': '127.0.0.1'}, {'host': 'name.example', 'hostaddr': '::1'}, {'host': '/tmp/owned-socket'}):
        assert t.names_to_resolve(params) == []
        assert len(t.endpoints(params, {})) == 1


def test_empty_hostaddr_uses_corresponding_name_and_validates_lists():
    t = transport()
    params = {'host': 'one.example,two.example', 'hostaddr': ',127.0.0.2', 'port': '5432'}
    assert t.names_to_resolve(params) == ['one.example']
    assert [p['hostaddr'] for p in t.endpoints(params, {'one.example': ['127.0.0.1']})] == ['127.0.0.1', '127.0.0.2']
    with pytest.raises(ValueError):
        t.endpoints({'host': 'one,two', 'port': '1,2,3'}, {})


def test_dns_helper_uses_clean_environment_and_is_reaped(monkeypatch):
    t = transport()
    for name in ('DATABASE_URL', 'APP_SECRET_KEY', 'PGPASSWORD', 'AUTH_TOKEN'):
        monkeypatch.setenv(name, 'SYNTHETIC-SECRET-DO-NOT-INHERIT')
    assert not any('SYNTHETIC-SECRET' in value for value in t.resolver_environment().values())
    before = t.resolver_process_count()
    assert t.resolve_names(['localhost'], time.monotonic() + 1)['localhost']
    assert t.resolver_process_count() == before


def test_real_dns_helper_timeout_reaps_stalled_threads(tmp_path, monkeypatch):
    t = transport()
    helper = tmp_path / 'resolver.py'
    helper.write_text('import time; time.sleep(30)', encoding='utf-8')
    monkeypatch.setattr(t, 'RESOLVER_PATH', helper)
    started = time.monotonic()
    assert t.resolve_names(['test.invalid'], started + .25) == {}
    assert time.monotonic() - started < 1
    assert t.resolver_process_count() == 0


def test_partial_dns_output_survives_other_name_timeout(tmp_path, monkeypatch):
    t = transport()
    helper = tmp_path / 'resolver.py'
    helper.write_text('import json,time; print(json.dumps(["ready.example",["127.0.0.1"]]),flush=True); time.sleep(30)', encoding='utf-8')
    monkeypatch.setattr(t, 'RESOLVER_PATH', helper)
    assert t.resolve_names(['ready.example', 'silent.example'], time.monotonic() + .35) == {'ready.example': ['127.0.0.1']}
    assert t.resolver_process_count() == 0


def test_injected_or_oversize_dns_results_are_rejected():
    t = transport()
    with pytest.raises(ValueError):
        t.decode_dns(b'["other.example",["127.0.0.1"]]\n', ['ready.example'])
    with pytest.raises(ValueError):
        t.decode_dns(b'x' * (t.MAX_IPC_BYTES + 1), ['ready.example'])


def test_effective_parameters_keep_options_and_environment(monkeypatch):
    t = transport()
    monkeypatch.setenv('PGHOST', 'environment.example')
    monkeypatch.setenv('PGPORT', '5440')
    engine = create_engine('postgresql+psycopg://user:synthetic@explicit.example/db?options=-c%20search_path%3Downed')
    try:
        params = t.connection_parameters(engine)
        assert params['host'] == 'explicit.example' and str(params['port']) == '5440'
        assert '-c search_path=owned' in params['options']
        assert 'statement_timeout=1000' in params['options']
    finally:
        engine.dispose()


def test_shared_limiter_saturation_does_not_queue_audit_worker(tmp_path, monkeypatch):
    from app.services import audit_security as security
    from tests.test_audit import make_client
    from tests.test_platform_audit_contract import request
    make_client(tmp_path, monkeypatch, audit_stdout_mode='durable')
    observed = threading.Event()
    monkeypatch.setattr(security, '_record_denial', lambda *a, **kw: observed.set())
    async def run():
        limiter = anyio.to_thread.current_default_thread_limiter()
        old = limiter.total_tokens
        limiter.total_tokens = 1
        await limiter.acquire()
        try:
            await asyncio.wait_for(security.emit_auth_denial_once(request(), 'session_absent'), .5)
        finally:
            limiter.release(); limiter.total_tokens = old
    asyncio.run(run())
    assert observed.is_set()


def test_overload_does_not_start_writer_and_signals_once(tmp_path, monkeypatch):
    from app.services import audit_security as security
    from tests.test_audit import make_client
    from tests.test_platform_audit_contract import request
    make_client(tmp_path, monkeypatch, audit_stdout_mode='durable')
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(security, '_denial_slots', slots)
    monkeypatch.setattr(security, '_record_denial', lambda *a, **kw: pytest.fail('Writer started while overloaded'))
    before = security.failure_count()
    req = request()
    asyncio.run(security.emit_auth_denial_once(req, 'session_absent'))
    asyncio.run(security.emit_auth_denial_once(req, 'session_absent'))
    assert security.failure_count() == before + 1
    slots.release()


def test_attempt_deadline_is_created_before_offload(tmp_path, monkeypatch):
    from app.services import audit_security as security
    from tests.test_audit import make_client
    from tests.test_platform_audit_contract import request
    make_client(tmp_path, monkeypatch, audit_stdout_mode='durable')
    captured = []
    monkeypatch.setattr(security, '_record_denial', lambda *a, **kw: captured.append(kw['deadline']))
    start = time.monotonic()
    asyncio.run(security.emit_auth_denial_once(request(), 'session_absent'))
    assert start + 3.9 < captured[0] < start + 4.1

def test_cancellation_keeps_slot_owned_until_worker_finishes(tmp_path, monkeypatch):
    from app.services import audit_security as security
    from tests.test_audit import make_client
    from tests.test_platform_audit_contract import request
    make_client(tmp_path, monkeypatch, audit_stdout_mode='durable')
    slots = threading.BoundedSemaphore(1)
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(security, '_denial_slots', slots)
    def worker(*a, **kw):
        entered.set()
        try:
            release.wait(2)
        finally:
            done.set()
    monkeypatch.setattr(security, '_record_denial', worker)
    async def run():
        pending = asyncio.create_task(security.emit_auth_denial_once(request(), 'session_absent'))
        assert await asyncio.to_thread(entered.wait, 1)
        pending.cancel()
        try:
            await pending
        except asyncio.CancelledError:
            pass
        try:
            acquired = slots.acquire(blocking=False)
            if acquired:
                slots.release()
            assert not acquired, 'Cancelled caller released an active writer slot'
        finally:
            release.set()
            assert await asyncio.to_thread(done.wait, 1)
        await asyncio.sleep(.05)
    asyncio.run(run())
    assert slots.acquire(blocking=False)
    slots.release()


def test_cleanup_failure_quarantines_admission_slot(tmp_path, monkeypatch):
    from app.services import audit_security as security
    from tests.test_audit import make_client
    from tests.test_platform_audit_contract import request
    make_client(tmp_path, monkeypatch, audit_stdout_mode='durable')
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(security, '_denial_slots', slots)
    monkeypatch.setattr(security, '_record_denial', lambda *a, **kw: False)
    asyncio.run(security.emit_auth_denial_once(request(), 'session_absent'))
    assert not slots.acquire(blocking=False)

def test_authentication_refusal_stops_failover_and_finishes_connection(monkeypatch):
    t = transport()
    pgconn = SimpleNamespace(status=t.pq.ConnStatus.AWAITING_RESPONSE,
                             used_password=False, needs_password=False)
    closed = []
    pgconn.connect_poll = lambda: t.pq.PollingStatus.FAILED
    pgconn.finish = lambda: closed.append(True)
    monkeypatch.setattr(t.pq, 'PGconn', SimpleNamespace(connect_start=lambda value: pgconn))
    with pytest.raises(t.psycopg.OperationalError) as exc:
        t._connect_one({'host': 'original.example', 'hostaddr': '127.0.0.1'}, time.monotonic() + 1)
    assert exc.value.audit_stop_failover is True
    assert closed == [True]


def test_sqlalchemy_context_is_not_sent_to_libpq(monkeypatch):
    t = transport()
    captured = []
    def start(value):
        captured.append(t.conninfo_to_dict(value.decode()))
        raise RuntimeError('Synthetic stop after validated conninfo')
    monkeypatch.setattr(t.pq, 'PGconn', SimpleNamespace(connect_start=start))
    with pytest.raises(RuntimeError, match='Synthetic stop'):
        t._connect_one({'host': '127.0.0.1', 'context': SimpleNamespace()}, time.monotonic() + 1)
    assert 'context' not in captured[0]


def test_sql_wait_closes_connection_without_default_cancel(monkeypatch):
    t = transport()
    pg = SimpleNamespace(status=t.pq.ConnStatus.OK, socket=0)
    closed = []
    pg.finish = lambda: closed.append(True)
    connection = t.DeadlineConnection(pg, time.monotonic() + 1)
    waits = []
    def timeout(*a, **kw):
        waits.append(kw['timeout'])
        raise TimeoutError('Synthetic wait timeout')
    monkeypatch.setattr(t.waiting, 'wait', timeout)
    monkeypatch.setattr(connection, '_try_cancel', lambda *a, **kw: pytest.fail('Extra cancel wait'))
    with pytest.raises(TimeoutError):
        connection.wait(iter(()))
    assert len(waits) == 1 and 0 < waits[0] <= 1 and closed == [True]

@pytest.mark.parametrize('platform,version,libpq,allowed', [
    ('linux', '3.3.6', 180006, True),
    ('win32', '3.3.6', 180004, True),
    ('linux', '3.3.6', 180004, False),
    ('win32', '3.3.6', 180006, False),
    ('darwin', '3.3.6', 180004, False),
    ('linux', '3.3.4', 180006, False),
])
def test_driver_guard_requires_proven_platform_build(monkeypatch, platform, version, libpq, allowed):
    t = transport()
    monkeypatch.setattr(t.sys, 'platform', platform)
    monkeypatch.setattr(t.psycopg, '__version__', version)
    monkeypatch.setattr(t.pq, 'version', lambda: libpq)
    if allowed:
        t.validate_driver()
    else:
        with pytest.raises(ValueError, match='tested psycopg/libpq'):
            t.validate_driver()


def test_driver_guard_requires_timeout_capability(monkeypatch):
    t = transport()
    monkeypatch.setattr(t.sys, 'platform', 'linux')
    monkeypatch.setattr(t.psycopg, '__version__', '3.3.6')
    monkeypatch.setattr(t.pq, 'version', lambda: 180006)
    monkeypatch.setattr(t.waiting, 'wait', lambda gen, socket: None)
    with pytest.raises(ValueError, match='tested psycopg/libpq'):
        t.validate_driver()


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows launcher descendant proof')
def test_windows_timeout_reaps_actual_resolver_interpreter(tmp_path, monkeypatch):
    import ctypes
    import concurrent.futures
    t = transport()
    marker = tmp_path / 'owned-interpreter.pid'
    helper = tmp_path / 'owned-interpreter.py'
    helper.write_text('import os,time\nfrom pathlib import Path\nPath(' + repr(str(marker)) + ').write_text(str(os.getpid()))\ntime.sleep(30)\n')
    monkeypatch.setattr(t, 'RESOLVER_PATH', helper)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    owned_handle = None
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(t.resolve_names, ['owned.invalid'], time.monotonic() + .8)
            until = time.monotonic() + .6
            while not marker.exists():
                assert time.monotonic() < until, 'Actual interpreter marker not observed'
                time.sleep(.01)
            # Retain this exact process handle before timeout; PID reuse cannot
            # redirect cleanup to another Windows process.
            owned_handle = kernel.OpenProcess(0x100000 | 0x1000 | 1, False, int(marker.read_text()))
            assert owned_handle
            assert future.result(timeout=2) == {}
            assert kernel.WaitForSingleObject(owned_handle, 1000) == 0, 'Resolver interpreter survived launcher cleanup'
            assert t.resolver_process_count() == 0
    finally:
        if owned_handle:
            if kernel.WaitForSingleObject(owned_handle, 0) == 258:
                kernel.TerminateProcess(owned_handle, 1)
                kernel.WaitForSingleObject(owned_handle, 1000)
            kernel.CloseHandle(owned_handle)
