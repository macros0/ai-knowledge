from __future__ import annotations

import pytest

from app.services.glossary.expansion import prepare_query
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.types import GlossaryAliasInput


@pytest.fixture
def glossary_term():
    registry = GlossaryRegistry()
    return registry.create(
        "IT0003",
        "sap_infotype",
        "Payroll infotype",
        canonical_locale="de",
        aliases=(
            *(GlossaryAliasInput(form, auto_expand=True, search_enabled=True)
              for form in ["ИТ 0003", "it-3", "инфотип 0003", "инфотипе 3", "infotype 0003"]),
            GlossaryAliasInput(
                "Payroll-IT",
                locale="en",
                auto_expand=True,
                search_enabled=True,
            ),
        ),
    )


@pytest.mark.parametrize("query", ["ИТ 0003", "it-3", "инфотип 0003", "в инфотипе 3", "infotype 0003"])
def test_explicit_infotype_aliases_are_expanded_deterministically(query, glossary_term):
    plan = prepare_query(query, ui_locale="en", enabled=True)

    assert plan.original_query == query
    assert [term.canonical for term in plan.applied_terms] == ["IT0003"]
    assert plan.applied_terms[0].matched_texts == (query if query != "в инфотипе 3" else "инфотипе 3",)
    assert plan.applied_terms[0].canonical_locale == "de"
    assert plan.applied_terms[0].display_name == "Payroll infotype"
    assert plan.applied_terms[0].display_is_fallback is True
    assert plan.status in {"applied", "limited"}


@pytest.mark.parametrize(
    "query",
    [
        "ТР",
        "PA",
        "0003",
        "IT12345",
        "XIT0003",
        "IТ0003",
        "IT 12.5",
        "IT 12,5",
        "ИТ 0001–0003",
        "ИТ 1/3",
    ],
)
def test_does_not_invent_expansion(query, glossary_term):
    plan = prepare_query(query, ui_locale="ru", enabled=True)

    assert plan.applied_terms == ()
    assert plan.match_groups == ()
    assert plan.dense_query == query
    assert plan.status == "no_match"


def test_alias_matching_ignores_alias_locale_and_respects_identifier_boundaries():
    registry = GlossaryRegistry()
    registry.create(
        "PA20",
        "sap_transaction",
        "Change user master record",
        aliases=(
            GlossaryAliasInput(
                "user transaction",
                locale="de",
                auto_expand=True,
                search_enabled=True,
            ),
        ),
    )

    plan = prepare_query("XPA20Y /nPA20 user transaction", ui_locale="ru", enabled=True)

    assert [term.canonical for term in plan.applied_terms] == ["PA20"]
    assert plan.applied_terms[0].matched_texts == ("user transaction",)
    assert plan.applied_terms[0].match_type == "alias"


def test_alias_matching_uses_nfc_but_reports_original_offsets():
    registry = GlossaryRegistry()
    registry.create(
        "CAFE1",
        "business_term",
        "Cafe",
        aliases=(GlossaryAliasInput("café", auto_expand=True, search_enabled=True),),
    )

    query = "  Cafe\u0301  "
    plan = prepare_query(query, ui_locale="en", enabled=True)

    assert [term.canonical for term in plan.applied_terms] == ["CAFE1"]
    assert plan.applied_terms[0].matched_texts == ("Cafe\u0301",)
    assert plan.match_groups[0].spans[0].start == 2
    assert plan.match_groups[0].spans[0].end == len(query) - 2


def test_repeated_matches_are_deduplicated_and_original_name_is_not_recursive_trigger():
    registry = GlossaryRegistry()
    registry.create(
        "PA20",
        "sap_transaction",
        "Change user master record",
        aliases=(
            GlossaryAliasInput(
                "change user",
                auto_expand=True,
                search_enabled=True,
            ),
        ),
    )
    registry.create(
        "IT0003",
        "sap_infotype",
        "Payroll infotype",
        aliases=(
            GlossaryAliasInput(
                "payroll",
                auto_expand=True,
                search_enabled=True,
            ),
        ),
    )

    plan = prepare_query("PA20 PA20 Change user record", ui_locale="en", enabled=True)

    assert [term.canonical for term in plan.applied_terms] == ["PA20"]
    assert plan.applied_terms[0].matched_texts == ("Change user",)
    assert all("Payroll infotype" not in text for text in plan.added_sparse_texts)


def test_terms_are_ordered_by_first_match_and_expansion_forms_are_stable():
    registry = GlossaryRegistry()
    registry.create(
        "PA20",
        "sap_transaction",
        "Transaction",
        aliases=(
            GlossaryAliasInput("user transaction", auto_expand=True, search_enabled=True),
            GlossaryAliasInput("PA old", auto_expand=True, search_enabled=True),
        ),
    )
    registry.create(
        "IT0003",
        "sap_infotype",
        "Infotype",
        aliases=(GlossaryAliasInput("payroll", auto_expand=True, search_enabled=True),),
    )

    plan = prepare_query("payroll and user transaction", ui_locale="en", enabled=True)

    assert [term.canonical for term in plan.applied_terms] == ["IT0003", "PA20"]
    assert plan.added_sparse_texts == ("payroll", "PA old", "user transaction")


def test_disabled_path_does_not_read_glossary(monkeypatch):
    def fail_if_called():
        raise AssertionError("disabled query must not access the glossary")

    monkeypatch.setattr("app.services.glossary.expansion.load_glossary_snapshot", fail_if_called)

    plan = prepare_query("ИТ 0003", ui_locale="ru", enabled=False)

    assert plan.status == "disabled"
    assert plan.original_query == "ИТ 0003"
    assert plan.dense_query == "ИТ 0003"
    assert plan.applied_terms == ()


def test_snapshot_failure_keeps_original_query_and_reports_unavailable(monkeypatch):
    def fail_snapshot():
        raise RuntimeError("database is unavailable")

    monkeypatch.setattr("app.services.glossary.expansion.load_glossary_snapshot", fail_snapshot)

    plan = prepare_query("ИТ 0003", ui_locale="ru", enabled=True)

    assert plan.status == "unavailable"
    assert plan.dense_query == "ИТ 0003"
    assert plan.added_sparse_texts == ()


def test_limit_marks_plan_limited_and_keeps_whole_additions():
    registry = GlossaryRegistry()
    for index in range(1, 4):
        registry.create(
            f"PA{index:02d}",
            "sap_transaction",
            f"Transaction {index}",
            aliases=(GlossaryAliasInput(f"txn-{index}", auto_expand=True, search_enabled=True),),
        )

    class Settings:
        glossary_max_terms_per_query = 2
        glossary_max_added_aliases_per_term = 1
        glossary_max_added_tokens = 32
        glossary_max_added_chars = 768
        glossary_query_text_max_chars = 8192

    plan = prepare_query("txn-1 txn-2 txn-3", ui_locale="en", enabled=True, settings=Settings())

    assert plan.status == "limited"
    assert [term.canonical for term in plan.applied_terms] == ["PA01", "PA02"]
    assert all("PA03" not in text for text in plan.added_sparse_texts)
    assert not plan.dense_query.endswith("PA03")
    assert any(
        item.canonical == "PA03" and item.reason == "max_terms_per_query"
        for item in plan.skipped_reasons
    )


def test_old_plan_is_immutable_when_term_is_disabled():
    registry = GlossaryRegistry()
    created = registry.create("PA20", "sap_transaction", "Transaction", aliases=[GlossaryAliasInput("PA20", auto_expand=True, search_enabled=True)])

    first = prepare_query("PA20", ui_locale="en", enabled=True)
    registry.update(created["id"], created["version"], enabled=False)
    second = prepare_query("PA20", ui_locale="en", enabled=True)

    assert [term.canonical for term in first.applied_terms] == ["PA20"]
    assert second.applied_terms == ()
    assert first.status == "applied"
