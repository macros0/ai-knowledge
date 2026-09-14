from app.services.glossary.merge import build_merge_result, preview_digest
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.types import GlossaryAliasInput
from app.db.models import DomainTermTranslation
from app.db.session import session_scope


def test_merge_deduplicates_only_equivalent_forms():
    source = {
        "id": 2,
        "kind": "business_term",
        "original_name": "Учёт времени",
        "original_description": None,
        "canonical_locale": "ru",
        "aliases": [],
        "translations": [],
        "infotype_number": None,
    }
    target = {**source, "id": 1, "original_name": "Табель"}
    result = build_merge_result(source, target, {"original_name": "target"}, ())
    assert result["original_name"] == "Табель"
    assert "Учёт времени" in [a["alias"] for a in result["aliases"]]
    assert all(a["alias"] != "Табель" for a in result["aliases"])


def test_preview_digest_is_stable_for_json_order():
    assert preview_digest({"b": 2, "a": 1}) == preview_digest({"a": 1, "b": 2})


def test_registry_merge_moves_aliases_and_is_idempotent():
    registry = GlossaryRegistry()
    source = registry.create(None, "business_term", "Учёт времени", canonical_locale="ru", aliases=[])
    target = registry.create(None, "business_term", "Табель", canonical_locale="ru", aliases=[])
    selections = {"original_name": "target"}
    request = {"request_id": "00000000-0000-0000-0000-000000000001", "source_id": source["id"], "target_id": target["id"], "selections": selections}
    digest = registry.merge_preview(request)['digest']
    merged = registry.merge(request, digest, actor_id="admin")
    assert merged["id"] == target["id"]
    moved = next(item for item in merged["aliases"] if item["alias"] == "Учёт времени")
    assert moved["locale"] == "ru"
    assert registry.get(source["id"]) is None
    assert registry.merge(request, digest, actor_id="admin")["id"] == target["id"]


def test_registry_merge_persists_source_alias_and_previous_target_name():
    registry = GlossaryRegistry()
    source = registry.create(
        None,
        "business_term",
        "Новое имя",
        canonical_locale="ru",
        aliases=[GlossaryAliasInput("Исходный алиас", locale="ru", auto_expand=True)],
    )
    target = registry.create(None, "business_term", "Старое имя", canonical_locale="ru")
    selections = {"original_name": "source"}
    request = {
        "request_id": "00000000-0000-0000-0000-000000000002",
        "source_id": source["id"],
        "target_id": target["id"],
        "selections": selections,
    }

    merged = registry.merge(request, registry.merge_preview(request)['digest'], actor_id="admin")
    aliases = {item["alias"]: item for item in merged["aliases"]}
    assert "Исходный алиас" in aliases
    assert "Старое имя" in aliases
    assert aliases["Исходный алиас"]["locale"] == "ru"


def test_merge_replay_is_available_after_source_is_deleted():
    registry = GlossaryRegistry()
    source = registry.create(None, "business_term", "Источник")
    target = registry.create(None, "business_term", "Цель")
    selections = {"original_name": "target"}
    request = {
        "request_id": "00000000-0000-0000-0000-000000000003",
        "source_id": source["id"],
        "target_id": target["id"],
        "selections": selections,
    }
    digest = registry.merge_preview(request)['digest']
    first = registry.merge(request, digest, actor_id="admin")
    replay = registry.merge(request, digest, actor_id="admin")
    assert replay["id"] == first["id"]


def test_registry_merge_copies_source_translation_when_target_lacks_locale():
    registry = GlossaryRegistry()
    source = registry.create(None, "business_term", "Источник", canonical_locale="ru")
    target = registry.create(None, "business_term", "Цель", canonical_locale="ru")
    with session_scope() as session:
        session.add(DomainTermTranslation(
            term_id=source["id"], locale="en", display_name="Source", description="Translated",
            source_revision=source["source_revision"], version=1, is_machine_translated=False,
        ))
    request = {
        "request_id": "00000000-0000-0000-0000-000000000004",
        "source_id": source["id"], "target_id": target["id"], "selections": {"original_name": "target"},
    }
    digest = registry.merge_preview(request)['digest']
    merged = registry.merge(request, digest, actor_id="admin")
    assert next(item for item in merged["translations"] if item["locale"] == "en")["display_name"] == "Source"
