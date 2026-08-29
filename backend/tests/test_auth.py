"""Тесты авторизации: режимы disabled / simulation, вычисление ролей, SSO-поток.

Покрывают:
  - GroupRoleAuthorizer: маппинг 4 ролей, приоритет security>admin>editor>viewer,
    fallback и fail-closed.
  - /api/auth/me в disabled и simulation (роль в поле user.roles).
  - /api/auth/simulate: 400 неизвестный юзер, 403 fail-closed.
  - защищённый эндпоинт: 401 без сессии, доступ после simulate.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.authorizer import GroupRoleAuthorizer
from app.auth.identity import AuthenticatedIdentity
from app.config import Settings
from app.main import create_app

ROLE_GROUPS = {
    "KB_Viewer": "viewer",
    "KB_Editor": "editor",
    "KB_Admin": "admin",
    "KB_Security": "security",
}


def make_client(tmp_path: Path, monkeypatch, **overrides) -> TestClient:
    defaults: dict = {
        "_env_file": None,
        "data_dir": tmp_path,
        "auth_provider": "simulation",
        "auth_role_groups": ROLE_GROUPS,
        "auth_default_role": "viewer",
        "auth_sim_users": [
            {"user_id": "sim-user", "username": "demo.user", "email": "u@d.local", "groups": ["KB_Viewer"]},
            {"user_id": "sim-editor", "username": "demo.editor", "email": "e@d.local", "groups": ["KB_Editor"]},
            {"user_id": "sim-admin", "username": "demo.admin", "email": "a@d.local", "groups": ["KB_Admin"]},
            {"user_id": "sim-security", "username": "demo.security", "email": "s@d.local", "groups": ["KB_Security"]},
            {"user_id": "sim-guest", "username": "demo.guest", "email": "g@d.local", "groups": []},
        ],
    }
    defaults.update(overrides)
    settings = Settings(**defaults)
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    return TestClient(create_app())


@pytest.fixture
def client(tmp_path, monkeypatch):
    return make_client(tmp_path, monkeypatch)


# ---------- GroupRoleAuthorizer ----------

class TestAuthorizer:
    def test_mapping_single(self):
        a = GroupRoleAuthorizer(ROLE_GROUPS, "viewer")
        assert a.resolve_role(["KB_Viewer"]) == "viewer"
        assert a.resolve_role(["KB_Editor"]) == "editor"
        assert a.resolve_role(["KB_Admin"]) == "admin"
        assert a.resolve_role(["KB_Security"]) == "security"

    def test_priority_security_wins(self):
        a = GroupRoleAuthorizer(ROLE_GROUPS, "viewer")
        assert a.resolve_role(["KB_Viewer", "KB_Admin", "KB_Security"]) == "security"

    def test_priority_admin_over_editor(self):
        a = GroupRoleAuthorizer(ROLE_GROUPS, "viewer")
        assert a.resolve_role(["KB_Viewer", "KB_Editor", "KB_Admin"]) == "admin"

    def test_priority_editor_over_viewer(self):
        a = GroupRoleAuthorizer(ROLE_GROUPS, "viewer")
        assert a.resolve_role(["KB_Viewer", "KB_Editor"]) == "editor"

    def test_no_match_uses_default(self):
        a = GroupRoleAuthorizer(ROLE_GROUPS, "viewer")
        assert a.resolve_role(["UNMAPPED"]) == "viewer"

    def test_no_match_fail_closed(self):
        a = GroupRoleAuthorizer(ROLE_GROUPS, None)
        assert a.resolve_role(["UNMAPPED"]) is None

    def test_empty_groups_uses_default(self):
        assert GroupRoleAuthorizer(ROLE_GROUPS, "viewer").resolve_role([]) == "viewer"
        assert GroupRoleAuthorizer(ROLE_GROUPS, None).resolve_role([]) is None


# ---------- AuthenticatedIdentity ----------

class TestIdentity:
    def test_from_mapping_oidc_default(self):
        raw = {"sub": "s1", "preferred_username": "idb.user", "email": "e@x", "group": ["KB_Viewer"]}
        i = AuthenticatedIdentity.from_mapping(raw, provider="keycloak_oidc")
        assert i.external_id == "s1"
        assert i.username == "idb.user"
        assert i.groups == ["KB_Viewer"]

    def test_from_mapping_custom(self):
        raw = {"uid": "42", "name": "u", "role_groups": "KB_Admin,KB_Viewer"}
        i = AuthenticatedIdentity.from_mapping(
            raw,
            field_mapping={"external_id": "uid", "username": "name", "groups": "role_groups"},
            provider="custom_client",
        )
        assert i.external_id == "42"
        assert i.groups == ["KB_Admin", "KB_Viewer"]

    def test_to_user_assigns_role(self):
        i = AuthenticatedIdentity(external_id="s1", username="u", groups=["KB_Admin"])
        user = i.to_user(GroupRoleAuthorizer(ROLE_GROUPS, "viewer"))
        assert user.roles == ["admin"]


# ---------- disabled ----------

def test_me_disabled(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch, auth_provider="disabled")
    body = c.get("/api/auth/me").json()
    assert body["mode"] == "disabled"
    assert body["user"]["username"] == "anonymous"
    assert body["sim_users"] is None


# ---------- simulation: gates ----------

def test_protected_requires_session_in_simulation(client):
    resp = client.get("/api/documents")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Требуется авторизация"


def test_simulate_unknown_user_400(client):
    resp = client.post("/api/auth/simulate", json={"username": "nobody"})
    assert resp.status_code == 400


# ---------- simulation: login + roles ----------

@pytest.mark.parametrize("username,expected_role", [
    ("demo.user", "viewer"),
    ("demo.editor", "editor"),
    ("demo.admin", "admin"),
    ("demo.security", "security"),
])
def test_simulate_roles(client, username, expected_role):
    resp = client.post("/api/auth/simulate", json={"username": username})
    assert resp.status_code == 200
    assert resp.json()["user"]["roles"] == [expected_role]


def test_simulate_then_me(client):
    client.post("/api/auth/simulate", json={"username": "demo.admin"})
    me = client.get("/api/auth/me").json()
    assert me["user"]["username"] == "demo.admin"
    assert me["user"]["roles"] == ["admin"]
    assert me["mode"] == "simulation"
    assert [u["username"] for u in me["sim_users"]] == [
        "demo.user", "demo.editor", "demo.admin", "demo.security", "demo.guest",
    ]


def test_fail_closed_403(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch, auth_default_role=None)
    resp = c.post("/api/auth/simulate", json={"username": "demo.guest"})
    assert resp.status_code == 403


def test_simulate_allows_protected(client):
    client.post("/api/auth/simulate", json={"username": "demo.admin"})
    assert client.get("/api/documents").status_code == 200


def test_logout_clears_session(client):
    client.post("/api/auth/simulate", json={"username": "demo.user"})
    assert client.get("/api/auth/me").json()["user"]["username"] == "demo.user"
    resp = client.get("/api/auth/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert client.get("/api/documents").status_code == 401


def test_health_still_works(client):
    assert client.get("/health").status_code == 200
