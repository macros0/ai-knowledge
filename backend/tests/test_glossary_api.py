from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.settings import get_settings as settings_dependency
from app.config import Settings
from app.main import create_app
from app.services import audit
from app.services.audit import AuditService


ROLE_GROUPS = {
    "KB_Viewer": "viewer",
    "KB_Editor": "editor",
    "KB_Admin": "admin",
    "KB_Security": "security",
}


def make_client(tmp_path: Path, monkeypatch) -> TestClient:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        auth_provider="simulation",
        glossary_query_expansion_enabled=False,
        auth_role_groups=ROLE_GROUPS,
        auth_default_role="viewer",
        auth_sim_users=[
            {"user_id": "viewer", "username": "viewer", "groups": ["KB_Viewer"]},
            {"user_id": "editor", "username": "editor", "groups": ["KB_Editor"]},
            {"user_id": "admin", "username": "admin", "groups": ["KB_Admin"]},
            {"user_id": "security", "username": "security", "groups": ["KB_Security"]},
        ],
    )
    for module in ("app.config", "app.main", "app.auth.api", "app.api.settings"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[settings_dependency] = lambda: settings
    return TestClient(app)


@pytest.fixture
def client(tmp_path, monkeypatch):
    return make_client(tmp_path, monkeypatch)


def login(client, username):
    response = client.post("/api/auth/simulate", json={"username": username})
    assert response.status_code == 200, response.text


def test_chat_settings_exposes_glossary_expansion_state(client):
    login(client, "viewer")
    response = client.get("/api/settings")

    assert response.status_code == 200, response.text
    assert response.json()["glossary_query_expansion_enabled"] is False
    assert response.json()["knowledge_profile"] == "Основной контур"


def create_term(client, **overrides):
    payload = {
        "canonical": "IT0003",
        "kind": "sap_infotype",
        "original_name": "Infotype three",
        "original_description": "Description",
        "canonical_locale": "en",
    }
    payload.update(overrides)
    return client.post("/api/admin/glossary", json=payload)


def test_glossary_crud_is_role_gated_and_audited(client):
    login(client, "editor")
    assert client.get("/api/admin/glossary").status_code == 200
    assert create_term(client).status_code == 403

    login(client, "admin")
    response = create_term(
        client,
        aliases=[
            {
                "alias": "ИТ 0003",
                "locale": "ru",
                "auto_expand": True,
                "search_enabled": True,
            }
        ],
    )
    assert response.status_code == 201, response.text
    term = response.json()
    assert len(term["aliases"]) == 2
    assert AuditService().query(action_type=audit.GLOSSARY_TERM_CREATE)

    listed = client.get("/api/admin/glossary?limit=1&offset=0&q=Infotype").json()
    assert listed["total"] == 1
    assert listed["terms"][0]["id"] == term["id"]

    updated = client.patch(
        f"/api/admin/glossary/{term['id']}",
        json={"version": term["version"], "enabled": False},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["enabled"] is False


def test_only_admin_can_change_source_and_source_revision_is_audited(client):
    login(client, "admin")
    term = create_term(client).json()
    response = client.patch(
        f"/api/admin/glossary/{term['id']}/source",
        json={
            "version": term["version"],
            "original_name": "Updated name",
            "canonical_locale": "de",
            "enabled": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["source_revision"] == term["source_revision"] + 1
    assert response.json()["enabled"] is False
    assert AuditService().query(action_type=audit.GLOSSARY_SOURCE_UPDATE)

    login(client, "editor")
    forbidden = client.patch(
        f"/api/admin/glossary/{term['id']}/source",
        json={"version": response.json()["version"], "original_name": "Nope"},
    )
    assert forbidden.status_code == 403


def test_glossary_alias_validation_conflict_and_version_errors(client):
    login(client, "admin")
    created = create_term(client).json()

    unsafe = client.post(
        f"/api/admin/glossary/{created['id']}/aliases",
        json={
            "version": created["version"],
            "alias": "PA",
            "auto_expand": True,
            "search_enabled": True,
        },
    )
    assert unsafe.status_code == 422
    assert unsafe.json()["code"] == "glossary_unsafe_auto_expand"

    stale = client.patch(
        f"/api/admin/glossary/{created['id']}",
        json={"version": created["version"] + 1, "enabled": False},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "version_conflict"

    foreign_alias = client.delete(
        f"/api/admin/glossary/{created['id']}/aliases/999999?version={created['version']}"
    )
    assert foreign_alias.status_code == 404
    assert foreign_alias.json()["code"] == "glossary_alias_not_found"


def test_glossary_alias_lifecycle_uses_term_version(client):
    login(client, "admin")
    term = create_term(client).json()
    added = client.post(
        f"/api/admin/glossary/{term['id']}/aliases",
        json={"version": term["version"], "alias": "infotype 3", "auto_expand": True},
    )
    assert added.status_code == 200, added.text
    current = added.json()
    alias_id = next(item["id"] for item in current["aliases"] if item["alias"] == "infotype 3")

    changed = client.patch(
        f"/api/admin/glossary/{term['id']}/aliases/{alias_id}",
        json={"version": current["version"], "alias": "infotype three", "auto_expand": True},
    )
    assert changed.status_code == 200, changed.text
    latest = changed.json()
    deleted = client.delete(
        f"/api/admin/glossary/{term['id']}/aliases/{alias_id}?version={latest['version']}"
    )
    assert deleted.status_code == 200, deleted.text


def test_glossary_preview_is_available_to_editor_but_not_user(client):
    login(client, "editor")
    response = client.post("/api/admin/glossary/preview", json={"query": "ИТ 0003"})
    assert response.status_code == 200, response.text
    assert response.json()["original_query"] == "ИТ 0003"
    assert "dense_query" in response.json()

    login(client, "viewer")
    assert client.post("/api/admin/glossary/preview", json={"query": "ИТ 0003"}).status_code == 403


def test_glossary_preview_validates_locale_and_exposes_skipped_reasons(client, monkeypatch):
    login(client, "admin")
    first = create_term(client, canonical="PA01", kind="sap_transaction").json()
    second = create_term(client, canonical="PA02", kind="sap_transaction").json()
    assert first["id"] != second["id"]

    class PreviewSettings:
        glossary_max_terms_per_query = 1
        glossary_max_added_aliases_per_term = 4
        glossary_max_added_tokens = 32
        glossary_max_added_chars = 768
        glossary_query_text_max_chars = 8192

    monkeypatch.setattr("app.api.glossary.get_settings", lambda: PreviewSettings())
    response = client.post(
        "/api/admin/glossary/preview",
        json={"query": "PA01 PA02", "locale": "en"},
    )
    assert response.status_code == 200, response.text
    assert any(
        item["canonical"] == "PA02" and item["reason"] == "max_terms_per_query"
        for item in response.json()["skipped_reasons"]
    )

    invalid = client.post(
        "/api/admin/glossary/preview",
        json={"query": "PA01", "locale": "not-a-locale"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "glossary_invalid_locale"


def test_glossary_static_preview_route_is_not_parsed_as_term_id(client):
    login(client, "editor")
    response = client.get("/api/admin/glossary/translations/pending")
    assert response.status_code == 422


def test_glossary_pending_route_is_static_and_role_gated(client):
    login(client, "editor")
    response = client.get("/api/admin/glossary/translations/pending?locale=en")
    assert response.status_code == 200, response.text
    assert response.json()["pending"]["missing"] == 0

    login(client, "security")
    assert client.get("/api/admin/glossary?limit=1").status_code == 403


def test_glossary_schema_validation_returns_stable_codes(client):
    login(client, "admin")
    too_long = create_term(client, aliases=[{"alias": "x" * 257}])
    assert too_long.status_code == 422
    assert too_long.json()["code"] == "glossary_invalid_alias"

    invalid_locale = create_term(client, canonical_locale="not-a-locale")
    assert invalid_locale.status_code == 422
    assert invalid_locale.json()["code"] == "glossary_invalid_locale"


def test_cookie_authenticated_glossary_mutation_requires_csrf(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        auth_provider="keycloak_oidc",
        auth_role_groups=ROLE_GROUPS,
        auth_default_role=None,
        keycloak_url="https://sso.test",
        keycloak_realm="test",
        keycloak_client_id="client",
        keycloak_client_secret="secret",
    )
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    client = TestClient(create_app())
    response = client.post(
        "/api/admin/glossary",
        cookies={"session": "opaque", "csrf_token": "token"},
        json={"canonical": "IT0003", "kind": "sap_infotype", "original_name": "Name"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


def test_glossary_translation_patch_is_editor_allowed_and_uses_independent_versions(client):
    login(client, "admin")
    term = create_term(client, canonical="PA20", kind="sap_transaction").json()

    login(client, "editor")
    response = client.patch(
        f"/api/admin/glossary/{term['id']}/translations/ru",
        json={
            "translation_version": 0,
            "source_revision": term["source_revision"],
            "display_name": "Русское имя",
            "description": "Русское описание",
        },
    )
    assert response.status_code == 200, response.text
    translation = response.json()["translations"][0]
    assert translation["reviewed_by"] == "editor"

    stale = client.patch(
        f"/api/admin/glossary/{term['id']}/translations/ru",
        json={
            "translation_version": 0,
            "source_revision": term["source_revision"],
            "display_name": "Конфликт",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "version_conflict"


def test_glossary_translation_backfill_is_admin_only_and_limited_to_ten(client, monkeypatch):
    login(client, "editor")
    forbidden = client.post(
        "/api/admin/glossary/translations/backfill",
        json={"locale": "ru", "term_ids": [1]},
    )
    assert forbidden.status_code == 403

    login(client, "admin")
    monkeypatch.setattr(
        "app.api.glossary.backfill_glossary_translations",
        lambda *args, **kwargs: {"status": "completed", "requested": len(args[1])},
    )
    response = client.post(
        "/api/admin/glossary/translations/backfill",
        json={"locale": "ru", "term_ids": [1], "expected_translation_versions": {1: 0}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["requested"] == 1

    too_many = client.post(
        "/api/admin/glossary/translations/backfill",
        json={"locale": "ru", "term_ids": list(range(1, 12))},
    )
    assert too_many.status_code == 422
