from tests.test_reference_edit_lock_api import client as reference_client, login

client = reference_client


def test_development_save_checks_both_revision_and_own_lease(client):
    login(client, "alice")
    created = client.post("/api/developments", json={"number": "REF-1", "name": "Original"})
    assert created.status_code == 200, created.text
    dev = created.json()
    assert dev["reference_revision"] == 1
    path = f'/api/developments/{dev["id"]}'
    assert client.patch(path, json={"name": "Old client", "version": 1}).status_code == 428
    status = client.post(f'/api/reference-edit-locks/development/{dev["id"]}', json={"edit_token": "a" * 64}).json()
    headers = {"X-Reference-Edit-Token": "a" * 64, "X-Reference-Edit-Epoch": str(status["lease_epoch"])}
    body = {"name": "Alice", "version": 1, "expected_reference_revision": 1}
    assert client.patch(path, json=body).status_code == 428
    saved = client.patch(path, json=body, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["reference_revision"] == 2
    login(client, "bob")
    refused = client.patch(path, json={**body, "name": "Stale Bob"})
    assert refused.status_code == 409
    assert refused.json()["current"]["name"] == "Alice"
    assert client.get(path).json()["name"] == "Alice"


def test_noop_keeps_revision_and_delete_requires_current_snapshot(client):
    client.reference_settings.reference_edit_locks_enabled = False
    login(client, "alice")
    dev = client.post("/api/developments", json={"number": "REF-2", "name": "Original"}).json()
    path = f'/api/developments/{dev["id"]}'
    saved = client.patch(path, json={"name": "Original", "version": 1, "expected_reference_revision": 1})
    assert saved.status_code == 200, saved.text
    assert saved.json()["reference_revision"] == saved.json()["version"] == 1
    assert client.delete(path + "?version=1").status_code == 428
    assert client.delete(path + "?version=1&expected_reference_revision=1").status_code == 200
