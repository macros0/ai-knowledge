from tests.test_reference_edit_lock_api import client as reference_client, login

client = reference_client
from app.services.tag_registry import TagRegistry
from app.services.registry import get_registry
import pytest


def test_legacy_cleanup_cannot_bypass_an_occupied_tag(client):
    from app.services.reference_concurrency import ReferenceError
    login(client, "alice")
    tag = TagRegistry().all()[0]
    assert client.post(f'/api/reference-edit-locks/tag/{tag["id"]}', json={"edit_token": "a" * 64}).status_code == 200
    with pytest.raises(ReferenceError) as exc:
        TagRegistry().delete_unused()
    assert exc.value.code == "reference_edit_locked"
    assert TagRegistry().all()[0]["id"] == tag["id"]


def test_one_busy_review_transaction_does_not_discard_other_card_results(client, monkeypatch):
    from app.services import reference_tag_actions as actions
    from app.services.reference_concurrency import ReferenceError
    login(client, "alice")
    registry = TagRegistry()
    registry.add(["busy-card", "ready-card"])
    snapshot = tags_by_name(client)
    for name in ("busy-card", "ready-card"):
        registry.set_translation(snapshot[name]["id"], "en", name, is_machine=True)
    snapshot = tags_by_name(client)
    original = actions._card
    def card(session, identifier, *args):
        if identifier == snapshot["busy-card"]["id"]:
            raise ReferenceError("reference_write_busy", 503)
        return original(session, identifier, *args)
    monkeypatch.setattr(actions, "_card", card)
    result = client.post("/api/tags/bulk-review", json={"items": [
        {"tag_id": snapshot[name]["id"], "locale": "en", "expected_reference_revision": snapshot[name]["reference_revision"]}
        for name in ("busy-card", "ready-card")]})
    assert result.status_code == 200, result.text
    assert [item["status"] for item in result.json()["results"]] == ["failed", "reviewed"]
    assert result.json()["results"][0]["code"] == "reference_write_busy"


def tags_by_name(client):
    return {item["name"]: item for item in client.get("/api/tags").json()["tags"]}


def test_bulk_review_reports_changed_and_occupied_cards(client):
    login(client, "alice")
    registry = TagRegistry()
    registry.add(["review-a", "review-b", "review-c"])
    for name, tag in tags_by_name(client).items():
        registry.set_translation(tag["id"], "en", name + " machine", is_machine=True)
    snapshot = tags_by_name(client)
    b = snapshot["review-b"]
    registry.set_translation(b["id"], "en", "Changed", reviewed_by="human")
    c = snapshot["review-c"]
    assert client.post(f'/api/reference-edit-locks/tag/{c["id"]}', json={"edit_token": "a" * 64}).status_code == 200
    response = client.post("/api/tags/bulk-review", json={"items": [
        {"tag_id": snapshot[name]["id"], "locale": "en", "expected_reference_revision": snapshot[name]["reference_revision"]}
        for name in ["review-a", "review-b", "review-c"]]})
    assert response.status_code == 200, response.text
    outcomes = {row["tag_id"]: row["status"] for row in response.json()["results"]}
    assert outcomes[snapshot["review-a"]["id"]] == "reviewed"
    assert outcomes[b["id"]] == "skipped_changed"
    assert outcomes[c["id"]] == "skipped_locked"
    assert response.json()["reviewed"] == 1


def test_two_locales_of_one_card_increment_revision_only_once(client):
    login(client, "alice")
    registry = TagRegistry(); tag = registry.all()[0]
    for locale in ["en", "de"]:
        registry.set_translation(tag["id"], locale, locale + " machine", is_machine=True)
    tag = registry.all()[0]
    response = client.post("/api/tags/bulk-review", json={"items": [
        {"tag_id": tag["id"], "locale": locale, "expected_reference_revision": tag["reference_revision"]} for locale in ["en", "de"]]})
    assert response.status_code == 200, response.text
    assert response.json()["reviewed"] == 2
    assert len(response.json()["results"]) == 1
    assert registry.all()[0]["reference_revision"] == tag["reference_revision"] + 1


def test_cleanup_preserves_new_tags_and_new_document_usage(client):
    login(client, "alice")
    registry = TagRegistry(); registry.add(["unused", "new-use"])
    preview = client.post("/api/tags/cleanup", json={"confirm": False})
    assert preview.status_code == 200, preview.text
    batch = preview.json()["batches"][0]
    registry.add(["after-preview"])
    get_registry().create("abcdabcdabcdabcd", "test.txt", "hash", 1, tags=["new-use"])
    response = client.post("/api/tags/cleanup", json={"confirm": True, **batch})
    assert response.status_code == 200, response.text
    remaining = tags_by_name(client)
    assert "unused" not in remaining
    assert "after-preview" in remaining and "new-use" in remaining
    assert any(item["status"] == "skipped_used" for item in response.json()["results"])


def test_cleanup_does_not_delete_an_occupied_tag_and_rejects_changed_preview(client):
    login(client, "alice")
    tag = TagRegistry().all()[0]
    batch = client.post("/api/tags/cleanup", json={"confirm": False}).json()["batches"][0]
    client.post(f'/api/reference-edit-locks/tag/{tag["id"]}', json={"edit_token": "a" * 64})
    response = client.post("/api/tags/cleanup", json={"confirm": True, **batch})
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["status"] == "skipped_locked"
    assert TagRegistry().all()
    refused = client.post("/api/tags/cleanup", json={"confirm": True, **batch, "preview_digest": "bad"})
    assert refused.status_code == 409


def test_legacy_bulk_clients_cannot_bypass_snapshot_checks(client):
    login(client, "alice")
    assert client.post("/api/tags/bulk-review", json={"tag_ids": [1]}).status_code == 428
    assert client.post("/api/tags/cleanup").status_code == 428


def test_single_delete_requires_revision_and_own_lease(client):
    login(client, "alice")
    tag = TagRegistry().all()[0]
    path = f'/api/tags/{tag["name"]}'
    assert client.delete(path).status_code == 428
    acquired = client.post(f'/api/reference-edit-locks/tag/{tag["id"]}', json={"edit_token": "a" * 64}).json()
    query = f'?expected_reference_revision={tag["reference_revision"]}'
    assert client.delete(path + query).status_code == 428
    deleted = client.delete(path + query, headers={"X-Reference-Edit-Token": "a" * 64,
        "X-Reference-Edit-Epoch": str(acquired["lease_epoch"])})
    assert deleted.status_code == 200, deleted.text
