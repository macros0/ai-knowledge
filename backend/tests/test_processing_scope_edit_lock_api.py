from tests.test_processing_scope_api import base_upload_client, upload_mail


def test_api_requires_tab_lease_and_does_not_let_second_tab_save(base_upload_client):
    client, settings, _ = base_upload_client
    settings.attachment_review_enabled = True
    settings.dedup_enabled = False
    doc = upload_mail(client, 600).json()
    route = f"/api/documents/{doc['id']}/processing-scope"
    review = client.get(route).json()["pending"]
    body = {"review_id": review["id"], "expected_revision": review["revision"], "selected_source_keys": []}
    assert client.put(route + "/review", json=body).status_code == 409
    first = client.post(route + "/edit-lock", json={"edit_token": "1" * 64})
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "editing"
    assert client.post(route + "/edit-lock", json={"edit_token": "2" * 64}).status_code == 409
    assert client.put(route + "/review", json={**body, "edit_token": "2" * 64}).status_code == 409
    saved = client.put(route + "/review", json={**body, "edit_token": "1" * 64})
    assert saved.status_code == 200, saved.text
    assert client.put(route + "/edit-lock", json={"edit_token": "1" * 64}).status_code == 200
    assert client.request("DELETE", route + "/edit-lock", json={"edit_token": "2" * 64}).status_code == 200
    assert client.post(route + "/edit-lock", json={"edit_token": "2" * 64}).status_code == 409
    assert client.request("DELETE", route + "/edit-lock", json={"edit_token": "1" * 64}).status_code == 200
    assert client.post(route + "/edit-lock", json={"edit_token": "2" * 64}).status_code == 200
    assert "token" not in repr(client.get(route).json()["edit_lock"])


def test_idle_expiration_is_enforced_by_api_even_with_regular_heartbeats(base_upload_client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import app.services.processing_scope_edit_lock as locks
    client, settings, _ = base_upload_client
    settings.attachment_review_enabled = True
    settings.dedup_enabled = False
    doc = upload_mail(client, 600).json()
    route = f"/api/documents/{doc['id']}/processing-scope/edit-lock"
    start = datetime.now(timezone.utc)
    monkeypatch.setattr(locks, "_now", lambda:start)
    assert client.post(route,json={"edit_token":"1"*64}).status_code == 200
    for minute in range(1,10):
        monkeypatch.setattr(locks,"_now",lambda minute=minute:start+timedelta(minutes=minute))
        assert client.put(route,json={"edit_token":"1"*64,"activity_sequence":0}).status_code == 200
    # Retrying acquire of the same active opening is not human activity either.
    assert client.post(route,json={"edit_token":"1"*64}).json()["idle_remaining_seconds"] == 60
    monkeypatch.setattr(locks,"_now",lambda:start+timedelta(minutes=10))
    expired=client.put(route,json={"edit_token":"1"*64,"activity_sequence":1})
    assert expired.status_code == 409 and expired.json()["code"] == "scope_edit_lock_lost"
    assert client.post(route,json={"edit_token":"2"*64}).status_code == 200
    assert client.put(route,json={"edit_token":"2"*64,"activity_sequence":-1}).status_code == 422
