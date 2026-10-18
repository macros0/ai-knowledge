import pytest
from sqlalchemy.exc import IntegrityError

from app.auth.models import User
from app.db.models import AttributeValue
from app.db.session import session_scope
from app.services.attribute_registry import AttributeRegistry
from app.services.reference_concurrency import ReferenceError
from tests.test_reference_edit_lock_api import client as reference_client, login  # noqa: F401
from app.services.reference_mutation import ReferenceWriteContext
from app.services.reference_resources import ReferenceKey


client = reference_client


def context(kind, code, revision, lease=None, subkind=None):
    key = ReferenceKey(kind, code, kind=subkind)
    return ReferenceWriteContext(User(user_id="admin", username="Admin", roles=["admin"]),
        {key: revision}, {key: lease} if lease else {})


def test_catalog_revision_rejects_stale_remove_and_keeps_duplicate_add_idempotent():
    registry = AttributeRegistry()
    actor = User(user_id="a", username="Alice", roles=["editor"])
    first = registry.add("module", "PY", expected_reference_revision=1, actor=actor)
    assert first["reference_revision"] == 2
    duplicate = registry.add("module", "PY", expected_reference_revision=1, actor=actor)
    assert duplicate["id"] == first["id"]
    assert duplicate["reference_revision"] == 2
    registry.add("module", "PA", expected_reference_revision=2, actor=actor)
    with pytest.raises(ReferenceError) as exc:
        registry.remove("module", "PY", expected_reference_revision=2, actor=actor)
    assert exc.value.code == "version_conflict"
    assert registry.values("module") == ["PA", "PY"]


def test_global_catalog_remove_does_not_delete_a_scoped_value():
    registry = AttributeRegistry()
    registry.add("module", "PY")
    registry.add("module", "PY", org_id=1)
    assert registry.remove("module", "PY")
    assert registry.values("module", org_id=1) == ["PY"]


def test_null_organization_duplicates_are_rejected_by_the_database():
    AttributeRegistry().add("module", "PY")
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(AttributeValue(attribute_key="module", value="PY", org_id=None))


def test_attribute_api_requires_the_observed_catalog_revision(client):
    login(client, "alice")
    snapshot = client.get("/api/attributes/module").json()
    assert snapshot["reference_revision"] == 1
    assert client.post("/api/attributes/module", json={"value": "PY"}).status_code == 428
    saved = client.post("/api/attributes/module", json={"value": "PY", "expected_reference_revision": 1})
    assert saved.status_code == 200, saved.text
    assert saved.json()["reference_revision"] == 2
    refused = client.delete("/api/attributes/module/PY?expected_reference_revision=1")
    assert refused.status_code == 409, refused.text


def test_stopword_confirm_keeps_a_word_added_after_preview():
    from app.services import locale_service as service
    service.create_locale("zz", "Test")
    user = User(user_id="admin", username="Admin", roles=["admin"])
    preview = service.import_stopwords("zz", ["one"], "bm25", "replace", confirm=False, user=user)
    service.add_stopword("zz", "two", "bm25", user=user)
    with pytest.raises(ReferenceError) as exc:
        service.import_stopwords("zz", ["one"], "bm25", "replace", confirm=True, user=user,
            preview_digest=preview["preview_digest"],
            reference_context=context("stopword_set", "zz", preview["reference_revision"], subkind="bm25"))
    assert exc.value.code == "version_conflict"
    assert [row["word"] for row in service.list_stopwords("zz", "bm25")] == ["two"]


def test_stopword_confirm_binds_the_exact_preview_payload():
    from app.services import locale_service as service
    service.create_locale("zz", "Test")
    user = User(user_id="admin", username="Admin", roles=["admin"])
    preview = service.import_stopwords("zz", ["one"], "bm25", "merge", confirm=False, user=user)
    with pytest.raises(ReferenceError) as exc:
        service.import_stopwords("zz", ["other"], "bm25", "merge", confirm=True, user=user,
            preview_digest=preview["preview_digest"],
            reference_context=context("stopword_set", "zz", preview["reference_revision"], subkind="bm25"))
    assert exc.value.code == "reference_preview_stale"
    assert service.list_stopwords("zz", "bm25") == []


def test_locale_card_rejects_a_stale_rename():
    from app.services import locale_service as service
    original = service.create_locale("zz", "Test")
    first = service.update_locale("zz", name="Alice", reference_context=context("locale", "zz", original["reference_revision"]))
    assert first["reference_revision"] == 2
    with pytest.raises(ReferenceError):
        service.update_locale("zz", name="Bob", reference_context=context("locale", "zz", original["reference_revision"]))
    assert service.get_locale("zz")["name"] == "Alice"


def test_dictionary_confirm_checks_revision_and_explicit_absent_active_version():
    from app.services import locale_service, ui_dictionary as service
    locale_service.create_locale("zz", "Test")
    payload = {"nav.documents": "One"}
    preview = service.import_dictionary("zz", payload, None, "Admin", confirm=False)
    ctx = context("ui_dictionary", "zz", preview["reference_revision"])
    with pytest.raises(ReferenceError) as exc:
        service.import_dictionary("zz", payload, None, "Admin", confirm=True, reference_context=ctx,
                                  preview_digest=preview["preview_digest"])
    assert exc.value.status_code == 428
    saved = service.import_dictionary("zz", payload, None, "Admin", confirm=True, reference_context=ctx,
        preview_digest=preview["preview_digest"], expected_active_version=None)
    assert saved["reference_revision"] == 2
    with pytest.raises(ReferenceError) as exc:
        service.import_dictionary("zz", payload, None, "Admin", confirm=True, reference_context=ctx,
            preview_digest=preview["preview_digest"], expected_active_version=None)
    assert exc.value.code == "version_conflict"


def test_dictionary_revision_is_monotonic_through_active_a_b_a_and_inactive_import():
    from app.services import locale_service, ui_dictionary as service
    locale_service.create_locale("zz", "Test")
    first = service.import_dictionary("zz", {"nav.documents": "A"}, None, "Admin", confirm=True)
    second = service.import_dictionary("zz", {"nav.documents": "B"}, None, "Admin", confirm=True)
    first_entry = next(row for row in service.history("zz") if row["version"] == first["version"])
    rollback = service.rollback("zz", first_entry["id"])
    assert first["reference_revision"] < second["reference_revision"] < rollback["reference_revision"]
    inactive = service.import_dictionary("zz", {"nav.documents": "Draft"}, None, "Admin", confirm=True, activate=False)
    assert inactive["reference_revision"] > rollback["reference_revision"]
    assert service.get_active("zz")["version"] == first["version"]


def test_set_http_protocol_requires_the_lease_and_original_preview(client):
    from app.services.locale_service import create_locale
    create_locale("zz", "Test")
    login(client, "admin")
    stopwords = "/api/admin/locales/zz/stopwords"
    preview = client.post(stopwords + "/import", json={"words": ["one"], "confirm": False}).json()
    assert len(preview["preview_digest"]) == 64
    assert client.post(stopwords, json={"word": "two", "expected_reference_revision": 1}).status_code == 428
    lock = client.post("/api/reference-edit-locks/stopword_set/zz?kind=bm25", json={"edit_token": "a" * 64}).json()
    headers = {"X-Reference-Edit-Token": "a" * 64, "X-Reference-Edit-Epoch": str(lock["lease_epoch"])}
    body = {"words": ["one"], "confirm": True, "expected_reference_revision": preview["reference_revision"],
            "preview_digest": preview["preview_digest"]}
    saved = client.post(stopwords + "/import", json=body, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["reference_revision"] == 2
    stale = client.post(stopwords + "/import", json=body, headers=headers)
    assert stale.status_code == 409, stale.text
    assert client.get(stopwords).json()["reference_revisions"]["bm25"] == 2


def test_dictionary_http_confirms_an_explicit_null_active_version(client):
    from app.services.locale_service import create_locale
    create_locale("zz", "Test")
    login(client, "admin")
    path = "/api/admin/locales/zz/ui-dictionary"
    preview = client.post(path + "/import", json={"data": {"nav.documents": "Docs"}, "confirm": False}).json()
    lock = client.post("/api/reference-edit-locks/ui_dictionary/zz", json={"edit_token": "a" * 64}).json()
    headers = {"X-Reference-Edit-Token": "a" * 64, "X-Reference-Edit-Epoch": str(lock["lease_epoch"])}
    body = {"data": {"nav.documents": "Docs"}, "confirm": True, "preview_digest": preview["preview_digest"],
        "expected_reference_revision": preview["reference_revision"]}
    assert client.post(path + "/import", json=body, headers=headers).status_code == 428
    saved = client.post(path + "/import", json={**body, "expected_active_version": None}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["reference_revision"] == 2
    assert client.get(path).json()["reference_revision"] == 2
    # Activating a dictionary also changes the locale card's visible version pointer.
    stale_locale = client.patch("/api/admin/locales/zz", json={"name": "Stale", "expected_reference_revision": 1})
    assert stale_locale.status_code == 409, stale_locale.text


def test_startup_seed_does_not_restore_words_inside_an_occupied_set():
    from app.services.stopwords import ensure_seeded
    from app.services.reference_edit_lock import acquire_edit_lock
    from app.db.models import Stopword
    ensure_seeded()
    with session_scope() as session:
        session.delete(session.get(Stopword, ("en", "the", "bm25")))
        acquire_edit_lock(session, ReferenceKey("stopword_set", "en", kind="bm25"),
            User(user_id="admin", username="Admin", roles=["admin"]), "a" * 64)
    result = ensure_seeded()
    assert result["skipped_locked"] == 1
    with session_scope() as session:
        assert session.get(Stopword, ("en", "the", "bm25")) is None
