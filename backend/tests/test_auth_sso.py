"""Mock-тесты SSO-потока (keycloak_oidc): redirect_uri через Next.js-прокси.

Ключевой сценарий: фронтенд ходит на бэкенд через прокси (:3000 vs :8000),
поэтому Keycloak должен получить внешний URI (SSO_REDIRECT_URI), и тот же URI
должен уйти в authorize_access_token для валидации state.
"""
from pathlib import Path

import pytest
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
    "sso_redirect_uri": "http://localhost:3000/api/auth/callback",
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
        return {"userinfo": {
            "sub": "kc-sub-123",
            "preferred_username": "idb.user",
            "email": "idb@company.local",
            "group": ["KB_Viewer"],
        }}

    async def userinfo(self, token=None):
        self.userinfo_calls += 1
        return {}


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
    assert FakeClient.last.auth_redirect_uri == "http://localhost:3000/api/auth/callback"


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
