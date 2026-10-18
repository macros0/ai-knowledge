import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.tag_registry import TagRegistry
from app.services.audit import AuditService


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="simulation", reference_edit_locks_enabled=True,
        auth_role_groups={"editors": "editor", "admins": "admin", "viewers": "viewer"},
        auth_sim_users=[{"user_id": name, "username": name, "groups": [group]}
                        for name, group in [("alice", "editors"), ("bob", "editors"), ("admin", "admins"), ("viewer", "viewers")]])
    for module in ("app.config", "app.main", "app.auth.api", "app.api.reference_editing", "app.services.reference_mutation"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    TagRegistry().add(["api-lease"])
    result = TestClient(create_app())
    result.reference_settings = settings
    return result


def login(client, who):
    assert client.post("/api/auth/simulate", json={"username": who}).status_code == 200


def route():
    return f'/api/reference-edit-locks/tag/{TagRegistry().all()[0]["id"]}'


def test_status_has_no_token_and_competing_users_receive_conflict(client):
    login(client, "alice")
    acquired = client.post(route(), json={"edit_token": "a" * 64})
    assert acquired.status_code == 200, acquired.text
    epoch = acquired.json()["lease_epoch"]
    login(client, "bob")
    refused = client.post(route(), json={"edit_token": "b" * 64})
    assert refused.status_code == 409
    assert refused.json()["code"] == "reference_edit_locked"
    status = client.get(route())
    assert status.status_code == 200
    assert status.json()["owner_name"] == "alice"
    assert "a" * 64 not in acquired.text + refused.text + status.text
    login(client, "alice")
    assert client.put(route(), json={"edit_token": "a" * 64, "lease_epoch": epoch, "activity_sequence": 1}).status_code == 200
    assert client.request("DELETE", route(), json={"edit_token": "a" * 64, "lease_epoch": epoch}).status_code == 200
    assert "a" * 64 not in str(AuditService().query())


def test_viewer_cannot_read_owner_and_editor_cannot_revoke(client):
    login(client, "viewer")
    assert client.get(route()).status_code == 403
    login(client, "alice")
    first = client.post(route(), json={"edit_token": "a" * 64}).json()
    assert client.post(route() + "/revoke", json={"lease_epoch": first["lease_epoch"], "reason": "review"}).status_code == 403
    login(client, "admin")
    assert client.post(route() + "/revoke", json={"lease_epoch": first["lease_epoch"], "reason": "review"}).status_code == 200
    login(client, "alice")
    assert client.put(route(), json={"edit_token": "a" * 64, "lease_epoch": first["lease_epoch"], "activity_sequence": 1}).status_code == 409


def test_validation_does_not_echo_token_or_accept_invalid_identity(client):
    login(client, "alice")
    response = client.put(route(), json={"edit_token": "a" * 64, "lease_epoch": 1, "activity_sequence": -1})
    assert response.status_code == 422
    assert "a" * 64 not in response.text
    assert client.post("/api/reference-edit-locks/document/1", json={"edit_token": "a" * 64}).status_code == 422


def test_tag_translation_requires_original_revision_and_rejects_stale_save(client):
    client.reference_settings.reference_edit_locks_enabled = False
    login(client, "alice")
    tag = client.get("/api/tags").json()["tags"][0]
    path = f'/api/tags/{tag["id"]}/translations/en'
    assert client.patch(path, json={"locale": "en", "text": "Old client"}).status_code == 428
    body = {"locale": "en", "text": "Alice", "expected_reference_revision": tag["reference_revision"]}
    saved = client.patch(path, json=body)
    assert saved.status_code == 200, saved.text
    login(client, "bob")
    refused = client.patch(path, json={**body, "text": "Stale Bob"})
    assert refused.status_code == 409
    assert refused.json()["code"] == "version_conflict"
    assert client.get("/api/tags").json()["tags"][0]["translations"][0]["text"] == "Alice"
    assert client.patch(path, json={**body, "locale": "de"}).status_code == 422
