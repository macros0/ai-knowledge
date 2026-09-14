from __future__ import annotations

from app.services.context_builder import (
    drop_partial_title_matches,
    drop_unmatched_blocks,
    format_context,
)
from app.services.glossary.expansion import prepare_query
from app.services.glossary.matching import (
    matched_domain_terms,
    promote_glossary_identifier_hits,
)
from app.services.glossary import matching
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.types import GlossaryAliasInput, MatchGroup, MatchSpan
from app.services.fusion import Hit


def _block(title: str, content: str, *, concept_content: str | None = None) -> dict:
    return {
        "title": title,
        "content": content,
        "concept_content": concept_content,
        "tags": [],
        "source_filename": "source.docx",
        "point_type": "concept",
        "kind": "concept",
        "chunk_index": None,
    }


def _infotype_plan(query: str = "инфотип 3"):
    GlossaryRegistry().create(
        "IT0003",
        "sap_infotype",
        "Payroll infotype",
        canonical_locale="de",
        aliases=[GlossaryAliasInput(text, auto_expand=True, search_enabled=True) for text in ["инфотип 3", "IT0003"]],
    )
    return prepare_query(query, ui_locale="ru", enabled=True)


def test_domain_match_keeps_source_with_explicit_equivalent_alias():
    plan = _infotype_plan()
    source = _block("Настройка IT0003", "Параметры IT0003 задаются в системе.")
    unrelated = _block("Другой раздел", "Описание инфотипа без конкретного кода.")

    kept = drop_unmatched_blocks(
        [source, unrelated],
        plan.original_query,
        match_groups=plan.match_groups,
    )

    assert kept == [source, unrelated]
    assert matched_domain_terms(source, plan.match_groups) == ["Payroll infotype via инфотип 3"]
    assert matched_domain_terms(unrelated, plan.match_groups) == []


def test_bare_domain_word_does_not_count_as_domain_match():
    plan = _infotype_plan()
    unrelated = _block("Другой раздел", "Описание инфотипа без конкретного кода.")

    assert matched_domain_terms(unrelated, plan.match_groups) == []


def test_complete_form_matching_does_not_build_source_offset_mapping(monkeypatch):
    def fail_mapping(_text):
        raise AssertionError("content matching must not build source offsets")

    monkeypatch.setattr(matching, "normalize_query_with_mapping", fail_mapping)

    assert matching._contains_complete_form("Настройка IT0003", ("IT0003",))


def test_exact_identifier_priority_is_generic_and_boundary_safe():
    group = MatchGroup(
        term_id=20,
        canonical="PA20",
        kind="sap_transaction",
        canonical_locale="en",
        original_name="Payroll Status",
        term_version=1,
        source_revision=1,
        spans=(MatchSpan(0, 4, "PA20", "alias", "PA20"),),
        matched_forms=("/nPA20",),
        match_type="alias",
    )
    near = Hit("near", 1.0, {"title": "X/nPA20Y", "content": ""})
    exact = Hit("exact", 0.01, {"title": "Run /nPA20", "content": ""})

    promoted = promote_glossary_identifier_hits([near, exact], (group,))

    assert [hit.point_id for hit in promoted] == ["exact", "near"]


def test_transaction_identifier_priority_does_not_use_a_text_alias():
    group = MatchGroup(
        term_id=20,
        canonical="PA30",
        kind="sap_transaction",
        canonical_locale="en",
        original_name="Payroll transaction",
        term_version=1,
        source_revision=1,
        spans=(MatchSpan(0, 4, "PA30", "alias", "PA30"),),
        matched_forms=("PA30", "AHK payroll"),
        match_type="alias",
    )
    first = Hit("first", 1.0, {"title": "Other", "content": ""})
    text_alias = Hit("text", 0.01, {"title": "AHK payroll", "content": ""})
    hits = [first, text_alias]

    promoted = promote_glossary_identifier_hits(hits, (group,))

    assert promoted is hits


def test_text_only_glossary_form_does_not_change_ranking():
    group = MatchGroup(
        term_id=21,
        canonical="PAYROLL_STATUS",
        kind="business_term",
        canonical_locale="en",
        original_name="Payroll Status",
        term_version=1,
        source_revision=1,
        spans=(MatchSpan(0, 14, "Payroll Status", "alias", "Payroll Status"),),
        matched_forms=("Payroll Status",),
        match_type="alias",
    )
    first = Hit("first", 1.0, {"title": "Other", "content": ""})
    second = Hit("second", 0.01, {"title": "Payroll Status", "content": ""})
    hits = [first, second]

    assert promote_glossary_identifier_hits(hits, (group,)) is hits


def test_domain_match_can_reuse_request_local_cache():
    plan = _infotype_plan()
    source = _block("Настройка IT0003", "Параметры IT0003 задаются в системе.")
    cache = {}

    first = matched_domain_terms(source, plan.match_groups, cache=cache)
    cached = matched_domain_terms(source, plan.match_groups, cache=cache)

    assert cached == first == ["Payroll infotype via инфотип 3"]
    assert len(cache) == 1


def test_exact_domain_title_uses_own_concept_content_but_numeric_title_does_not():
    plan = _infotype_plan()
    about = _block(
        "Настройка IT0003",
        "сырой чанк с соседним разделом",
        concept_content="Собственная выжимка IT0003.",
    )
    numeric_only = _block(
        "Настройка 0003",
        "сырой текст числового раздела",
        concept_content="Выжимка числового раздела.",
    )

    kept = drop_partial_title_matches(
        [about, numeric_only],
        plan.original_query,
        match_groups=plan.match_groups,
    )

    assert [item["title"] for item in kept] == ["Настройка IT0003"]
    assert kept[0]["content"] == "Собственная выжимка IT0003."

    numeric_kept = drop_partial_title_matches(
        [numeric_only],
        plan.original_query,
        match_groups=plan.match_groups,
    )
    assert numeric_kept == [numeric_only]
    assert numeric_kept[0]["content"] == "сырой текст числового раздела"


def test_two_domain_groups_keep_both_sides_and_do_not_require_added_name_in_title():
    registry = GlossaryRegistry()
    registry.create("IT0001", "sap_infotype", "Employee grouping", canonical_locale="de", aliases=[GlossaryAliasInput("IT0001", auto_expand=True, search_enabled=True)])
    registry.create("IT0003", "sap_infotype", "Payroll infotype", canonical_locale="de", aliases=[GlossaryAliasInput("IT0003", auto_expand=True, search_enabled=True)])
    plan = prepare_query("IT0001 и IT0003", ui_locale="ru", enabled=True)
    first = _block("Описание IT0001", "Факты про IT0001")
    second = _block("Описание IT0003", "Факты про IT0003")

    kept = drop_unmatched_blocks(
        [first, second],
        plan.original_query,
        match_groups=plan.match_groups,
    )
    exact_filtered = drop_partial_title_matches(
        kept,
        plan.original_query,
        match_groups=plan.match_groups,
    )
    context = format_context(
        exact_filtered,
        query=plan.original_query,
        match_groups=plan.match_groups,
    )

    assert exact_filtered == [first, second]
    assert "Matched domain terms: [Employee grouping via IT0001]" in context
    assert "Matched domain terms: [Payroll infotype via IT0003]" in context
