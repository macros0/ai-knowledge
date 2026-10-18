import pytest

from app.db.session import session_scope
from app.services.tag_registry import TagRegistry


def make_tag():
    registry = TagRegistry()
    registry.add(["concurrent-translation"], canonical_locale="ru")
    return registry, registry.all()[0]


def test_stale_tag_translation_is_rejected():
    from app.services.reference_concurrency import ReferenceError
    registry, tag = make_tag()
    revision = tag["reference_revision"]
    registry.set_translation(tag["id"], "en", "Alice", reviewed_by="alice", expected_reference_revision=revision)
    with pytest.raises(ReferenceError) as exc:
        registry.set_translation(tag["id"], "en", "Bob stale", reviewed_by="bob", expected_reference_revision=revision)
    assert exc.value.code == "version_conflict"
    assert registry.all()[0]["translations"][0]["text"] == "Alice"


def test_human_edit_during_llm_is_preserved(monkeypatch):
    from app.services import translation
    registry, tag = make_tag()
    def delayed(*args, **kwargs):
        registry.set_translation(tag["id"], "en", "Human during LLM", reviewed_by="alice")
        return ["Machine after human"]
    monkeypatch.setattr(translation, "translate_texts_batch", delayed)
    counters = {}
    translation._backfill_tags("en", None, "admin", counters=counters)
    result = registry.all()[0]["translations"][0]
    assert result["text"] == "Human during LLM"
    assert result["reviewed_by"] == "alice"
    assert result["is_machine_translated"] is False
    assert counters["skipped_reviewed"] == 1


@pytest.mark.parametrize("mutation", ["source", "delete", "review"])
def test_changed_source_deleted_row_and_review_are_rechecked(monkeypatch, mutation):
    from app.services import translation
    from app.db.models import Tag
    registry, tag = make_tag()
    if mutation == "review":
        registry.set_translation(tag["id"], "en", "Existing machine", is_machine=True)
    def delayed(*args, **kwargs):
        if mutation == "source":
            with session_scope() as s:
                s.get(Tag, tag["id"]).canonical_locale = "de"
        elif mutation == "delete":
            registry.delete(tag["name"])
        else:
            registry.bulk_review([tag["id"]], "reviewer")
        return ["Late machine"]
    monkeypatch.setattr(translation, "translate_texts_batch", delayed)
    counters = {}
    translation._backfill_tags("en", None, "admin", counters=counters)
    if mutation != "delete":
        rows = registry.all()[0]["translations"]
        assert not rows or rows[0]["text"] == "Existing machine"
    assert counters[{"source": "skipped_changed", "delete": "skipped_deleted", "review": "skipped_reviewed"}[mutation]] == 1


def test_locked_candidate_skips_model_call(monkeypatch):
    from app.services import translation
    from app.services.reference_resources import ReferenceKey
    from app.services.reference_edit_lock import acquire_edit_lock
    from app.auth.models import User
    registry, tag = make_tag()
    with session_scope() as s:
        acquire_edit_lock(s, ReferenceKey("tag", str(tag["id"])), User(user_id="a"), "a" * 64)
    def forbidden(*args, **kwargs):
        pytest.fail("model must not be called for an occupied editor")
    monkeypatch.setattr(translation, "translate_texts_batch", forbidden)
    counters = {}
    translation._backfill_tags("en", None, "admin", counters=counters)
    assert counters["skipped_locked"] == 1


@pytest.mark.parametrize("entity", ["tags", "developments", "attributes"])
def test_manual_unreviewed_translation_is_never_a_machine_candidate(monkeypatch, entity):
    from app.services import translation
    from app.services.reference_translation import CONFIG, capture_translation_candidate
    from tests.test_reference_origin import create
    identifier = create(entity, "Original manual source", "ru")
    _, model, fk, _, field, _ = CONFIG[entity]
    with session_scope() as session:
        session.add(model(**{fk: identifier, "locale": "en", field: "Manual without review",
                            "is_machine_translated": False, "reviewed_by": None}))
    candidate, skipped = capture_translation_candidate(entity, identifier, "en")
    assert candidate is None
    assert skipped == "skipped_reviewed"
    assert translation.count_pending("en", [entity])[entity] == 0
    def forbidden(*_args, **_kwargs):
        pytest.fail("manual translation must be preserved without calling the model")
    monkeypatch.setattr(translation, "translate_texts_batch", forbidden)
    translation._backfill_reference(entity, "en", None, "admin")
