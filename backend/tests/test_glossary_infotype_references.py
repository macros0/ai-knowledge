"""Infotype references in canonical source text retain complete code identity."""
import pytest
from time import perf_counter

from app.services.glossary.matching import exact_excerpt, group_form_matches
from app.services.glossary.types import MatchGroup, MatchSpan


def _group(number):
    return MatchGroup(
        term_id=None, canonical=f"IT{number}", kind="sap_infotype", canonical_locale="und",
        original_name=f"IT{number}", term_version=0, source_revision=0,
        spans=(MatchSpan(0, 6, f"IT{number}", "structural", f"IT{number}"),),
        matched_forms=(f"IT{number}", f"ИТ {number}"), match_type="structural",
        system_rule="sap_infotype", structural_code=f"IT{number}",
    )


@pytest.mark.parametrize("text,number", [
    ("Вместо ms_it3330 используется другой объект.", "3330"),
    ("Методы GET_DATA_IT3330 и GET_DATA_IT3273.", "3273"),
    ("Есть все данные для заполнения ИТ 3330 и 3273", "3273"),
    ("Заполнение ИТ 3330, 3273 и 0003", "3273"),
    ("IT3330 and 3273", "3273"),
    ("ИТ 3330 и 3273 и 12345", "3273"),
])
def test_context_recognizes_qualified_fields_and_coordinated_codes(text, number):
    assert group_form_matches(text, _group(number))


@pytest.mark.parametrize("text", [
    "ms_it32737", "ms_it3273_extra", "XIT3273Y", "Другие данные 3273",
    "ИТ 3330-3273", "ИТ 3330. Другая таблица 3273", "ИТ 3330\n3273",
    "ИТ 3330 и 32737", "ИТ 3330 и 3273_extra", "PA3330 и 3273",
    "инфо-типа 3330 и 3273", "IT3330 / 3273", "ms_it3273/subkey",
])
def test_context_does_not_infer_partial_codes_or_unconfigured_prefixes(text):
    assert not group_form_matches(text, _group("3273"))


def test_excerpt_keeps_the_context_needed_to_recognize_the_reference():
    group = _group("3273")
    text = "Вводные сведения. " * 100 + "Заполнение ИТ 3330 и 3273. " + "Продолжение. " * 100
    excerpt = exact_excerpt(text, 200, (group,))
    assert len(excerpt) <= 200
    assert "ИТ 3330 и 3273" in excerpt
    assert group_form_matches(excerpt, group)


def test_long_token_without_infotype_does_not_trigger_quadratic_field_scanning():
    group = _group("3273")
    text = "a" * 30000
    started = perf_counter()
    assert not group_form_matches(text, group)
    # A broad budget for a linear 30K scan; the unanchored field expression
    # retries from every character and takes several seconds on this input.
    assert perf_counter() - started < 0.5


@pytest.mark.parametrize("budget,count", [(200, 40), (4000, 700), (6000, 1050)])
def test_excerpt_shows_late_code_when_its_shared_prefix_cannot_fit(budget, count):
    text = "Вводные сведения. " * 10 + "ИТ 3330, " + ", ".join(
        f"{n:04}" for n in range(1000, 1000 + count)
    ) + " и 3273."
    excerpt = exact_excerpt(text, budget, (_group("3273"),))
    assert len(excerpt) <= budget
    assert excerpt in text  # Evidence remains a contiguous canonical excerpt.
    assert "3273" in excerpt


def _long_reference_hit():
    from app.services.fusion import Hit

    return Hit("late", 1.0, {
        "doc_id": "late-doc", "slug": "late", "point_type": "concept",
        "chunk_index": 0, "title": "Заполнение записей", "tags": [],
        "source_id": "root", "generation_id": "generation",
        "_canonical_verified": True,
        "content": "Вводные сведения. " * 10 + "ИТ 3330, " + ", ".join(
            f"{n:04}" for n in range(1000, 1040)
        ) + " и 3273.",
    })


def test_long_reference_survives_merge_postfilters_and_snapshot():
    from app.config import Settings
    from app.services.context_builder import merge_and_format, drop_unmatched_blocks, drop_partial_title_matches
    from app.services.glossary.matching import matched_domain_terms
    from app.services.chat_source_selection import snapshot_blocks
    from app.services.fusion import Hit

    groups = (_group("3273"),)
    late = _long_reference_hit()
    direct = Hit("direct", 2.0, {
        **late.payload, "doc_id": "direct-doc", "slug": "direct",
        "title": "IT3273", "content": "IT3273: запись.",
    })
    blocks = merge_and_format([direct, late], Settings(chat_concept_max_chars=200),
                              exact_groups=groups, limit_total_chars=False)
    blocks = drop_unmatched_blocks(blocks, "IT3273", match_groups=groups)
    blocks = drop_partial_title_matches(blocks, "IT3273", match_groups=groups)
    assert [block["doc_id"] for block in blocks] == ["direct-doc", "late-doc"]
    assert "3273" in blocks[1]["content"]
    assert blocks[1]["_evidence"]["length"] <= 200
    assert matched_domain_terms(blocks[1], groups) == ["IT3273 via IT3273"]
    assert matched_domain_terms(snapshot_blocks(blocks)[1], groups) == ["IT3273 via IT3273"]


def test_preserved_match_cannot_grant_a_removed_search_form():
    from dataclasses import replace
    from app.config import Settings
    from app.services.context_builder import merge_and_format
    from app.services.glossary.matching import matched_domain_terms

    group = _group("3273")
    blocks = merge_and_format([_long_reference_hit()], Settings(chat_concept_max_chars=200),
                              exact_groups=(group,), limit_total_chars=False)
    assert len(blocks) == 1
    changed = replace(group, matched_forms=("IT3273",), term_version=1)
    assert matched_domain_terms(blocks[0], (changed,)) == []
    assert matched_domain_terms(blocks[0], ()) == []
