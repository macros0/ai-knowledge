"""Нормализация group-claim и гибкий field_mapping (сценарий IDP + IDB).

Покрывают:
  - normalize_groups: все формы claim (list / строка / JSON-объект / вложенный
    dict / None / скаляр) + dedup с сохранением порядка.
  - from_mapping: partial override field_mapping (незаданные поля — стандарт OIDC).
  - KeycloakOidcProvider._normalize_paths: leaf vs full_path.
  - Валидатор keycloak_group_path_mode.
  - end-to-end: full_path-группы + auth_role_groups с full_path-ключами → роль.
"""

import pytest
from fastapi.testclient import TestClient

from app.auth.authorizer import GroupRoleAuthorizer
from app.auth.identity import AuthenticatedIdentity, normalize_groups
from app.auth.providers.keycloak_oidc import KeycloakOidcProvider
from app.config import Settings
from app.main import create_app


# ---------- normalize_groups ----------

class TestNormalizeGroups:
    def test_none(self):
        assert normalize_groups(None) == []

    def test_string_comma(self):
        assert normalize_groups("KB_Admin,KB_Viewer") == ["KB_Admin", "KB_Viewer"]

    def test_string_strips_whitespace_and_empties(self):
        assert normalize_groups(" KB_Admin , ,KB_Viewer ") == ["KB_Admin", "KB_Viewer"]

    def test_custom_separator(self):
        assert normalize_groups("KB_Admin;KB_Viewer", separator=";") == ["KB_Admin", "KB_Viewer"]

    def test_list(self):
        assert normalize_groups(["KB_Admin", "KB_Viewer"]) == ["KB_Admin", "KB_Viewer"]

    def test_nested_list_flatten(self):
        assert normalize_groups(["KB_Admin", ["KB_Viewer", ["KB_Editor"]]]) == [
            "KB_Admin", "KB_Viewer", "KB_Editor",
        ]

    def test_json_object_with_known_key(self):
        assert normalize_groups({"group": ["KB_Admin", "KB_Viewer"]}) == ["KB_Admin", "KB_Viewer"]
        assert normalize_groups({"groups": ["KB_Admin"]}) == ["KB_Admin"]

    def test_known_key_wins_over_other_values(self):
        # Сначала ищем известные ключи (group/groups), иначе рискуем "расплющить"
        # не те данные глубокой структуры.
        raw = {"group": ["KB_Admin"], "unrelated": ["SHOULD_NOT_APPEAR"]}
        assert normalize_groups(raw) == ["KB_Admin"]

    def test_nested_dict_fallback_flattens_values(self):
        raw = {"parent": {"child": ["KB_Viewer"]}}
        assert normalize_groups(raw) == ["KB_Viewer"]

    def test_scalar(self):
        assert normalize_groups("KB_Admin") == ["KB_Admin"]
        assert normalize_groups(42) == ["42"]

    def test_dedup_preserves_order(self):
        assert normalize_groups(["A", "B", "A"]) == ["A", "B"]
        assert normalize_groups(["A", "B", "B", "A"]) == ["A", "B"]


# ---------- from_mapping: partial override ----------

class TestFieldMappingOverride:
    def test_partial_override_groups_only(self):
        raw = {
            "sub": "s1",
            "preferred_username": "idb.user",
            "email": "e@x",
            "roles": ["KB_Admin"],
        }
        i = AuthenticatedIdentity.from_mapping(
            raw, field_mapping={"groups": "roles"}, provider="keycloak_oidc"
        )
        assert i.external_id == "s1"
        assert i.username == "idb.user"
        assert i.email == "e@x"
        assert i.groups == ["KB_Admin"]

    def test_partial_override_username_only(self):
        raw = {"sub": "s1", "name": "u", "group": ["KB_Viewer"]}
        i = AuthenticatedIdentity.from_mapping(
            raw, field_mapping={"username": "name"}, provider="keycloak_oidc"
        )
        assert i.username == "u"
        assert i.external_id == "s1"
        assert i.groups == ["KB_Viewer"]

    def test_empty_mapping_equals_default(self):
        raw = {"sub": "s1", "preferred_username": "u", "email": "e@x", "group": ["KB_Viewer"]}
        i = AuthenticatedIdentity.from_mapping(raw, field_mapping={}, provider="x")
        assert i.external_id == "s1"
        assert i.username == "u"
        assert i.groups == ["KB_Viewer"]


# ---------- _normalize_paths ----------

class TestNormalizePaths:
    def _provider(self, path_mode):
        settings = Settings(
            _env_file=None,
            keycloak_group_path_mode=path_mode,
        )
        return KeycloakOidcProvider(settings)

    def test_leaf_strips_prefix(self):
        p = self._provider("leaf")
        assert p._normalize_paths(["/IDB/KB_Viewer", "KB_Admin"]) == ["KB_Viewer", "KB_Admin"]

    def test_full_path_keeps(self):
        p = self._provider("full_path")
        assert p._normalize_paths(["/IDB/KB_Viewer"]) == ["/IDB/KB_Viewer"]


# ---------- config validator ----------

class TestConfigValidator:
    def test_valid_path_modes(self):
        assert Settings(_env_file=None, keycloak_group_path_mode="leaf").keycloak_group_path_mode == "leaf"
        assert Settings(_env_file=None, keycloak_group_path_mode="full_path").keycloak_group_path_mode == "full_path"

    def test_invalid_path_mode_raises(self):
        with pytest.raises(ValueError):
            Settings(_env_file=None, keycloak_group_path_mode="bogus")

    def test_field_mapping_parses_json_string(self):
        s = Settings(_env_file=None, keycloak_field_mapping='{"groups": "roles"}')
        assert s.keycloak_field_mapping == {"groups": "roles"}


# ---------- end-to-end: full_path ----------

def test_full_path_groups_resolve_role():
    role_groups = {"/IDB/KB_Viewer": "viewer", "/IDB/KB_Admin": "admin"}
    a = GroupRoleAuthorizer(role_groups, None)
    assert a.resolve_role(["/IDB/KB_Admin", "/IDB/KB_Viewer"]) == "admin"
    assert a.resolve_role(["KB_Viewer"]) is None  # leaf-имя не совпадёт


class FullPathClient:
    """Юзер с full-path группами → роль резолвится по full-path ключам."""

    async def authorize_redirect(self, request, redirect_uri):
        return {"fake": "redirect"}

    async def authorize_access_token(self, request, **kwargs):
        return {"userinfo": {
            "sub": "s1",
            "preferred_username": "idb.user",
            "email": "e@x",
            "group": ["/IDB/KB_Admin"],
        }}

    async def userinfo(self, token=None):
        return {}


class FakeOAuth:
    def __init__(self, client):
        self.keycloak = client


def test_sso_full_path_end_to_end(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        auth_provider="keycloak_oidc",
        auth_role_groups={"/IDB/KB_Admin": "admin"},
        auth_default_role=None,
        keycloak_url="http://kc.example",
        keycloak_realm="myrealm",
        keycloak_client_id="my-app",
        keycloak_client_secret="dev-secret",
        keycloak_group_path_mode="full_path",
        sso_redirect_uri="http://localhost:16300/api/auth/callback",
    )
    monkeypatch.setattr(
        "app.auth.providers.keycloak_oidc.KeycloakOidcProvider._oauth",
        lambda self: FakeOAuth(FullPathClient()),
    )
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)

    c = TestClient(create_app(), follow_redirects=False)
    resp = c.get("/api/auth/callback")
    assert resp.status_code == 303
    me = c.get("/api/auth/me").json()
    assert me["user"]["roles"] == ["admin"]
