import pytest

from app.services.glossary.registry import (
    GlossaryIdentityConflictError,
    GlossaryRegistry,
)
from app.services.glossary.expansion import prepare_query
from app.services.glossary.matching import matched_domain_terms
from app.services.glossary.types import GlossaryAliasInput
from app.services.glossary.normalization import GlossaryValidationError, GlossaryRedundantAliasError
from app.services.glossary.rule_registry import GlossaryRuleRegistry


def alias(text, locale="ru"):
    return GlossaryAliasInput(text, locale=locale, auto_expand=True, search_enabled=True)


@pytest.mark.parametrize("kind,code", [
    ("sap_transaction", "PA20"), ("sap_table", "T001"),
    ("sap_program", "ZREPORT"), ("sap_object", "ZOBJECT"),
    ("abbreviation", "ABC"), ("business_term", "INTERNAL_KEY"),
])
def test_non_infotype_codes_only_trigger_through_explicit_aliases(kind, code):
    registry = GlossaryRegistry()
    term = registry.create(code, kind, "Понятное имя", aliases=[alias("пособие")])
    assert len(term["aliases"]) == 1
    for query in [code, "/n" + code]:
        assert prepare_query(query, ui_locale="ru", enabled=True).status == "no_match"
    plan = prepare_query("пособие", ui_locale="ru", enabled=True)
    assert plan.added_sparse_texts == ("Понятное имя",)
    assert plan.applied_terms[0].matched_texts == ("пособие",)
    assert code not in plan.dense_query
    assert not matched_domain_terms({"content": code}, plan.match_groups)
    assert matched_domain_terms({"content": "пособие"}, plan.match_groups)


def test_infotype_code_uses_the_system_rule_without_an_explicit_alias():
    registry = GlossaryRegistry()
    term = registry.create("IT0003", "sap_infotype", "Понятное имя", aliases=[alias("пособие")])
    GlossaryRuleRegistry().create(name="PA", number_from=0, number_to=999, prefixes=("IT", "ИТ", "ИТ "))

    plan = prepare_query("ИТ 0003", ui_locale="ru", enabled=True)

    assert len(plan.applied_terms) == 1
    assert plan.applied_terms[0].term_id == term["id"]
    assert plan.applied_terms[0].system_rule == "sap_infotype"


def test_auto_identifier_has_no_alias_and_is_not_admin_searchable():
    registry = GlossaryRegistry()
    first = registry.create(None, "sap_infotype", "Имя", infotype_number="0003")
    with pytest.raises(GlossaryIdentityConflictError):
        registry.create(None, "sap_infotype", "Имя", infotype_number="0004")
    assert first["aliases"] == []
    assert registry.list_page(q=first["canonical"])["total"] == 0


def test_auto_identifier_still_validates_term_kind():
    with pytest.raises(GlossaryValidationError):
        GlossaryRegistry().create(None, "invalid", "Имя")


def test_cross_term_duplicates_are_rejected_before_commit():
    registry = GlossaryRegistry()
    first = registry.create(None, "business_term", "Первый", aliases=[alias("Общая форма")])
    with pytest.raises(GlossaryIdentityConflictError):
        registry.create(None, "sap_table", "Second", aliases=[alias(" общая  ФОРМА ", "en")])
    assert len(registry.list()) == 1
    assert registry.get(first["id"])["has_duplicates"] is False


def test_update_duplicate_alias_is_rejected_and_keeps_old_form():
    registry = GlossaryRegistry()
    registry.create(None, "business_term", "One", aliases=[alias("shared"), alias("unique")])
    second = registry.create(None, "business_term", "Two", aliases=[alias("second")])
    assert prepare_query("shared", ui_locale="en", enabled=True).applied_terms
    with pytest.raises(GlossaryIdentityConflictError):
        registry.update_alias(second["id"], second["version"], second["aliases"][0]["id"], alias="SHARED")
    plan = prepare_query("unique", ui_locale="en", enabled=True)
    assert plan.applied_terms[0].matched_texts == ("unique",)
    assert "unique" not in plan.added_sparse_texts
    assert prepare_query("shared", ui_locale="en", enabled=True).applied_terms
    assert registry.get(second["id"])["aliases"][0]["normalized_alias"] == "second"


def test_rule_repeat_rejected_and_legacy_code_alias_can_be_replaced():
    registry = GlossaryRegistry()
    term = registry.create("IT0003", "sap_infotype", "One", aliases=[alias("IT0003")])
    GlossaryRuleRegistry().create(name="PA", number_from=0, number_to=999, prefixes=("IT", "ИТ"))
    with pytest.raises(GlossaryRedundantAliasError):
        registry.add_alias(term["id"], term["version"], "it0003")
    with pytest.raises(GlossaryRedundantAliasError):
        registry.update_alias(term["id"], term["version"], term["aliases"][0]["id"], alias="ИТ0003")
    changed = registry.update_alias(term["id"], term["version"], term["aliases"][0]["id"], alias="Personnel status")
    assert prepare_query("IT0003", ui_locale="ru", enabled=True).applied_terms
    assert prepare_query("ИТ0003", ui_locale="ru", enabled=True).applied_terms
    deleted = registry.delete_alias(term["id"], changed["version"], term["aliases"][0]["id"])
    assert deleted["aliases"] == []
    assert prepare_query("IT0003", ui_locale="ru", enabled=True).applied_terms
