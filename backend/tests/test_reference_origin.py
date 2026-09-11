"""Reference translations always start from the element's original language."""
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.db.models import AttributeValueTranslation, DevelopmentTranslation, TagTranslation
from app.db.session import session_scope
from app.services.attribute_registry import AttributeRegistry
from app.services.development_registry import DevelopmentRegistry
from app.services.tag_registry import TagRegistry
from app.services.translation import backfill_reference_data, count_pending, translate_texts_batch


class User:
    user_id = "admin"
    username = "admin"


def create(entity, text, locale):
    if entity == "tags":
        reg = TagRegistry()
        return reg.get_or_create_ids([text], canonical_locale=locale)[0]
    if entity == "developments":
        return DevelopmentRegistry().create(text, text, canonical_locale=locale)["id"]
    return AttributeRegistry().add("module", text, label=text, canonical_locale=locale)["id"]


@pytest.mark.parametrize("entity", ["tags", "developments", "attributes"])
def test_backfill_uses_each_original_and_skips_target_originals(entity):
    create(entity, "Payroll", "en")
    create(entity, "Urlaub", "de")
    create(entity, "Congé", "fr")
    assert count_pending("fr", [entity]) == {entity: 2}
    with patch("app.services.translation.translate_texts_batch", return_value=["Traduit"]) as tr:
        result = backfill_reference_data("fr", [entity], user=User())
    assert result[entity] == {"created": 2, "failed": 0}
    assert [(c.args[0], c.args[1], c.kwargs["source_locale"]) for c in tr.call_args_list] == [
        (["Payroll"], "fr", "en"), (["Urlaub"], "fr", "de")
    ]
    # A subsequent target still uses the original, never the French translation.
    with patch("app.services.translation.translate_texts_batch", return_value=["Перевод"]) as tr:
        backfill_reference_data("ru", [entity], user=User())
    assert {c.kwargs["source_locale"] for c in tr.call_args_list} == {"en", "de", "fr"}
    assert [c.args[0][0] for c in tr.call_args_list] == ["Payroll", "Urlaub", "Congé"]


def test_reusing_tag_and_attribute_does_not_change_origin():
    tid = create("tags", "Payroll", "en")
    assert create("tags", "Payroll", "de") == tid
    assert TagRegistry().all()[0]["canonical_locale"] == "en"
    aid = create("attributes", "Payroll", "en")
    assert create("attributes", "Payroll", "de") == aid
    assert AttributeRegistry().list("module")[0]["canonical_locale"] == "en"


@pytest.mark.parametrize("entity,model,text_field", [
    ("tags", TagTranslation, "text"),
    ("developments", DevelopmentTranslation, "name"),
    ("attributes", AttributeValueTranslation, "label"),
])
def test_russian_translation_is_displayed_and_reviewed_value_preserved(entity, model, text_field):
    create(entity, "Payroll", "en")
    backfill_reference_data("ru", [entity], translations={"Payroll": "Зарплата"}, user=User())
    with session_scope() as s:
        row = s.execute(select(model)).scalar_one()
        row.reviewed_by = "editor"
    with patch("app.services.translation.translate_texts_batch") as tr:
        assert backfill_reference_data("ru", [entity], user=User())[entity]["created"] == 0
        tr.assert_not_called()
    if entity == "tags":
        assert TagRegistry().all(locale="ru")[0]["display"] == "Зарплата"
    elif entity == "developments":
        reg = DevelopmentRegistry()
        items, _ = reg.query()
        reg.add_display_names(items, "ru")
        assert items[0]["display_name"] == "Зарплата"
    else:
        reg = AttributeRegistry()
        items = reg.list("module")
        reg.add_display_labels(items, "ru")
        assert items[0]["display_label"] == "Зарплата"


def test_translator_prompt_names_actual_source(monkeypatch):
    monkeypatch.setenv("TRANSLATION_PROVIDER", "llm")
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        with patch("app.services.llm_client.LLMClient") as client:
            client.return_value.chat_json.return_value = ["Congé"]
            assert translate_texts_batch(["Urlaub"], "fr", source_locale="de") == ["Congé"]
            prompt = client.return_value.chat_json.call_args.args[0]
            assert "from de" in prompt
            assert "Russian" not in prompt
    finally:
        get_settings.cache_clear()


def test_api_records_creation_locale_and_preserves_it_on_edit(monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    settings = Settings(_env_file=None, auth_provider="disabled", api_prefix="")
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    client = TestClient(create_app())
    client.cookies.set("okf.locale", "ru")
    response = client.post("/developments", json={"number": "en-1", "name": "Payroll", "canonical_locale": "en"})
    assert response.status_code == 200, response.text
    dev = response.json()
    assert dev["canonical_locale"] == "en"
    response = client.patch(f'/developments/{dev["id"]}', json={"version": dev["version"], "name": "Payroll register"})
    assert response.status_code == 200, response.text
    assert response.json()["canonical_locale"] == "en"
    response = client.post("/attributes/module", json={"value": "HR", "label": "Personal", "canonical_locale": "de"})
    assert response.status_code == 200, response.text
    assert response.json()["canonical_locale"] == "de"
    assert client.post("/developments", json={"number": "bad", "name": "Bad", "canonical_locale": "INVALID language"}).status_code == 422


def test_api_cookie_is_default_only_for_new_elements(monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    from app.services.stopwords import ensure_seeded
    ensure_seeded()
    settings = Settings(_env_file=None, auth_provider="disabled", api_prefix="")
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    client = TestClient(create_app())
    client.cookies.set("okf.locale", "en")
    response = client.post("/attributes/module", json={"value": "Payroll", "label": "Payroll"})
    assert response.status_code == 200, response.text
    assert response.json()["canonical_locale"] == "en"
    client.cookies.set("okf.locale", "ru")
    assert client.post("/attributes/module", json={"value": "Payroll"}).json()["canonical_locale"] == "en"


def test_tag_origin_reaches_registry_through_document_updates(monkeypatch):
    from app.services.registry import DocumentRegistry
    from app.services import document_tag_service as service
    reg = DocumentRegistry()
    reg.create("origin-doc", "test.pdf", "application/pdf", 1, tags=["Payroll"], canonical_locale="en")
    monkeypatch.setattr(service, "schedule_document_tags_sync", lambda *_: None)
    monkeypatch.setattr(service, "reindex_document_dev_tags", lambda *_: True)
    service.update_document_tags("origin-doc", ["Payroll", "Urlaub"], User(), canonical_locale="de")
    service.bulk_update_tags(["origin-doc"], ["Congé"], [], User(), canonical_locale="fr")
    assert {t["name"]: t["canonical_locale"] for t in TagRegistry().all()} == {
        "Payroll": "en", "Urlaub": "de", "Congé": "fr",
    }


def test_migration_is_idempotent_and_does_not_invent_historical_language(tmp_path):
    from sqlalchemy import create_engine, text
    from scripts.migrate_reference_origin import migrate
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        for table in ("developments", "attribute_values"):
            conn.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value TEXT)"))
            conn.execute(text(f"INSERT INTO {table} (id, value) VALUES (1, 'Payroll')"))
    migrate(engine)
    migrate(engine)
    with engine.connect() as conn:
        for table in ("developments", "attribute_values"):
            assert tuple(conn.execute(text(f"SELECT value, canonical_locale FROM {table}")).one()) == ("Payroll", "und")
    engine.dispose()
