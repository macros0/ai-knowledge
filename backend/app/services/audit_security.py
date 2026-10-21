"""Verified auth audit helpers. Failure never grants access or blocks on stdout."""
import threading
import time

import anyio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from app import config as config_mod
from app.db.session import get_engine
from . import audit
from .audit_event import AuditContext

_lock = threading.Lock()
_failures = 0
_denial_slots = threading.BoundedSemaphore(4)


def enabled():
    from .audit_modes import get_audit_mode
    get_audit_mode(config_mod.get_settings())
    return True


def failure_count():
    with _lock:
        return _failures


def signal_failure():
    global _failures
    with _lock:
        _failures += 1
    try:
        from .diagnostics.recorder import emit_event
        emit_event('audit_unavailable')
    except Exception:
        pass


def context(request, *, identity=None, outcome='success', reason=None):
    provider = config_mod.get_settings().auth_provider
    return AuditContext(actor_type='user' if identity is not None else 'unknown',
                        outcome=outcome, method='simulation' if provider == 'simulation' else 'oidc' if provider == 'keycloak_oidc' else None,
                        reason=reason, request_id=getattr(request.state, 'request_id', None))


def record_auth_in_session(session, request, identity, action):
    if not enabled():
        return
    audit.record_in_session(session, action_type=action, user_id=identity.external_id, username=identity.username,
                            target_type='user', target_id=identity.external_id,
                            event_context=context(request, identity=identity))


def _claim(request):
    if not enabled():
        return False
    if request.method == 'OPTIONS' or request.url.path in {'/health', '/health/ready', f'{config_mod.get_settings().api_prefix}/auth/me'}:
        return False
    with _lock:
        if getattr(request.state, 'audit_denial_attempted', False):
            return False
        request.state.audit_denial_attempted = True
    return True


def _record_denial(request, reason, *, action='auth_access_denied', identity=None, deadline):
    engine = None
    owned_connections = []
    failed = False
    cleanup_complete = True
    try:
        if time.monotonic() >= deadline:
            raise TimeoutError('Audit denial deadline expired before worker')
        parent = get_engine()
        if parent.dialect.name == 'postgresql':
            from .audit_denial_transport import connect, connection_parameters
            params = connection_parameters(parent, deadline=deadline)
            def creator():
                connection = connect(params, deadline)
                owned_connections.append(connection)
                return connection
            engine = create_engine(parent.url, creator=creator, poolclass=NullPool)
        else:
            engine = create_engine(parent.url, connect_args={'timeout': .5}, pool_size=1, max_overflow=0, pool_timeout=.5)
        if identity is None:
            identity = getattr(request.state, 'audit_identity', None)
        with sessionmaker(bind=engine).begin() as session:
            audit.record_in_session(session, action_type=action, user_id=identity.external_id if identity else None,
                                    username=identity.username if identity else None,
                                    event_context=context(request, identity=identity, outcome='denied', reason=reason))
    except Exception as exc:
        failed = True
        cleanup_complete = not getattr(exc, "audit_cleanup_incomplete", False)
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception as exc:
                failed = True
                cleanup_complete = cleanup_complete and not getattr(exc, 'audit_cleanup_incomplete', False)
        # NullPool logs and suppresses exceptions from DBAPI close. The attempt
        # owner must inspect cleanup independently before returning its slot.
        for connection in owned_connections:
            try:
                connection.close()
            except Exception as exc:
                failed = True
                cleanup_complete = cleanup_complete and not getattr(exc, 'audit_cleanup_incomplete', False)
    if failed:
        signal_failure()
    return cleanup_complete


def record_denial_once(request, reason, *, action='auth_access_denied', identity=None):
    deadline = time.monotonic() + 4.0
    if not _claim(request):
        return
    if not _denial_slots.acquire(blocking=False):
        signal_failure()
        return
    _owned_denial(request, reason, deadline=deadline, action=action, identity=identity)


def _owned_denial(request, reason, *, deadline, **kwargs):
    # The worker, rather than its caller, owns the slot after offload begins.
    # Native asyncio cancellation must not release an active writer's capacity.
    release = True
    try:
        release = _record_denial(request, reason, deadline=deadline, **kwargs) is not False
    finally:
        if release:
            _denial_slots.release()


async def emit_auth_denial_once(request, reason, **kwargs):
    deadline = time.monotonic() + 4.0
    if not _claim(request):
        return
    if not _denial_slots.acquire(blocking=False):
        signal_failure()
        return
    state = ['pending']
    lock = threading.Lock()

    def worker():
        with lock:
            if state[0] != 'pending':
                return
            state[0] = 'running'
        _owned_denial(request, reason, deadline=deadline, **kwargs)

    try:
        await anyio.to_thread.run_sync(worker, limiter=anyio.CapacityLimiter(1), abandon_on_cancel=False)
    except BaseException:
        with lock:
            if state[0] == 'pending':
                state[0] = 'cancelled'
                _denial_slots.release()
        raise
