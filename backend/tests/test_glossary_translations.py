import threading

import pytest

from app.config import Settings
from app.db.models import DomainTermTranslation
from app.db.session import session_scope
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.translations import (
    GlossaryTranslationConflictError,
    backfill_glossary_translations,
    review_glossary_translation,
    set_glossary_translation,
    pending_glossary_term_ids,
)
from app.services.translation import count_pending, translate_texts_batch


class _User:
    user_id = "user-id"
    username = "editor"


def _settings(**overrides):
    values = {"_env_file": None, "translation_provider": "llm", "translation_model": "test/translator"}
    values.update(overrides)
    return Settings(**values)


def _term(*, locale="de", description="Deutsche Beschreibung"):
    return GlossaryRegistry().create(
        "IT0003",
        "sap_infotype",
        "Deutscher Name",
        description,
        locale,
        created_by="admin",
    )


def test_unreviewed_human_translation_is_protected_before_model_and_at_commit(monkeypatch):
    from app.services.glossary.translations import _write_machine_translation
    monkeypatch.setattr("app.services.glossary.translations.get_settings", lambda: _settings())
    term = _term()
    with session_scope() as session:
        session.add(DomainTermTranslation(term_id=term["id"], locale="ru", display_name="Ручной перевод",
            description="Из объединённой карточки", is_machine_translated=False, reviewed_by=None,
            version=1, source_revision=term["source_revision"]))
    def forbidden(*_args, **_kwargs):
        pytest.fail("manual text must never be sent for machine replacement")
    monkeypatch.setattr("app.services.glossary.translations.translate_texts_batch", forbidden)
    result = backfill_glossary_translations("ru", [term["id"]], user=_User())
    assert result["skipped_reviewed"] == 1
    outcome = _write_machine_translation(term_id=term["id"], locale="ru", source_revision=term["source_revision"],
        expected_version=1, expected_reference_revision=term["reference_revision"],
        display_name="Machine overwrite", description=None, user=_User(), ip_address=None)
    assert outcome == "skipped_reviewed"
    assert GlossaryRegistry().get(term["id"])["translations"][0]["display_name"] == "Ручной перевод"


def test_backfill_translates_name_and_description_from_canonical_source_even_when_machine_translation_exists(monkeypatch):
    monkeypatch.setattr("app.services.glossary.translations.get_settings", lambda: _settings())
    term = _term()
    with session_scope() as session:
        session.add(
            DomainTermTranslation(
                term_id=term["id"],
                locale="ru",
                display_name="Старый перевод",
                description="Старое описание",
                source_revision=term["source_revision"],
                is_machine_translated=True,
            )
        )

    calls = []

    def translate(texts, target, **kwargs):
        calls.append((texts, target, kwargs))
        return [f"{target} name", f"{target} description"]

    monkeypatch.setattr("app.services.glossary.translations.translate_texts_batch", translate)
    results = [
        backfill_glossary_translations(locale, [term["id"]], user=_User())
        for locale in ("ru", "en", "fr")
    ]

    assert [result["created"] + result["updated"] for result in results] == [1, 1, 1]
    assert all(result["failed"] == 0 for result in results)
    assert calls == [
        (["Deutscher Name", "Deutsche Beschreibung"], locale, {"source_locale": "de", "strict": True})
        for locale in ("ru", "en", "fr")
    ]
    current = GlossaryRegistry().get(term["id"])
    assert {item["locale"] for item in current["translations"]} == {"en", "fr", "ru"}
    assert all(item["is_machine_translated"] for item in current["translations"])


def test_backfill_preserves_und_source_skips_same_locale_and_keeps_null_description(monkeypatch):
    monkeypatch.setattr("app.services.glossary.translations.get_settings", lambda: _settings())
    unknown = _term(locale="und", description=None)
    same = GlossaryRegistry().create("PA20", "sap_transaction", "Русское имя", "Описание", "ru")
    calls = []

    def translate(texts, target, **kwargs):
        calls.append((texts, target, kwargs))
        return ["Translated name"]

    monkeypatch.setattr("app.services.glossary.translations.translate_texts_batch", translate)
    result = backfill_glossary_translations("ru", [unknown["id"], same["id"]], user=_User())

    assert result["created"] == 1
    assert result["skipped_same_locale"] == 1
    assert calls == [
        (["Deutscher Name"], "ru", {"source_locale": "und", "strict": True})
    ]
    assert GlossaryRegistry().get(unknown["id"])["translations"][0]["description"] is None


def test_manual_translation_requires_source_and_translation_versions_and_marks_human(monkeypatch):
    term = _term()
    result = set_glossary_translation(
        term["id"],
        "ru",
        display_name="Ручное имя",
        description="Ручное описание",
        translation_version=0,
        source_revision=term["source_revision"],
        user=_User(),
    )

    assert result["translations"][0]["reviewed_by"] == "editor"
    assert result["translations"][0]["is_machine_translated"] is False
    with pytest.raises(GlossaryTranslationConflictError):
        set_glossary_translation(
            term["id"],
            "ru",
            display_name="Конфликт",
            description=None,
            translation_version=0,
            source_revision=term["source_revision"],
            user=_User(),
        )


def test_machine_translation_can_be_reviewed_with_its_own_version(monkeypatch):
    monkeypatch.setattr("app.services.glossary.translations.get_settings", lambda: _settings())
    term = _term(description=None)
    monkeypatch.setattr(
        "app.services.glossary.translations.translate_texts_batch",
        lambda texts, target, **kwargs: ["Machine name"],
    )
    backfill_glossary_translations("ru", [term["id"]], user=_User())
    current = GlossaryRegistry().get(term["id"])["translations"][0]

    reviewed = review_glossary_translation(
        term["id"],
        "ru",
        translation_version=current["version"],
        source_revision=term["source_revision"],
        user=_User(),
    )
    assert reviewed["translations"][0]["reviewed_by"] == "editor"
    assert reviewed["translations"][0]["is_machine_translated"] is False
    assert pending_glossary_term_ids("ru") == []


def test_legacy_translation_backfill_and_pending_accept_glossary_entity(monkeypatch):
    settings = _settings()
    monkeypatch.setattr("app.services.translation.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.glossary.translations.get_settings", lambda: settings)
    _term(description=None)
    monkeypatch.setattr(
        "app.services.glossary.translations.translate_texts_batch",
        lambda texts, target, **kwargs: ["Translated"],
    )

    assert count_pending("ru", ["glossary"]) == {"glossary": 1}
    from app.services.translation import backfill_reference_data

    result = backfill_reference_data("ru", ["glossary"], user=_User())
    assert result["glossary"]["created"] == 1
    assert result["glossary"]["failed"] == 0


def test_backfill_race_does_not_overwrite_translation_reviewed_while_llm_was_running(monkeypatch):
    monkeypatch.setattr("app.services.glossary.translations.get_settings", lambda: _settings())
    term = _term()
    started = threading.Event()
    release = threading.Event()

    def translate(texts, target, **kwargs):
        started.set()
        assert release.wait(5)
        return ["Machine name", "Machine description"]

    monkeypatch.setattr("app.services.glossary.translations.translate_texts_batch", translate)
    outcome = {}

    def run():
        outcome.update(backfill_glossary_translations("ru", [term["id"]], user=_User()))

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(5)
    set_glossary_translation(
        term["id"],
        "ru",
        display_name="Ручное имя",
        description="Ручное описание",
        translation_version=0,
        source_revision=term["source_revision"],
        user=_User(),
    )
    release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert outcome["skipped_reviewed"] == 1
    translation = GlossaryRegistry().get(term["id"])["translations"][0]
    assert translation["display_name"] == "Ручное имя"
    assert translation["reviewed_by"] == "editor"


def test_backfill_race_does_not_write_result_for_changed_original(monkeypatch):
    monkeypatch.setattr("app.services.glossary.translations.get_settings", lambda: _settings())
    term = _term()
    started = threading.Event()
    release = threading.Event()

    def translate(texts, target, **kwargs):
        started.set()
        assert release.wait(5)
        return ["Machine name", "Machine description"]

    monkeypatch.setattr("app.services.glossary.translations.translate_texts_batch", translate)
    outcome = {}

    def run():
        outcome.update(backfill_glossary_translations("ru", [term["id"]], user=_User()))

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(5)
    GlossaryRegistry().update(
        term["id"], term["version"], original_name="Новое исходное имя", updated_by="admin"
    )
    release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert outcome["skipped_changed"] == 1
    assert GlossaryRegistry().get(term["id"])["translations"] == []


def test_backfill_provider_off_and_batch_limit(monkeypatch):
    monkeypatch.setattr("app.services.glossary.translations.get_settings", lambda: _settings(translation_provider="off"))
    term = _term()
    result = backfill_glossary_translations("ru", [term["id"]], user=_User())
    assert result["status"] == "skipped_provider_off"
    assert result["created"] == 0
    assert GlossaryRegistry().get(term["id"])["translations"] == []

    with pytest.raises(ValueError, match="не более 10"):
        backfill_glossary_translations("ru", list(range(1, 12)), user=_User())


def test_partial_batch_reports_failure_and_keeps_failed_term_pending(monkeypatch):
    monkeypatch.setattr("app.services.glossary.translations.get_settings", lambda: _settings())
    first = _term(description=None)
    second = GlossaryRegistry().create("PA20", "sap_transaction", "Другой термин", None, "de")
    calls = {"n": 0}

    def translate(texts, target, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("provider failure")
        return ["Translated"]

    monkeypatch.setattr("app.services.glossary.translations.translate_texts_batch", translate)
    result = backfill_glossary_translations(
        "ru", [first["id"], second["id"]], user=_User()
    )

    assert result["created"] == 1
    assert result["failed"] == 1
    assert result["status"] == "partial"
    from app.services.glossary.translations import pending_glossary_term_ids

    # Machine output remains pending for human review; the failed term remains
    # pending as missing, so neither is silently dropped from the next batch.
    assert pending_glossary_term_ids("ru") == [first["id"], second["id"]]


def test_strict_parser_rejects_non_string_items_before_string_coercion(monkeypatch):
    monkeypatch.setattr("app.services.translation.get_settings", lambda: _settings())
    monkeypatch.setattr(
        "app.services.llm_client.LLMClient",
        type("FakeClient", (), {"__init__": lambda self, **kwargs: None, "chat_json": lambda self, *args, **kwargs: ["ok", None]}),
    )
    with pytest.raises(ValueError, match="только строки"):
        translate_texts_batch(["source"], "ru", source_locale="de", strict=True)


def test_strict_parser_rejects_empty_name_and_long_fields(monkeypatch):
    monkeypatch.setattr("app.services.translation.get_settings", lambda: _settings())

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def chat_json(self, *args, **kwargs):
            return ["  ", "description"]

    monkeypatch.setattr("app.services.llm_client.LLMClient", FakeClient)
    with pytest.raises(ValueError, match="пустое"):
        translate_texts_batch(["source", "description"], "ru", source_locale="de", strict=True)


def test_translation_model_override_is_used_only_by_translation_client(monkeypatch):
    settings = _settings(translation_model="vendor/glossary-model", llm_model="vendor/default-model")
    monkeypatch.setattr("app.services.translation.get_settings", lambda: settings)
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def chat_json(self, *args, **kwargs):
            return ["translated"]

    monkeypatch.setattr("app.services.llm_client.LLMClient", FakeClient)
    assert translate_texts_batch(["source"], "ru", source_locale="de") == ["translated"]
    assert captured == {"interactive": False, "model": "vendor/glossary-model"}



def test_generic_backfill_aggregates_locked_glossary_terms_across_batches(monkeypatch):
    from app.auth.models import User
    from app.services.translation import backfill_reference_data
    from app.services.reference_resources import ReferenceKey
    from app.services.reference_edit_lock import acquire_edit_lock
    monkeypatch.setattr("app.services.glossary.translations.get_settings", lambda: _settings())
    monkeypatch.setattr("app.services.translation.get_settings", lambda: _settings())
    actor = User(user_id="aggregate-test", username="Aggregate test", roles=["admin"])
    terms = [GlossaryRegistry().create(f"TX{index:02d}", "sap_transaction", f"Term {index}", None, "de") for index in range(12)]
    for index in (0, 10):
        with session_scope() as session:
            acquire_edit_lock(session, ReferenceKey("glossary_term", terms[index]["id"]), actor, f"{index:064x}")
    translated = []
    def translate(texts, *_args, **_kwargs):
        translated.extend(texts)
        return ["Translated " + text for text in texts]
    monkeypatch.setattr("app.services.glossary.translations.translate_texts_batch", translate)
    result = backfill_reference_data("ru", ["glossary"], user=actor, glossary_term_ids=[term["id"] for term in terms])["glossary"]
    assert result["skipped_locked"] == 2
    assert result["requested"] == 12
    assert result["created"] == 10
    assert result["status"] == "partial"
    assert "Term 0" not in translated and "Term 10" not in translated
    for index in (0, 10):
        assert GlossaryRegistry().get(terms[index]["id"])["translations"] == []
