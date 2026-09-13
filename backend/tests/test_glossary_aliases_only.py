import pytest

from app.services.glossary.registry import GlossaryAliasConflictError, GlossaryRegistry
from app.services.glossary.expansion import prepare_query
from app.services.glossary.matching import matched_domain_terms
from app.services.glossary.types import GlossaryAliasInput
from app.services.glossary.normalization import GlossaryValidationError


def alias(text, locale="ru"):
    return GlossaryAliasInput(text, locale=locale, auto_expand=True, search_enabled=True)


@pytest.mark.parametrize("kind,code", [
    ("sap_infotype", "IT0003"), ("sap_transaction", "PA20"),
    ("sap_table", "T001"), ("abbreviation", "ABC"), ("business_term", "INTERNAL_KEY"),
])
def test_codes_never_trigger_or_expand_search(kind, code):
    registry = GlossaryRegistry()
    term = registry.create(code, kind, "Понятное имя", aliases=[alias("пособие")])
    assert len(term["aliases"]) == 1
    for query in [code, "/n" + code, "ИТ 0003", "инфотип 3"]:
        assert prepare_query(query, ui_locale="ru", enabled=True).status == "no_match"
    plan = prepare_query("пособие", ui_locale="ru", enabled=True)
    assert plan.added_sparse_texts == ("пособие",)
    assert code not in plan.dense_query
    assert not matched_domain_terms({"content": code}, plan.match_groups)
    assert matched_domain_terms({"content": "пособие"}, plan.match_groups)


def test_auto_identifier_has_no_alias_and_is_not_admin_searchable():
    registry = GlossaryRegistry()
    first = registry.create(None, "sap_infotype", "Имя")
    second = registry.create(None, "sap_infotype", "Имя")
    assert first["canonical"] != second["canonical"]
    assert first["aliases"] == []
    assert registry.list_page(q=first["canonical"])["total"] == 0


def test_auto_identifier_still_validates_term_kind():
    with pytest.raises(GlossaryValidationError):
        GlossaryRegistry().create(None, "invalid", "Имя")


def test_cross_term_duplicates_mark_both_and_clear_after_delete():
    registry = GlossaryRegistry()
    first = registry.create(None, "business_term", "Первый", aliases=[alias("Общая форма")])
    second = registry.create(None, "sap_table", "Second", aliases=[alias(" общая  ФОРМА ", "en")])
    assert second["has_duplicates"] is True
    assert second["alias_conflicts"][0]["term_id"] == first["id"]
    assert registry.get(first["id"])["alias_conflicts"][0]["term_id"] == second["id"]
    assert all(t["has_duplicates"] for t in registry.list_page(limit=1)["terms"])
    plan = prepare_query("общая форма", ui_locale="ru", enabled=True)
    assert plan.applied_terms == ()
    assert any(r.reason == "ambiguous_alias" for r in plan.skipped_reasons)
    registry.delete_alias(second["id"], second["version"], second["aliases"][0]["id"])
    assert registry.get(first["id"])["has_duplicates"] is False
    assert registry.get(second["id"])["has_duplicates"] is False
    assert len(prepare_query("общая форма", ui_locale="ru", enabled=True).applied_terms) == 1


def test_update_duplicate_blocks_only_conflicting_form_and_refreshes_snapshot():
    registry = GlossaryRegistry()
    first = registry.create(None, "business_term", "One", aliases=[alias("shared"), alias("unique")])
    second = registry.create(None, "business_term", "Two", aliases=[alias("second")])
    assert prepare_query("shared", ui_locale="en", enabled=True).applied_terms
    changed = registry.update_alias(second["id"], second["version"], second["aliases"][0]["id"], alias="SHARED")
    assert changed["has_duplicates"]
    plan = prepare_query("unique", ui_locale="en", enabled=True)
    assert plan.added_sparse_texts == ("unique",)
    assert not matched_domain_terms({"content": "shared"}, plan.match_groups)
    assert not prepare_query("shared", ui_locale="en", enabled=True).applied_terms
    registry.update(second["id"], changed["version"], enabled=False)
    assert registry.get(first["id"])["has_duplicates"]
    assert not prepare_query("shared", ui_locale="en", enabled=True).applied_terms


def test_same_card_repeat_rejected_and_code_alias_is_editable():
    registry = GlossaryRegistry()
    term = registry.create("IT0003", "sap_infotype", "One", aliases=[alias("IT0003")])
    with pytest.raises(GlossaryAliasConflictError):
        registry.add_alias(term["id"], term["version"], "it0003")
    changed = registry.update_alias(term["id"], term["version"], term["aliases"][0]["id"], alias="ИТ0003")
    assert prepare_query("IT0003", ui_locale="ru", enabled=True).status == "no_match"
    assert prepare_query("ИТ0003", ui_locale="ru", enabled=True).applied_terms
    deleted = registry.delete_alias(term["id"], changed["version"], term["aliases"][0]["id"])
    assert deleted["aliases"] == []
