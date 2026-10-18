import pytest

from app.auth.models import User
from app.db.session import session_scope
from app.services.glossary.registry import GlossaryRegistry
from app.services.reference_concurrency import ReferenceError
from app.services.reference_edit_lock import acquire_edit_lock
from app.services.reference_resources import ReferenceKey
from tests.test_reference_edit_lock_api import client as reference_client, login

client = reference_client


def term():
    return GlossaryRegistry().create(None, "business_term", "Рабочее время", None, "ru", "admin")


def test_source_and_translation_share_one_revision_and_lease(client):
    row = term()
    login(client, "admin")
    path = f'/api/admin/glossary/{row["id"]}'
    body = {"version": row["version"], "original_name": "Новый исходник"}
    assert client.patch(path + "/source", json=body).status_code == 428
    lock_path = f'/api/reference-edit-locks/glossary_term/{row["id"]}'
    lock = client.post(lock_path, json={"edit_token": "a" * 64}).json()
    headers = {"X-Reference-Edit-Token": "a" * 64, "X-Reference-Edit-Epoch": str(lock["lease_epoch"])}
    response = client.patch(path + "/source", json={**body, "expected_reference_revision": 1}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["reference_revision"] == 2
    translation = {"translation_version": 0, "source_revision": response.json()["source_revision"],
                   "display_name": "Working time", "expected_reference_revision": 1}
    stale = client.patch(path + "/translations/en", json=translation, headers=headers)
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "version_conflict"
    login(client, "bob")
    refused = client.patch(path + "/translations/en", json={**translation, "expected_reference_revision": 2})
    assert refused.status_code == 428, refused.text
    assert client.post(lock_path, json={"edit_token": "b" * 64}).status_code == 409
    login(client, "admin")
    saved = client.patch(path + "/translations/en", json={**translation, "expected_reference_revision": 2}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["reference_revision"] == 3


def test_internal_writer_cannot_bypass_an_occupied_term():
    row = term()
    key = ReferenceKey("glossary_term", row["id"])
    with session_scope() as session:
        acquire_edit_lock(session, key, User(user_id="a", username="Alice", roles=["admin"]), "a" * 64)
    with pytest.raises(ReferenceError) as exc:
        GlossaryRegistry().add_alias(row["id"], row["version"], "Учет рабочего времени")
    assert exc.value.code == "reference_edit_locked"
    assert GlossaryRegistry().get(row["id"])["aliases"] == []


def test_glossary_backfill_skips_a_lease_before_calling_llm(monkeypatch):
    from app.config import Settings
    from app.services.glossary.translations import backfill_glossary_translations
    row = term()
    actor = User(user_id="a", username="Alice", roles=["admin"])
    with session_scope() as session:
        acquire_edit_lock(session, ReferenceKey("glossary_term", row["id"]), actor, "a" * 64)
    monkeypatch.setattr("app.services.glossary.translations.get_settings", lambda: Settings(_env_file=None, translation_provider="llm"))
    def unexpected(*args, **kwargs):
        pytest.fail("LLM called for an occupied resource")
    monkeypatch.setattr("app.services.glossary.translations.translate_texts_batch", unexpected)
    result = backfill_glossary_translations("en", [row["id"]], user=actor)
    assert result["skipped_locked"] == 1


def test_lease_acquired_during_llm_prevents_machine_commit(monkeypatch):
    from app.config import Settings
    from app.services.glossary.translations import backfill_glossary_translations
    row = term()
    actor = User(user_id="a", username="Alice", roles=["admin"])
    monkeypatch.setattr("app.services.glossary.translations.get_settings", lambda: Settings(_env_file=None, translation_provider="llm"))
    def translate(*args, **kwargs):
        with session_scope() as session:
            acquire_edit_lock(session, ReferenceKey("glossary_term", row["id"]), actor, "a" * 64)
        return ["Machine"]
    monkeypatch.setattr("app.services.glossary.translations.translate_texts_batch", translate)
    result = backfill_glossary_translations("en", [row["id"]], user=actor)
    assert result["skipped_locked"] == 1
    current = GlossaryRegistry().get(row["id"])
    assert current["translations"] == []
    assert current["reference_revision"] == 1


def test_merge_cannot_bypass_a_source_lease_and_preserves_both_terms():
    from uuid import uuid4
    registry = GlossaryRegistry()
    source = term()
    target = registry.create(None, "business_term", "Время труда", None, "ru", "admin")
    request = {"request_id": str(uuid4()), "source_term_id": source["id"], "target_term_id": target["id"],
               "selections": {"original_name": "target"}}
    preview = registry.merge_preview(request)
    with session_scope() as session:
        acquire_edit_lock(session, ReferenceKey("glossary_term", source["id"]),
                          User(user_id="a", username="Alice", roles=["admin"]), "a" * 64)
    with pytest.raises(ReferenceError) as exc:
        registry.merge(request, preview["digest"], actor_id="admin")
    assert exc.value.code == "reference_edit_locked"
    assert len(registry.list()) == 2


def test_rule_source_edit_preview_keeps_the_observed_common_revision():
    from app.services.glossary.rule_registry import GlossaryRuleRegistry
    registry = GlossaryRuleRegistry()
    source = registry.create(name="Source", number_from=0, number_to=99, prefixes=["IT"])
    target = registry.create(name="Target", number_from=100, number_to=199, prefixes=["IT"])
    source = registry.update(source["id"], source["version"], name="Updated")
    preview = registry.merge_preview({"source_rule_id": source["id"], "target_rule_id": target["id"],
        "source_edit": {"name": "Draft", "number_from": 0, "number_to": 99, "prefixes": ["IT"]}})
    assert preview["source"]["reference_revision"] == source["reference_revision"] == 2


def test_a_recreated_rule_cannot_reuse_the_tombstone_revision():
    from app.services.glossary.rule_registry import GlossaryRuleRegistry
    from app.services.reference_concurrency import current_reference_revision
    registry = GlossaryRuleRegistry()
    first = registry.create(name="First", number_from=0, number_to=99, prefixes=["IT"])
    registry.delete(first["id"], first["version"])
    with session_scope() as session:
        retired = current_reference_revision(session, ReferenceKey("glossary_rule", first["id"]))
    second = registry.create(name="Second", number_from=0, number_to=99, prefixes=["IT"])
    assert second["id"] == first["id"]  # SQLite can reuse a hard-deleted row ID.
    assert second["reference_revision"] > retired


def test_offline_prepare_respects_an_editor_and_backup_omits_lease_credentials():
    from app.db.models import ReferenceResourceState, GlossaryState
    from scripts.migrate_glossary_identities import _set_ready
    from scripts.glossary_migration_metadata import public_metadata, read_metadata
    row = term()
    key = ReferenceKey("glossary_term", row["id"])
    with session_scope() as session:
        acquire_edit_lock(session, key, User(user_id="private-owner", username="Private owner", roles=["admin"]), "a" * 64)
        hashed = session.get(ReferenceResourceState, key.canonical).token_hash
        public = repr(public_metadata(read_metadata(session)))
        assert hashed not in public
        assert "private-owner" not in public
    with pytest.raises(ReferenceError) as exc:
        _set_ready(False)
    assert exc.value.code == "reference_edit_locked"
    with session_scope() as session:
        assert session.get(GlossaryState, 1).identities_ready
