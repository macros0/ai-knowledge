"""Replica session/block expiry must use authoritative database time."""
from datetime import datetime, timedelta
import hashlib

import pytest
from starlette.requests import Request

from app.config import Settings
from app.auth.identity import AuthenticatedIdentity
from app.db.models import AuthSession
from app.db.session import session_scope
from app.services.work.store import _now, _utc


def request(session=None):
    return Request({"type": "http", "method": "GET", "path": "/", "scheme": "http",
        "headers": [], "query_string": b"", "server": ("test", 80), "client": ("127.0.0.1", 1),
        "state": {}, "session": session or {}})


@pytest.fixture
def settings(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="simulation")
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    return settings


def test_clock_behind_api_cannot_accept_an_expired_database_session(settings, monkeypatch):
    from app.auth import service
    class Past(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2000, 1, 1, tzinfo=tz)
    monkeypatch.setattr(service, "datetime", Past)
    token = "test-opaque-session"
    identity = AuthenticatedIdentity(external_id="actor", username="actor", groups=["KB_Viewer"], provider="simulation")
    with session_scope() as session:
        session.add(AuthSession(id=hashlib.sha256(token.encode()).hexdigest(), external_id="actor",
            identity=identity.model_dump(mode="json"), expires_at=_now(session) - timedelta(seconds=1)))
    assert service.identity_from_session(request({"identity": {"session_id": token}})) is None


def test_session_creation_ttl_uses_database_time_despite_ahead_api_clock(settings, monkeypatch):
    from app.auth import service
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2100, 1, 1, tzinfo=tz)
    monkeypatch.setattr(service, "datetime", Future)
    service.store_identity(request(), AuthenticatedIdentity(external_id="actor", username="actor",
        groups=["KB_Viewer"], provider="simulation"))
    with session_scope() as session:
        row = session.query(AuthSession).one()
        assert abs((_utc(row.expires_at) - _now(session)).total_seconds() - settings.auth_session_ttl_seconds) < 2


def test_active_block_uses_database_time_on_an_ahead_clock_api(settings, monkeypatch):
    from app.services import blocklist
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2100, 1, 1, tzinfo=tz)
    monkeypatch.setattr(blocklist, "datetime", Future)
    with session_scope() as session:
        expires = _now(session) + timedelta(hours=1)
    store = blocklist.Blocklist()
    store.block("actor", blocked_by="security", expires_at=expires)
    assert store.is_blocked("actor")
    assert store.list_active()[0]["active"]
