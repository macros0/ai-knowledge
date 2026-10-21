"""Early service refusal must precede libpq defaults and database I/O."""
import os
import time
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from app.services import audit_denial_transport as transport

@pytest.mark.parametrize('url_service,environment', [(None,None),('',None),('url',None),(None,''),(None,'env'),('','env'),('url',''),('url','env')])
def test_service_precedence_refuses_before_native_defaults(monkeypatch, url_service, environment):
    monkeypatch.delenv('PGSERVICE', raising=False)
    monkeypatch.setenv('PGSERVICEFILE', 'synthetic-unreadable-service-file')
    if environment is not None:
        monkeypatch.setenv('PGSERVICE', environment)
    calls = []
    monkeypatch.setattr(transport, 'connection_defaults', lambda deadline: calls.append(True) or {})
    url = 'postgresql+psycopg://synthetic:synthetic@127.0.0.1:9/isolated'
    if url_service is not None:
        url = make_url(url).update_query_dict({'service': url_service})
    engine = create_engine(url)
    original = dict(os.environ)
    try:
        if url_service is not None or environment is not None:
            with pytest.raises(ValueError, match='service-file profile'):
                transport.connection_parameters(engine)
            assert calls == [], 'Service profile reached native defaults before refusal'
        else:
            assert transport.connection_parameters(engine)['host'] == '127.0.0.1'
            assert calls == [True]
        assert dict(os.environ) == original
    finally:
        engine.dispose()

@pytest.mark.parametrize('environment', ['', 'synthetic'])
def test_service_preflight_refuses_before_database_connect(monkeypatch, environment):
    from app.services import audit_runtime
    monkeypatch.setenv('PGSERVICE', environment)
    monkeypatch.setattr(transport, 'validate_driver', lambda: None)
    calls = []
    monkeypatch.setattr(transport, 'connection_defaults', lambda deadline: calls.append('defaults') or {})
    engine = create_engine('postgresql+psycopg://synthetic:synthetic@127.0.0.1:9/isolated')
    monkeypatch.setattr(engine, 'connect', lambda: pytest.fail('Refused profile opened shared database'))
    try:
        started = time.monotonic()
        with pytest.raises(ValueError, match='service-file profile'):
            audit_runtime.preflight_audit(SimpleNamespace(audit_stdout_mode='durable'), engine=engine)
        assert time.monotonic() - started < .5 and calls == []
    finally:
        engine.dispose()

def test_service_refusal_preserves_protected_http_denial(monkeypatch, tmp_path):
    from app.services import audit_security as security
    from tests.test_audit import make_client
    client = make_client(tmp_path, monkeypatch, auth_provider='simulation', audit_stdout_mode='durable')
    engine = create_engine('postgresql+psycopg://synthetic:synthetic@127.0.0.1:9/isolated')
    monkeypatch.setenv('PGSERVICE', '')
    monkeypatch.setattr(security, 'get_engine', lambda: engine)
    calls = []
    monkeypatch.setattr(transport, 'connection_defaults', lambda deadline: calls.append('defaults') or {})
    before = security.failure_count()
    try:
        started = time.monotonic()
        response = client.get('/api/audit')
        assert response.status_code == 401
        assert response.headers['www-authenticate'] == 'Bearer'
        assert response.headers.get('x-request-id')
        assert 'session' not in response.cookies
        assert time.monotonic() - started < 5
        assert security.failure_count() == before + 1 and calls == []
    finally:
        engine.dispose()


def test_service_lifespan_refuses_before_seed_and_dispatcher(monkeypatch, tmp_path):
    from app.services import audit_runtime, audit_outbox
    from tests.test_audit import make_client
    from app import main
    client = make_client(tmp_path, monkeypatch, audit_stdout_mode='durable')
    engine = create_engine('postgresql+psycopg://synthetic:synthetic@127.0.0.1:9/isolated')
    monkeypatch.setenv('PGSERVICE', '')
    monkeypatch.setattr(audit_runtime, 'get_engine', lambda: engine)
    monkeypatch.setattr('app.services.diagnostics.runtime.initialize_diagnostics', lambda: SimpleNamespace(available=False))
    monkeypatch.setattr(main, '_validate_runtime_dependencies', lambda: None)
    calls = []
    monkeypatch.setattr('app.db.session.init_db', lambda: calls.append('init_db'))
    monkeypatch.setattr('app.services.stopwords.ensure_seeded', lambda: pytest.fail('Refused service reached startup seed'))
    monkeypatch.setattr(audit_outbox, 'AuditDispatcher', lambda *a: pytest.fail('Refused service started dispatcher'))
    started = time.monotonic()
    try:
        with pytest.raises(ValueError, match='service-file profile'):
            with client:
                pytest.fail('Refused service reached application readiness')
        assert calls == [] and time.monotonic() - started < 5
    finally:
        engine.dispose()


def test_early_service_guard_skips_only_sqlite(monkeypatch):
    from app.services.audit_runtime import validate_audit_transport_profile
    monkeypatch.setenv('PGSERVICE', '')
    monkeypatch.setattr(transport, 'reject_service_profile', lambda *a: pytest.fail('SQLite invoked PostgreSQL guard'))
    engine=create_engine('sqlite://')
    try:
        assert validate_audit_transport_profile(SimpleNamespace(audit_stdout_mode='disabled'),engine=engine) is None
    finally:
        engine.dispose()


def test_db_only_also_refuses_service_profile_before_native_io(monkeypatch):
    from app.services.audit_runtime import validate_audit_transport_profile
    monkeypatch.setenv('PGSERVICE','synthetic')
    monkeypatch.setattr(transport,'validate_driver',lambda:None)
    engine=create_engine('postgresql+psycopg://synthetic:synthetic@127.0.0.1:9/isolated')
    try:
        with pytest.raises(ValueError,match='service-file profile'):
            validate_audit_transport_profile(SimpleNamespace(audit_stdout_mode='disabled'),engine=engine)
    finally:
        engine.dispose()
