from tests.test_processing_scope_api import upload_mail
from tests import test_processing_scope_api as scope_fixtures

upload_client = scope_fixtures.upload_client
base_upload_client = scope_fixtures.base_upload_client  # noqa:F401


def test_pending_original_download_is_scoped_to_document(upload_client):
    client, settings, _ = upload_client
    settings.attachment_review_enabled = True
    settings.dedup_enabled = False
    import app.api.document_processing_scope as api

    # Fixture explicitly isolates module settings instead of using the main runtime.
    original = api.get_settings
    api.get_settings = lambda: settings
    try:
        doc = upload_mail(client, 600).json()
        route = f"/api/documents/{doc['id']}/processing-scope"
        node = client.get(route).json()["pending"]["inventory"]["nodes"][0]
        response = client.get(route + "/attachments/" + node["source_key"])
        assert response.status_code == 200, response.text
        assert response.content.startswith(b"PK")
        assert client.get(route + "/attachments/" + "f" * 64).status_code == 404
    finally:
        api.get_settings = original


def test_expansion_preserves_attachment_name_and_download(upload_client, monkeypatch):
    from app.db.models import DocumentProcessingReview
    from app.db.session import session_scope
    import app.api.document_processing_scope as api

    client, settings, _ = upload_client
    settings.attachment_review_enabled = True
    settings.dedup_enabled = False
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    doc = upload_mail(client, 600).json()
    route = f"/api/documents/{doc['id']}/processing-scope"
    review = client.get(route).json()["pending"]
    node = review["inventory"]["nodes"][0]
    with session_scope() as session:
        row = session.get(DocumentProcessingReview, doc["id"])
        inventory = dict(row.inventory_json)
        inventory["nodes"] = [dict(n, assessment="unknown") for n in inventory["nodes"]]
        row.inventory_json = inventory
    expanded = client.post(route + "/review", json={
        "expected_active_generation_id": None, "review_id": review["id"],
        "expected_revision": review["revision"], "expand_source_keys": [node["source_key"]],
    })
    assert expanded.status_code == 200, expanded.text
    current = expanded.json()["pending"]["inventory"]["nodes"][0]
    assert current["source_key"] == node["source_key"]
    assert current["parent_key"] == node["parent_key"]
    assert current["display_name"] == node["display_name"] == "table.xlsx"
    downloaded = client.get(route + "/attachments/" + node["source_key"])
    assert downloaded.status_code == 200
    assert 'filename="table.xlsx"' in downloaded.headers["content-disposition"]
