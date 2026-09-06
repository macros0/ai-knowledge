"""Mock-тесты SSO-потока (keycloak_oidc): redirect_uri через Next.js-прокси.

Ключевой сценарий: фронтенд ходит на бэкенд через прокси (:16300 vs :18000),
поэтому Keycloak должен получить внешний URI (SSO_REDIRECT_URI), и тот же URI
должен уйти в authorize_access_token для валидации state.
"""
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest
from authlib.integrations.base_client.errors import OAuthError
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

SSO_SETTINGS = {
    "_env_file": None,
    "auth_provider": "keycloak_oidc",
    "auth_role_groups": {"KB_Viewer": "viewer"},
    "auth_default_role": "viewer",
    "keycloak_url": "http://kc.example",
    "keycloak_realm": "myrealm",
    "keycloak_client_id": "my-app",
    "keycloak_client_secret": "dev-secret",
    "sso_redirect_uri": "http://localhost:16300/api/auth/callback",
}


class FakeClient:
    """Имитирует keycloak-клиент authlib: фиксирует redirect_uri."""

    last = None

    def __init__(self):
        self.auth_redirect_uri = None
        self.token_redirect_uri = None
        self.userinfo_calls = 0
        type(self).last = self

    async def authorize_redirect(self, request, redirect_uri):
        self.auth_redirect_uri = redirect_uri
        return {"fake": "redirect"}

    async def authorize_access_token(self, request, **kwargs):
        self.token_redirect_uri = kwargs.get("redirect_uri")
        return {
            "id_token": "fake-id-token",
            "userinfo": {
                "sub": "kc-sub-123",
                "preferred_username": "idb.user",
                "email": "idb@company.local",
                "group": ["KB_Viewer"],
            },
        }

    async def userinfo(self, token=None):
        self.userinfo_calls += 1
        return {}


class NoIdTokenClient(FakeClient):
    """Callback без id_token (например, старые сессии) — logout без id_token_hint."""

    async def authorize_access_token(self, request, **kwargs):
        self.token_redirect_uri = kwargs.get("redirect_uri")
        return {"userinfo": {
            "sub": "kc-sub-123",
            "preferred_username": "idb.user",
            "email": "idb@company.local",
            "group": ["KB_Viewer"],
        }}


class NoRoleClient(FakeClient):
    """Юзер без групп → fail-closed (нет роли)."""

    async def authorize_access_token(self, request, **kwargs):
        self.token_redirect_uri = kwargs.get("redirect_uri")
        return {"userinfo": {
            "sub": "x",
            "preferred_username": "no-group",
            "email": "x@company.local",
            "group": [],
        }}


class OAuthErrorClient(FakeClient):
    """Keycloak вернул error/error_description (например authentication_expired)."""

    async def authorize_access_token(self, request, **kwargs):
        raise OAuthError(error="temporarily_unavailable", description="authentication_expired")


class KeycloakDownClient(FakeClient):
    """Keycloak недоступен при старте входа (обрыв соединения)."""

    async def authorize_redirect(self, request, redirect_uri):
        raise httpx.ConnectError("connection refused")


class FakeOAuth:
    """Имитирует OAuth-клиент authlib с атрибутом `.keycloak`."""

    def __init__(self, client):
        self.keycloak = client


def build_sso_client(tmp_path: Path, monkeypatch, client_cls=FakeClient, **overrides):
    kwargs = {**SSO_SETTINGS, "data_dir": tmp_path, **overrides}
    settings = Settings(**kwargs)

    # Подменяем _oauth провайдера на мок-клиент.
    monkeypatch.setattr(
        "app.auth.providers.keycloak_oidc.KeycloakOidcProvider._oauth",
        lambda self: FakeOAuth(client_cls()),
    )
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    # follow_redirects=False: callback делает 303 на "/", которого в бэкенде нет
    # (это фронтенд) — иначе TestClient пойдёт на "/" и получит 404.
    return TestClient(create_app(), follow_redirects=False)


def test_sso_login_uses_configured_redirect_uri(tmp_path, monkeypatch):
    c = build_sso_client(tmp_path, monkeypatch)
    resp = c.get("/api/auth/login")
    assert resp.status_code == 200
    assert FakeClient.last.auth_redirect_uri == "http://localhost:16300/api/auth/callback"


def test_sso_callback_passes_redirect_uri_and_sets_session(tmp_path, monkeypatch):
    c = build_sso_client(tmp_path, monkeypatch)
    resp = c.get("/api/auth/callback")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"

    me = c.get("/api/auth/me").json()
    assert me["mode"] == "sso"
    assert me["user"]["user_id"] == "kc-sub-123"
    assert me["user"]["roles"] == ["viewer"]


def test_sso_fail_closed_403(tmp_path, monkeypatch):
    c = build_sso_client(tmp_path, monkeypatch, client_cls=NoRoleClient, auth_default_role=None)
    resp = c.get("/api/auth/callback")
    assert resp.status_code == 403


def test_sso_callback_oauth_error_redirects(tmp_path, monkeypatch):
    """Keycloak вернул error (authentication_expired) → редирект с сообщением, не 500."""
    c = build_sso_client(tmp_path, monkeypatch, client_cls=OAuthErrorClient)
    resp = c.get("/api/auth/callback")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/?auth_error=session_expired"


def test_sso_login_keycloak_unavailable_redirects(tmp_path, monkeypatch):
    """Keycloak недоступен при старте входа → редирект с сообщением, не 500."""
    c = build_sso_client(tmp_path, monkeypatch, client_cls=KeycloakDownClient)
    resp = c.get("/api/auth/login")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/?auth_error=unavailable"


def _logout_after_login(tmp_path, monkeypatch, client_cls=FakeClient):
    c = build_sso_client(tmp_path, monkeypatch, client_cls=client_cls)
    assert c.get("/api/auth/callback").status_code == 303  # вход
    resp = c.get("/api/auth/logout", follow_redirects=False)
    return resp


def test_sso_logout_redirects_to_keycloak_with_id_token(tmp_path, monkeypatch):
    """RP-Initiated Logout: редирект на end_session_endpoint с id_token_hint."""
    resp = _logout_after_login(tmp_path, monkeypatch, client_cls=FakeClient)
    assert resp.status_code == 303
    parsed = urlparse(resp.headers["location"])
    assert parsed.netloc == "kc.example"
    assert parsed.path == "/realms/myrealm/protocol/openid-connect/logout"
    q = parse_qs(parsed.query)
    assert unquote(q["post_logout_redirect_uri"][0]) == "http://localhost:16300/"
    assert q["id_token_hint"][0] == "fake-id-token"


def test_sso_logout_without_id_token_still_redirects(tmp_path, monkeypatch):
    """Сессия без id_token не должна ломать logout — идёт без id_token_hint."""
    resp = _logout_after_login(tmp_path, monkeypatch, client_cls=NoIdTokenClient)
    assert resp.status_code == 303
    parsed = urlparse(resp.headers["location"])
    assert parsed.path == "/realms/myrealm/protocol/openid-connect/logout"
    q = parse_qs(parsed.query)
    assert unquote(q["post_logout_redirect_uri"][0]) == "http://localhost:16300/"
    assert "id_token_hint" not in q
