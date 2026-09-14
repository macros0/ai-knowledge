"""Exact matching remains revision-, boundary- and Unicode-sensitive when reused."""
from dataclasses import replace

import pytest

from app.services.glossary.matching import exact_excerpt, group_form_matches
from app.services.glossary.normalization import literal_matches, normalize_query_with_mapping
from app.services.glossary.types import MatchGroup, ResolvedForm


def group(*forms, kind="sap_infotype"):
    return MatchGroup(
        term_id=3, canonical="infotype:0003", kind=kind, canonical_locale="en",
        original_name="Payroll", term_version=1, source_revision=1,
        spans=(), matched_forms=(), match_type="alias",
        resolved_forms=tuple(ResolvedForm(
            text=form, normalized=form.lower(), identity_key="infotype:0003",
            boundary_mode="identifier", can_search=True,
        ) for form in forms),
    )


def test_reused_text_rechecks_search_rights_forms_and_boundary_mode():
    initial = group("IT0003", "PA30")
    changed = replace(initial, resolved_forms=(replace(initial.resolved_forms[0], can_search=False),), term_version=2)
    new_prefix = replace(initial, resolved_forms=group("ИТ 0003").resolved_forms, term_version=3)
    for _ in range(3):
        assert group_form_matches("IT0003", initial)
        assert not group_form_matches("IT0003", changed)
        assert group_form_matches("ИТ 0003", new_prefix)
        assert not group_form_matches("ИТ 0003", initial)
        assert not group_form_matches("prefix-PA30", initial)
        assert group_form_matches("prefix-PA30", replace(initial, kind="business_term"))


@pytest.mark.parametrize("text", [
    "IT0003", "İT0003", "ИТ\u00a00003", "cafe\u0301", "PA3000", "XPA30",
    "PA30.", "PA30:next", "before:-PA30", "/PA30", "(PA30)", "PA30--next",
    "foo PA30  bar", "[cafe\u0301]", "x café", "IT0003 IT00037",
])
def test_repeated_matching_agrees_with_literal_boundary_reference(text):
    forms = ("IT0003", "ИТ 0003", "café", "PA30")
    item = group(*forms)
    normalized = normalize_query_with_mapping(text)
    expected = any(literal_matches(normalized, form, boundary_mode="identifier") for form in forms)
    assert group_form_matches(text, item) == expected
    assert group_form_matches(text, item) == expected


@pytest.mark.parametrize("prefix", ["neutral " * 80, "\t \n" + "neutre\u0301  " * 70])
def test_excerpt_cache_keeps_source_offsets_for_unicode_and_collapsed_whitespace(prefix):
    item = group("İT0003", "café")
    for target in ("İT0003", "cafe\u0301"):
        text = prefix + target + " end"
        for _ in range(2):
            excerpt = exact_excerpt(text, 100, (item,))
            assert target in excerpt
            assert len(excerpt) <= 100
            assert group_form_matches(excerpt, item)


def test_long_uncached_document_preserves_late_exact_form():
    item = group("PA30")
    text = "unrelated " * 2000 + "PA3000 differs. PA30 applies."
    assert group_form_matches(text, item)
    assert "PA30 applies" in exact_excerpt(text, 100, (item,))


def test_many_occurrences_do_not_truncate_exact_span_semantics():
    from app.services.glossary.matching import _normalized_spans

    text = "PA30 " * 200
    normalized = normalize_query_with_mapping(text)
    expected = literal_matches(normalized, "PA30", boundary_mode="identifier")
    for _ in range(2):
        assert tuple(_normalized_spans(normalized.text, ("PA30",), "identifier")) == expected
