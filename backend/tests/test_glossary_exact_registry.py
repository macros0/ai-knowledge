import pytest
from concurrent.futures import ThreadPoolExecutor

from app.services.glossary.registry import (
    GlossaryIdentityConflictError,
    GlossaryRegistry,
)
from app.services.glossary.rule_registry import (
    GlossaryRuleConflictError,
    GlossaryRuleRegistry,
)
from app.services.glossary.types import GlossaryAliasInput


def test_name_alias_conflict_is_global():
    registry = GlossaryRegistry()
    registry.create(None, "business_term", "табель")
    with pytest.raises(GlossaryIdentityConflictError) as exc:
        registry.create(
            None,
            "business_term",
            "Учёт времени",
            aliases=[GlossaryAliasInput(" ТАБЕЛЬ ", auto_expand=True, search_enabled=True)],
        )
    assert exc.value.conflicts[0]["key_value"] == "табель"
    assert len(registry.list()) == 1


def test_rule_crud_is_query_only_and_rejects_overlapping_prefix_range():
    rules = GlossaryRuleRegistry()
    first = rules.create(
        name="PA",
        number_from=0,
        number_to=999,
        prefixes=("IT", "ИТ"),
        actor_id="admin",
    )
    assert first["version"] == 1
    with pytest.raises(GlossaryRuleConflictError):
        rules.create(name="overlap", number_from=3, number_to=3, prefixes=("it",), actor_id="admin")
    updated = rules.update(first["id"], first["version"], prefixes=("Infotyp",), actor_id="admin")
    assert updated["version"] == 2
    assert updated["prefixes"] == ["infotyp"]
    deleted = rules.delete(updated["id"], updated["version"], actor_id="admin")
    assert deleted["id"] == first["id"]
    assert rules.list() == []


def test_adjacent_rules_with_same_prefixes_have_safe_merge_preview():
    rules = GlossaryRuleRegistry()
    first = rules.create(name="low", number_from=0, number_to=99, prefixes=("IT",), actor_id="admin")
    second = rules.create(name="high", number_from=100, number_to=199, prefixes=("IT",), actor_id="admin")
    preview = rules.merge_preview({"source_rule_id": first["id"], "target_rule_id": second["id"]})
    assert preview["number_from"] == 0
    assert preview["number_to"] == 199
    merged = rules.merge({"source_rule_id": first["id"], "target_rule_id": second["id"]}, preview["digest"], actor_id="admin")
    assert merged["number_from"] == 0
    assert merged["number_to"] == 199


def test_concurrent_same_name_has_one_owner():
    def create_one(_):
        try:
            GlossaryRegistry().create(None, "business_term", "Общее имя")
            return "created"
        except GlossaryIdentityConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = sorted(pool.map(create_one, range(2)))
    assert result == ["conflict", "created"]
